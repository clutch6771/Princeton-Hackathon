"""Admit already segmented device observations into the shared Judge queue."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
from datetime import timedelta
from uuid import UUID, uuid5

from PIL import Image, UnidentifiedImageError

from judge.bootstrap import JudgeRuntime
from judge.contracts.candidate import (
    AudioStatus,
    CloseReason,
    CoverageMetadata,
    ReelCandidate,
    SegmentationStatus,
)
from judge.contracts.capture import FrameObservation
from judge.contracts.ingest import AckStatus, IngestResponse
from judge.contracts.native import NativeReelUpload
from judge.contracts.record import EvaluationStatus, PendingReel
from judge.contracts.session import SessionRecord, SessionStatus
from judge.errors import (
    NotFoundError,
    OverloadedError,
    ReelErrorCode,
    SessionEndedError,
    SourceConflictError,
    ValidationFailure,
)
from judge.ingest.admission import inspect_frame
from judge.pipeline.job import JudgeJob
from judge.pipeline.outcomes import failed_outcome
from judge.privacy.eligibility import GateDecision
from judge.segmentation.frame_selector import KeptFrame
from judge.transcriber.prepare import PreparedAudio

SOURCE = "ios-reel-tracker"


def _frame(
    payload: NativeReelUpload, event_id: UUID, start_ms: int
) -> tuple[bytes, FrameObservation]:
    try:
        jpeg = base64.b64decode(payload.frame_base64 or "", validate=True)
        with Image.open(io.BytesIO(jpeg)) as image:
            width, height = image.size
    except (binascii.Error, ValueError, OSError, UnidentifiedImageError) as error:
        raise ValidationFailure("frame_base64 must contain a JPEG") from error
    return jpeg, FrameObservation(
        event_id=event_id,
        session_id=payload.session_id,
        source_id=SOURCE,
        source_seq=payload.reel_index,
        privacy=payload.privacy,
        capture_ts_ms=start_ms,
        mime_type="image/jpeg",
        width=width,
        height=height,
    )


async def ingest_native_reel(
    payload: NativeReelUpload, runtime: JudgeRuntime, owner_id: str
) -> IngestResponse:
    """Queue one device-segmented reel; retries reuse session ID + reel index.

    Legacy payloads without provenance work only in dev_permissive mode.
    Device transcripts have unknown coverage and are always marked partial.
    """
    event_id = uuid5(payload.session_id, f"{SOURCE}:{payload.reel_index}")
    response = IngestResponse(
        event_id=event_id, session_id=payload.session_id, status=AckStatus.ACCEPTED
    )
    screening = runtime.gate.screen_frame(payload.privacy)
    if screening.decision is not GateDecision.ADMIT or not payload.frame_base64:
        return response.model_copy(update={"status": AckStatus.PRIVACY_EXCLUDED})
    now = runtime.clock.now()
    start = payload.started_at or (now - timedelta(seconds=payload.dwell_seconds))
    if start.tzinfo is None:
        raise ValidationFailure("started_at must include a timezone")
    start_ms = round(start.timestamp() * 1000)
    end_ms = start_ms + max(1, round(payload.dwell_seconds * 1000))
    jpeg, metadata = await asyncio.to_thread(_frame, payload, event_id, start_ms)
    inspected = await asyncio.to_thread(inspect_frame, jpeg, metadata, runtime.settings)
    transcript = payload.transcript_text or None
    if transcript and len(transcript) > runtime.settings.transcript_max_chars:
        raise ValidationFailure("device transcript exceeds TRANSCRIPT_MAX_CHARS")
    async with runtime.volatile.lock(payload.session_id):
        reserved = False
        try:
            async with runtime.storage.transaction() as tx:
                session = await tx.sessions.get(payload.session_id, for_update=True)
                if session and session.owner_id != owner_id:
                    raise NotFoundError("session not found")
                prior = await tx.reels.get(owner_id, event_id)
                if prior is not None:
                    return response.model_copy(update={"status": AckStatus.DUPLICATE})
                if session and session.status is not SessionStatus.ACTIVE:
                    raise SessionEndedError("this session has ended")
                if session and session.source_id != SOURCE:
                    raise SourceConflictError("session has a different capture source")
                if session is None:
                    await tx.sessions.insert(
                        SessionRecord(
                            session_id=payload.session_id,
                            owner_id=owner_id,
                            source_id=SOURCE,
                            started_at=now,
                            last_ingest_at=now,
                        )
                    )
                if not runtime.budget.try_reserve(payload.session_id, len(jpeg)):
                    raise OverloadedError("media budget is full", retry_after_seconds=1)
                reserved = True
                order = await tx.sessions.allocate_order_seq(payload.session_id)
                pending = PendingReel(
                    reel_id=event_id,
                    owner_id=owner_id,
                    session_id=payload.session_id,
                    source_id=SOURCE,
                    order_seq=order,
                    idempotency_key=str(event_id),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    boundary_uncertainty_ms=0,
                    first_second_observed=False,
                    segmentation_status=SegmentationStatus.APPROXIMATE,
                    close_reason=CloseReason.BOUNDARY,
                    created_at=now,
                )
                await tx.reels.insert_pending(pending)
                await tx.sessions.touch(payload.session_id, now)
            duration = end_ms - start_ms
            audio = PreparedAudio(
                wav=None,
                status=AudioStatus.MISSING,
                window_ms=duration,
                kept_ms=0,
                uncertified_ms=0,
                protected_ms=0,
                corrupt_ms=0,
                missing_ms=duration,
            )
            coverage = CoverageMetadata(
                observed_ms=duration,
                frame_count=1,
                selected_frame_count=1,
                audio_window_ms=duration,
                audio_kept_ms=0,
                audio_boundary_guard_ms=0,
                audio_uncertified_ms=0,
                audio_protected_ms=0,
                audio_corrupt_ms=0,
                audio_missing_ms=duration,
                uncorroborated_changes=0,
                truncated=False,
            )
            job = JudgeJob(
                candidate=ReelCandidate(
                    **pending.model_dump(
                        exclude={"reel_id", "idempotency_key", "created_at"}
                    ),
                    candidate_id=event_id,
                    first_event_id=event_id,
                    coverage=coverage,
                    audio_status=AudioStatus.MISSING,
                ),
                frames=(
                    KeptFrame(
                        event_id=event_id,
                        ts_ms=start_ms,
                        jpeg=jpeg,
                        width=inspected.width,
                        height=inspected.height,
                    ),
                ),
                audio=audio,
                created_at=now,
                observed_end_at=start + timedelta(seconds=payload.dwell_seconds),
                closed_monotonic=runtime.clock.monotonic(),
                ready_monotonic=runtime.clock.monotonic(),
                native_transcript=transcript,
            )
            if runtime.queue.try_submit(job):
                reserved = False  # worker owns and releases the media
            else:
                await runtime.completion.complete(
                    failed_outcome(
                        job,
                        EvaluationStatus.SKIPPED_OVERLOAD,
                        ReelErrorCode.QUEUE_FULL,
                        now=now,
                    )
                )
                raise OverloadedError("judge queue is full", retry_after_seconds=1)
        finally:
            if reserved:
                runtime.budget.release(payload.session_id, len(jpeg))
    return response
