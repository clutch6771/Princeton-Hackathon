"""Ingest service: validate, privacy-screen, admit, segment, acknowledge.

An acknowledgment means the event was admitted to ephemeral, in-memory
processing. It is not durable storage of the media: after a restart the
media is gone and affected reels are marked ``lost_on_restart``.

Volatile state is only replaced after the database transaction commits, so
storage and memory cannot disagree about which exposures exist.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from judge.audio.codec import AacDecoder, trim_decoded
from judge.audio.pcm import AudioNote, AudioNoteKind, PcmSegment, ms_to_samples
from judge.config import Settings
from judge.contracts.capture import (
    AudioCaptureState,
    AudioChunk,
    CaptureEvent,
    FrameObservation,
    TimeInterval,
)
from judge.contracts.ingest import AckStatus, IngestResponse
from judge.contracts.record import PendingReel
from judge.contracts.session import (
    IngestKind,
    IngestReceipt,
    SessionRecord,
    SessionStatus,
)
from judge.errors import (
    AudioDecodeError,
    NotFoundError,
    OverloadedError,
    SessionEndedError,
    SourceConflictError,
    ValidationFailure,
)
from judge.ingest.admission import InspectedFrame, check_audio_payload, inspect_frame
from judge.ingest.session_state import (
    ClosedCandidate,
    FrameResult,
    SessionState,
    StateConfig,
    apply_audio,
    apply_feed_exit,
    apply_frame,
    new_session_state,
    with_order_seqs,
)
from judge.ingest.volatile import VolatileSessions
from judge.persistence.interfaces import Storage, StorageTx
from judge.pipeline.finalizer import CandidateFinalizer
from judge.privacy.eligibility import BackendEligibilityGate, GateDecision
from judge.runtime.clock import Clock
from judge.runtime.media_budget import MediaBudget
from judge.segmentation.frame_selector import KeptFrame
from judge.segmentation.segmenter import FrameSample, ReelSegmenter

_MS = 1000
_RETRY_AFTER_SECONDS = 2.0


@dataclass(slots=True)
class _Reservation:
    """Bytes of media budget reserved by one in-flight request."""

    nbytes: int = 0


@dataclass(frozen=True, slots=True)
class DecodedChunk:
    """Permitted PCM of one chunk plus notes about what was not kept."""

    segments: tuple[PcmSegment, ...]
    notes: tuple[AudioNote, ...]
    status: AckStatus


async def store_pending(
    tx: StorageTx,
    state: SessionState,
    closed: tuple[ClosedCandidate, ...],
    now: datetime,
) -> SessionState:
    """Record newly closed exposures as pending reels, in capture order."""
    order: dict[UUID, int] = {}
    for candidate in closed:
        order_seq = await tx.sessions.allocate_order_seq(state.session_id)
        order[candidate.reel_id] = order_seq
        await tx.reels.insert_pending(
            PendingReel(
                reel_id=candidate.reel_id,
                owner_id=state.owner_id,
                session_id=state.session_id,
                source_id=state.source_id,
                order_seq=order_seq,
                idempotency_key=str(candidate.first_event_id),
                start_ms=candidate.start_ms,
                end_ms=candidate.end_ms,
                boundary_uncertainty_ms=candidate.boundary_uncertainty_ms,
                first_second_observed=candidate.first_second_observed,
                segmentation_status=candidate.segmentation_status,
                close_reason=candidate.close_reason,
                created_at=now,
            )
        )
    return with_order_seqs(state, order)


def _uncertified_gaps(
    span: TimeInterval, intervals: tuple[TimeInterval, ...]
) -> tuple[AudioNote, ...]:
    notes: list[AudioNote] = []
    cursor = span.start_ms
    for interval in intervals:
        if interval.start_ms > cursor:
            notes.append(
                AudioNote(AudioNoteKind.UNCERTIFIED, cursor, interval.start_ms)
            )
        cursor = interval.end_ms
    if cursor < span.end_ms:
        notes.append(AudioNote(AudioNoteKind.UNCERTIFIED, cursor, span.end_ms))
    return tuple(notes)


def decode_chunk(
    decoder: AacDecoder,
    meta: AudioChunk,
    payload: bytes,
    intervals: tuple[TimeInterval, ...],
) -> DecodedChunk:
    """Decode one chunk and keep only natively certified samples (CPU-bound).

    Uncertified chunks are not decoded at all. Samples outside the certified
    intervals are dropped as soon as the chunk is decoded.
    """
    span = meta.span
    whole = (span.start_ms, span.end_ms)
    if meta.capture_state is AudioCaptureState.PROTECTED:
        note = AudioNote(AudioNoteKind.PROTECTED, *whole)
        return DecodedChunk((), (note,), AckStatus.ACCEPTED)
    if meta.capture_state is AudioCaptureState.UNAVAILABLE:
        return DecodedChunk((), (), AckStatus.ACCEPTED)
    if not intervals:
        note = AudioNote(AudioNoteKind.UNCERTIFIED, *whole)
        return DecodedChunk((), (note,), AckStatus.PRIVACY_EXCLUDED)
    try:
        pcm = trim_decoded(
            decoder.decode(payload, meta.container),
            priming_samples=meta.priming_samples,
            expected_samples=ms_to_samples(span.duration_ms),
        )
    except AudioDecodeError:
        note = AudioNote(AudioNoteKind.CORRUPT, *whole)
        return DecodedChunk((), (note,), AckStatus.CORRUPT_DISCARDED)
    segments: list[PcmSegment] = []
    for interval in intervals:
        first = ms_to_samples(interval.start_ms - span.start_ms)
        last = ms_to_samples(interval.end_ms - span.start_ms)
        samples = pcm[first:last].copy()
        samples.setflags(write=False)
        if len(samples):
            segments.append(PcmSegment(start_ms=interval.start_ms, samples=samples))
    notes = _uncertified_gaps(span, intervals)
    return DecodedChunk(tuple(segments), notes, AckStatus.ACCEPTED)


class IngestService:
    """Steps 1 to 4 of the workflow for both capture streams."""

    def __init__(
        self,
        *,
        settings: Settings,
        clock: Clock,
        storage: Storage,
        gate: BackendEligibilityGate,
        segmenter: ReelSegmenter,
        decoder: AacDecoder,
        volatile: VolatileSessions,
        budget: MediaBudget,
        finalizer: CandidateFinalizer,
        process_epoch: UUID,
    ) -> None:
        self._settings = settings
        self._config = StateConfig.from_settings(settings)
        self._skew_ms = int(settings.clock_skew_tolerance_seconds * _MS)
        self._clock = clock
        self._storage = storage
        self._gate = gate
        self._segmenter = segmenter
        self._decoder = decoder
        self._volatile = volatile
        self._budget = budget
        self._finalizer = finalizer
        self._epoch = process_epoch

    # --- frames -------------------------------------------------------------

    async def ingest_frame(
        self, owner_id: str, meta: FrameObservation, jpeg: bytes
    ) -> IngestResponse:
        """Admit one sampled frame. Returns promptly; inference is async."""
        screening = self._gate.screen_frame(meta.privacy)
        inspected: InspectedFrame | None = None
        if screening.decision is GateDecision.ADMIT:
            inspected = await asyncio.to_thread(
                inspect_frame, jpeg, meta, self._settings
            )
        async with self._volatile.lock(meta.session_id):
            response = await self._admit_frame(owner_id, meta, jpeg, inspected)
        await self._finalizer.process(meta.session_id)
        return response

    async def _admit_frame(
        self,
        owner_id: str,
        meta: FrameObservation,
        jpeg: bytes,
        inspected: InspectedFrame | None,
    ) -> IngestResponse:
        now = self._clock.now()
        # The reservation is rolled back if anything fails, including the
        # commit itself, so a database fault can never leak media budget.
        with self._reservation(meta.session_id) as reservation:
            async with self._storage.transaction() as tx:
                duplicate = await self._duplicate(tx, owner_id, meta, now)
                if duplicate is not None:
                    return duplicate
                state = await self._admit_session(tx, owner_id, meta, now)
                result = self._apply_frame(state, meta, jpeg, inspected, reservation)
                state = await store_pending(tx, result.state, result.closed, now)
                await self._record(
                    tx, owner_id, meta, IngestKind.FRAME, len(jpeg), result.status, now
                )
        self._volatile.put(state)
        self._budget.release(meta.session_id, sum(f.nbytes for f in result.released))
        return self._response(meta, result.status)

    def _apply_frame(
        self,
        state: SessionState,
        meta: FrameObservation,
        jpeg: bytes,
        inspected: InspectedFrame | None,
        reservation: _Reservation,
    ) -> FrameResult:
        now, monotonic = self._clock.now(), self._clock.monotonic()
        if inspected is None:
            exited, closed = apply_feed_exit(
                state,
                meta.capture_ts_ms,
                config=self._config,
                now=now,
                monotonic=monotonic,
            )
            return FrameResult(exited, AckStatus.PRIVACY_EXCLUDED, closed)
        mapping = state.clock.observe(now, meta.capture_ts_ms, self._skew_ms)
        self._reserve(meta.session_id, len(jpeg), reservation)
        return apply_frame(
            replace(state, clock=mapping),
            FrameSample(
                event_id=meta.event_id,
                ts_ms=meta.capture_ts_ms,
                signature=inspected.signature,
                transition_at_ms=meta.transition_at_ms,
            ),
            KeptFrame(
                event_id=meta.event_id,
                ts_ms=meta.capture_ts_ms,
                jpeg=jpeg,
                width=inspected.width,
                height=inspected.height,
            ),
            config=self._config,
            segmenter=self._segmenter,
            now=now,
            monotonic=monotonic,
        )

    # --- audio --------------------------------------------------------------

    async def ingest_audio(
        self, owner_id: str, meta: AudioChunk, payload: bytes
    ) -> IngestResponse:
        """Admit one app-audio chunk. Returns promptly; inference is async."""
        intervals = self._gate.certified_intervals(meta)
        check_audio_payload(payload, meta, self._settings)
        decoded = await asyncio.to_thread(
            decode_chunk, self._decoder, meta, payload, intervals
        )
        async with self._volatile.lock(meta.session_id):
            response = await self._admit_audio(owner_id, meta, len(payload), decoded)
        await self._finalizer.process(meta.session_id)
        return response

    async def _admit_audio(
        self, owner_id: str, meta: AudioChunk, byte_length: int, decoded: DecodedChunk
    ) -> IngestResponse:
        now = self._clock.now()
        nbytes = sum(segment.nbytes for segment in decoded.segments)
        with self._reservation(meta.session_id) as reservation:
            async with self._storage.transaction() as tx:
                duplicate = await self._duplicate(tx, owner_id, meta, now)
                if duplicate is not None:
                    return duplicate
                state = await self._admit_session(tx, owner_id, meta, now)
                mapping = state.clock.observe(now, meta.capture_end_ms, self._skew_ms)
                self._reserve(meta.session_id, nbytes, reservation)
                result = apply_audio(
                    replace(state, clock=mapping),
                    meta,
                    decoded.segments,
                    decoded.notes,
                    now=now,
                )
                status = result.status
                if status is AckStatus.ACCEPTED:
                    status = decoded.status
                await self._record(
                    tx, owner_id, meta, IngestKind.AUDIO, byte_length, status, now
                )
        self._volatile.put(result.state)
        self._budget.release(meta.session_id, result.released_bytes)
        return self._response(meta, status)

    # --- shared -------------------------------------------------------------

    @contextmanager
    def _reservation(self, session_id: UUID) -> Iterator[_Reservation]:
        """Release whatever this request reserved if it does not complete."""
        reservation = _Reservation()
        try:
            yield reservation
        except BaseException:
            self._budget.release(session_id, reservation.nbytes)
            raise

    def _reserve(
        self, session_id: UUID, nbytes: int, reservation: _Reservation
    ) -> None:
        if not self._budget.try_reserve(session_id, nbytes):
            msg = "in-memory media budget is full; drop this upload and retry later"
            raise OverloadedError(msg, retry_after_seconds=_RETRY_AFTER_SECONDS)
        reservation.nbytes += nbytes

    async def _duplicate(
        self, tx: StorageTx, owner_id: str, meta: CaptureEvent, now: datetime
    ) -> IngestResponse | None:
        prior = await tx.receipts.get(meta.event_id)
        if prior is None:
            return None
        if prior.owner_id != owner_id or prior.session_id != meta.session_id:
            msg = "event_id was already used for a different upload"
            raise ValidationFailure(msg)
        return IngestResponse(
            event_id=meta.event_id,
            session_id=meta.session_id,
            status=AckStatus.DUPLICATE,
            prior_outcome=prior.outcome,
            admitted_by_current_process=prior.process_epoch == self._epoch,
        )

    async def _admit_session(
        self, tx: StorageTx, owner_id: str, meta: CaptureEvent, now: datetime
    ) -> SessionState:
        session = await tx.sessions.get(meta.session_id, for_update=True)
        if session is None:
            self._volatile.ensure_capacity(meta.session_id)
            await tx.sessions.insert(
                SessionRecord(
                    session_id=meta.session_id,
                    owner_id=owner_id,
                    source_id=meta.source_id,
                    started_at=now,
                    last_ingest_at=now,
                )
            )
        elif session.owner_id != owner_id:
            msg = "session not found"
            raise NotFoundError(msg)
        elif session.status is not SessionStatus.ACTIVE:
            msg = "this session has ended; start a new session id"
            raise SessionEndedError(msg)
        elif session.source_id != meta.source_id:
            msg = "this session is bound to a different capture source"
            raise SourceConflictError(msg)
        state = self._volatile.get(meta.session_id)
        if state is not None:
            return state
        self._volatile.ensure_capacity(meta.session_id)
        return new_session_state(
            owner_id=owner_id,
            session_id=meta.session_id,
            source_id=meta.source_id,
            now=now,
        )

    async def _record(
        self,
        tx: StorageTx,
        owner_id: str,
        meta: CaptureEvent,
        kind: IngestKind,
        byte_length: int,
        status: AckStatus,
        now: datetime,
    ) -> None:
        if isinstance(meta, FrameObservation):
            start = end = meta.capture_ts_ms
        elif isinstance(meta, AudioChunk):
            start, end = meta.capture_start_ms, meta.capture_end_ms
        else:
            start = end = 0
        if status is AckStatus.ACCEPTED:
            await tx.sessions.touch(meta.session_id, now)
        await tx.receipts.insert(
            IngestReceipt(
                event_id=meta.event_id,
                owner_id=owner_id,
                session_id=meta.session_id,
                kind=kind,
                source_seq=meta.source_seq,
                capture_start_ms=start,
                capture_end_ms=end,
                received_at=now,
                byte_length=byte_length,
                outcome=status.value,
                process_epoch=self._epoch,
            )
        )

    @staticmethod
    def _response(meta: CaptureEvent, status: AckStatus) -> IngestResponse:
        return IngestResponse(
            event_id=meta.event_id,
            session_id=meta.session_id,
            status=status,
        )
