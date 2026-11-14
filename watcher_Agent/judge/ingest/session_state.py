"""Volatile per-session state as immutable values and pure transitions.

Everything here lives only in RAM: safe frames, timestamped PCM and candidate
intervals. Transitions return a new state and never mutate the old one, so a
failed database transaction can simply discard the new state.

Boundary handling: the true reel boundary lies somewhere between the last
frame of one reel and the first frame of the next. Without a native
transition hint that whole interval is treated as uncertain and its audio is
attributed to neither reel.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid5

from judge.audio.pcm import AudioNote, AudioNoteKind, PcmSegment
from judge.config import Settings
from judge.contracts.candidate import CloseReason, SegmentationStatus
from judge.contracts.capture import AudioChunk
from judge.contracts.ingest import AckStatus
from judge.ingest.clock_mapping import ClockMapping
from judge.segmentation.frame_selector import FrameSelection, KeptFrame
from judge.segmentation.segmenter import (
    BoundaryEvidence,
    FrameSample,
    ReelSegmenter,
    TransitionAssessment,
    TransitionKind,
)

__all__ = [
    "AudioNote",
    "AudioNoteKind",
    "AudioResult",
    "ClosedCandidate",
    "FrameResult",
    "KeptFrame",
    "OpenCandidate",
    "PcmSegment",
    "SessionState",
    "StateConfig",
    "apply_audio",
    "apply_feed_exit",
    "apply_frame",
    "close_for_end",
    "new_session_state",
    "prune_audio",
    "reel_id_for",
    "take_ready",
    "with_order_seqs",
]

REEL_NAMESPACE: Final = UUID("5b3f2a54-8d0e-4c0a-9b1e-6f2d1c7a9e42")
FIRST_SECOND_MS: Final = 1000
_MS: Final = 1000


def reel_id_for(session_id: UUID, first_event_id: UUID) -> UUID:
    """Deterministic reel id: one exposure is identified by its first frame."""
    return uuid5(REEL_NAMESPACE, f"{session_id}:{first_event_id}")


class StartEvidence(StrEnum):
    """How the start of an exposure was established."""

    UNKNOWN = "unknown"
    HINT = "hint"
    FRAME = "frame"


@dataclass(frozen=True, slots=True)
class StateConfig:
    """Engineering settings the transitions depend on."""

    max_frames: int
    max_candidate_ms: int
    late_grace_ms: int
    hint_uncertainty_ms: int
    default_uncertainty_ms: int
    final_audio_wait_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> StateConfig:
        """Derive the transition configuration from runtime settings."""
        return cls(
            max_frames=settings.judge_selected_frames,
            max_candidate_ms=int(settings.max_candidate_seconds * _MS),
            late_grace_ms=int(settings.late_event_grace_seconds * _MS),
            hint_uncertainty_ms=settings.transition_hint_uncertainty_ms,
            default_uncertainty_ms=int(settings.frame_interval_seconds * _MS),
            final_audio_wait_seconds=settings.final_audio_wait_seconds,
        )


@dataclass(frozen=True, slots=True)
class OpenCandidate:
    """The exposure currently on screen."""

    first_event_id: UUID
    start_ms: int
    start_audio_ms: int
    start_uncertainty_ms: int
    start_guard_ms: int
    start_evidence: StartEvidence
    first_second_observed: bool
    last_frame_ms: int
    frame_count: int
    selection: FrameSelection
    uncorroborated_changes: int = 0


@dataclass(frozen=True, slots=True)
class ClosedCandidate:
    """A closed exposure waiting for its final permitted audio."""

    reel_id: UUID
    first_event_id: UUID
    start_ms: int
    end_ms: int
    audio_window: tuple[int, int] | None
    boundary_guard_ms: int
    boundary_uncertainty_ms: int
    first_second_observed: bool
    segmentation_status: SegmentationStatus
    close_reason: CloseReason
    frame_count: int
    selection: FrameSelection
    uncorroborated_changes: int
    truncated: bool
    closed_at: datetime
    closed_monotonic: float
    order_seq: int = 0


@dataclass(frozen=True, slots=True)
class SessionState:
    """All volatile state of one capture session."""

    owner_id: str
    session_id: UUID
    source_id: str
    last_accepted_at: datetime
    clock: ClockMapping = ClockMapping()
    last_frame: FrameSample | None = None
    open: OpenCandidate | None = None
    awaiting_boundary: bool = False
    closed: tuple[ClosedCandidate, ...] = ()
    pcm: tuple[PcmSegment, ...] = ()
    notes: tuple[AudioNote, ...] = ()
    audio_watermark_ms: int | None = None
    finalized_until_ms: int | None = None
    ending: bool = False

    @property
    def held_bytes(self) -> int:
        """Bytes of raw media this state holds (frames and PCM)."""
        frames = sum(c.selection.nbytes for c in self.closed)
        if self.open is not None:
            frames += self.open.selection.nbytes
        return frames + sum(segment.nbytes for segment in self.pcm)


@dataclass(frozen=True, slots=True)
class FrameResult:
    """Outcome of applying one eligible frame."""

    state: SessionState
    status: AckStatus
    closed: tuple[ClosedCandidate, ...] = ()
    released: tuple[KeptFrame, ...] = ()


@dataclass(frozen=True, slots=True)
class AudioResult:
    """Outcome of applying one decoded audio chunk."""

    state: SessionState
    status: AckStatus
    released_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _CloseSpec:
    reason: CloseReason
    end_audio_ms: int
    end_uncertainty_ms: int
    end_guard_ms: int
    end_by_hint: bool = False
    truncated: bool = False


def new_session_state(
    *, owner_id: str, session_id: UUID, source_id: str, now: datetime
) -> SessionState:
    """Empty state for a newly admitted session."""
    return SessionState(
        owner_id=owner_id,
        session_id=session_id,
        source_id=source_id,
        last_accepted_at=now,
    )


def _has_hint(sample: FrameSample, assessment: TransitionAssessment) -> bool:
    return (
        BoundaryEvidence.TRANSITION_HINT in assessment.evidence
        and sample.transition_at_ms is not None
    )


def _segmentation_status(
    candidate: OpenCandidate, spec: _CloseSpec
) -> SegmentationStatus:
    weak_close = spec.reason in {CloseReason.FRAME_GAP, CloseReason.MAX_DURATION}
    if candidate.uncorroborated_changes or candidate.frame_count <= 1 or weak_close:
        return SegmentationStatus.UNCERTAIN
    if candidate.start_evidence is StartEvidence.HINT and spec.end_by_hint:
        return SegmentationStatus.CLEAN
    return SegmentationStatus.APPROXIMATE


def _close(
    state: SessionState,
    candidate: OpenCandidate,
    spec: _CloseSpec,
    now: datetime,
    monotonic: float,
) -> ClosedCandidate:
    has_window = spec.end_audio_ms > candidate.start_audio_ms
    return ClosedCandidate(
        reel_id=reel_id_for(state.session_id, candidate.first_event_id),
        first_event_id=candidate.first_event_id,
        start_ms=candidate.start_ms,
        end_ms=candidate.last_frame_ms,
        audio_window=(
            (candidate.start_audio_ms, spec.end_audio_ms) if has_window else None
        ),
        boundary_guard_ms=candidate.start_guard_ms + spec.end_guard_ms,
        boundary_uncertainty_ms=max(
            candidate.start_uncertainty_ms, spec.end_uncertainty_ms
        ),
        first_second_observed=candidate.first_second_observed,
        segmentation_status=_segmentation_status(candidate, spec),
        close_reason=spec.reason,
        frame_count=candidate.frame_count,
        selection=candidate.selection,
        uncorroborated_changes=candidate.uncorroborated_changes,
        truncated=spec.truncated,
        closed_at=now,
        closed_monotonic=monotonic,
    )


def _boundary_spec(
    candidate: OpenCandidate,
    sample: FrameSample,
    assessment: TransitionAssessment,
    config: StateConfig,
) -> _CloseSpec:
    last = candidate.last_frame_ms
    if _has_hint(sample, assessment) and sample.transition_at_ms is not None:
        hint = sample.transition_at_ms
        end_audio = max(last, hint - config.hint_uncertainty_ms)
        next_start = min(sample.ts_ms, hint + config.hint_uncertainty_ms)
        return _CloseSpec(
            reason=CloseReason.BOUNDARY,
            end_audio_ms=end_audio,
            end_uncertainty_ms=config.hint_uncertainty_ms,
            end_guard_ms=max(0, next_start - end_audio),
            end_by_hint=True,
        )
    if assessment.evidence == (BoundaryEvidence.FRAME_GAP,):
        return _CloseSpec(
            reason=CloseReason.FRAME_GAP,
            end_audio_ms=last,
            end_uncertainty_ms=config.default_uncertainty_ms,
            end_guard_ms=0,
        )
    interval = sample.ts_ms - last
    return _CloseSpec(
        reason=CloseReason.BOUNDARY,
        end_audio_ms=last,
        end_uncertainty_ms=interval,
        end_guard_ms=interval,
    )


def _open(
    sample: FrameSample,
    frame: KeptFrame,
    previous: FrameSample | None,
    assessment: TransitionAssessment,
    config: StateConfig,
) -> OpenCandidate:
    start_audio, uncertainty, guard = sample.ts_ms, config.default_uncertainty_ms, 0
    evidence, first_second = StartEvidence.UNKNOWN, False
    if previous is None or assessment.kind is TransitionKind.START:
        pass
    elif _has_hint(sample, assessment) and sample.transition_at_ms is not None:
        hint = sample.transition_at_ms
        start_audio = min(sample.ts_ms, hint + config.hint_uncertainty_ms)
        uncertainty = config.hint_uncertainty_ms
        lower = max(previous.ts_ms, hint - config.hint_uncertainty_ms)
        guard = max(0, start_audio - lower)
        evidence = StartEvidence.HINT
        first_second = sample.ts_ms - hint <= FIRST_SECOND_MS
    elif assessment.evidence == (BoundaryEvidence.FRAME_GAP,):
        evidence = StartEvidence.FRAME
    else:
        uncertainty = guard = sample.ts_ms - previous.ts_ms
        evidence = StartEvidence.FRAME
        first_second = uncertainty <= FIRST_SECOND_MS
    return OpenCandidate(
        first_event_id=sample.event_id,
        start_ms=sample.ts_ms,
        start_audio_ms=start_audio,
        start_uncertainty_ms=uncertainty,
        start_guard_ms=guard,
        start_evidence=evidence,
        first_second_observed=first_second,
        last_frame_ms=sample.ts_ms,
        frame_count=1,
        selection=FrameSelection(first=frame),
    )


def _apply_late_frame(
    state: SessionState, sample: FrameSample, frame: KeptFrame, config: StateConfig
) -> FrameResult:
    candidate, last = state.open, state.last_frame
    inside = (
        candidate is not None
        and last is not None
        and sample.ts_ms >= candidate.start_ms
        and last.ts_ms - sample.ts_ms <= config.late_grace_ms
    )
    if not inside or candidate is None:
        return FrameResult(state, AckStatus.LATE_DISCARDED, released=(frame,))
    selection, dropped = candidate.selection.offer_late(frame)
    updated = replace(
        candidate, frame_count=candidate.frame_count + 1, selection=selection
    )
    return FrameResult(
        replace(state, open=updated), AckStatus.ACCEPTED, released=dropped
    )


def _extend(
    candidate: OpenCandidate,
    sample: FrameSample,
    frame: KeptFrame,
    assessment: TransitionAssessment,
) -> tuple[OpenCandidate, tuple[KeptFrame, ...]]:
    selection, dropped = candidate.selection.add(frame)
    cut = 1 if assessment.kind is TransitionKind.CUT else 0
    extended = replace(
        candidate,
        last_frame_ms=sample.ts_ms,
        frame_count=candidate.frame_count + 1,
        selection=selection,
        uncorroborated_changes=candidate.uncorroborated_changes + cut,
    )
    return extended, dropped


def apply_frame(
    state: SessionState,
    sample: FrameSample,
    frame: KeptFrame,
    *,
    config: StateConfig,
    segmenter: ReelSegmenter,
    now: datetime,
    monotonic: float,
) -> FrameResult:
    """Apply one privacy-screened frame in capture order."""
    previous = state.last_frame
    if previous is not None and sample.ts_ms <= previous.ts_ms:
        return _apply_late_frame(state, sample, frame, config)
    assessment = segmenter.assess(previous, sample)
    starts_new = assessment.kind in {TransitionKind.START, TransitionKind.BOUNDARY}
    candidate, awaiting = state.open, state.awaiting_boundary and not starts_new
    closed: list[ClosedCandidate] = []
    released: tuple[KeptFrame, ...] = ()
    if starts_new and candidate is not None:
        spec = _boundary_spec(candidate, sample, assessment, config)
        closed.append(_close(state, candidate, spec, now, monotonic))
        candidate = None
    if candidate is None and awaiting:
        released = (frame,)
    elif candidate is None:
        candidate = _open(sample, frame, previous, assessment, config)
    else:
        candidate, released = _extend(candidate, sample, frame, assessment)
        if sample.ts_ms - candidate.start_ms >= config.max_candidate_ms:
            spec = _CloseSpec(
                reason=CloseReason.MAX_DURATION,
                end_audio_ms=sample.ts_ms,
                end_uncertainty_ms=config.default_uncertainty_ms,
                end_guard_ms=0,
                truncated=True,
            )
            closed.append(_close(state, candidate, spec, now, monotonic))
            candidate, awaiting = None, True
    new_state = replace(
        state,
        last_frame=sample,
        open=candidate,
        awaiting_boundary=awaiting,
        closed=(*state.closed, *closed),
        last_accepted_at=now,
    )
    return FrameResult(new_state, AckStatus.ACCEPTED, tuple(closed), released)


def apply_feed_exit(
    state: SessionState,
    ts_ms: int,
    *,
    config: StateConfig,
    now: datetime,
    monotonic: float,
) -> tuple[SessionState, tuple[ClosedCandidate, ...]]:
    """The user left the Reels surface: close whatever was on screen."""
    candidate = state.open
    cleared = replace(state, open=None, last_frame=None, awaiting_boundary=False)
    if candidate is None:
        return cleared, ()
    interval = ts_ms - candidate.last_frame_ms
    uncertainty = interval if interval > 0 else config.default_uncertainty_ms
    spec = _CloseSpec(
        reason=CloseReason.LEFT_FEED,
        end_audio_ms=candidate.last_frame_ms,
        end_uncertainty_ms=uncertainty,
        end_guard_ms=max(0, interval),
    )
    closed = _close(state, candidate, spec, now, monotonic)
    return replace(cleared, closed=(*state.closed, closed)), (closed,)


def apply_audio(
    state: SessionState,
    chunk: AudioChunk,
    segments: tuple[PcmSegment, ...],
    notes: tuple[AudioNote, ...],
    *,
    now: datetime,
) -> AudioResult:
    """Buffer the permitted PCM of one decoded chunk by capture time."""
    finalized = state.finalized_until_ms
    offered = sum(segment.nbytes for segment in segments)
    if finalized is not None and chunk.capture_end_ms <= finalized:
        return AudioResult(state, AckStatus.LATE_DISCARDED, released_bytes=offered)
    kept = tuple(s for s in segments if finalized is None or s.end_ms > finalized)
    kept_notes = tuple(n for n in notes if finalized is None or n.end_ms > finalized)
    watermark = max(state.audio_watermark_ms or 0, chunk.capture_end_ms)
    new_state = replace(
        state,
        pcm=tuple(sorted((*state.pcm, *kept), key=lambda s: s.start_ms)),
        notes=(*state.notes, *kept_notes),
        audio_watermark_ms=watermark,
        last_accepted_at=now,
    )
    released = offered - sum(segment.nbytes for segment in kept)
    return AudioResult(new_state, AckStatus.ACCEPTED, released_bytes=released)


def _is_ready(
    state: SessionState, candidate: ClosedCandidate, monotonic: float, wait: float
) -> bool:
    if state.ending or candidate.audio_window is None:
        return True
    watermark = state.audio_watermark_ms
    if watermark is not None and watermark >= candidate.audio_window[1]:
        return True
    return monotonic - candidate.closed_monotonic >= wait


def take_ready(
    state: SessionState, *, monotonic: float, config: StateConfig
) -> tuple[SessionState, tuple[ClosedCandidate, ...]]:
    """Remove and return closed exposures whose final audio wait is over.

    A candidate waits only until the audio watermark passes its window or the
    documented deadline elapses. Order is preserved.
    """
    ready: list[ClosedCandidate] = []
    for candidate in state.closed:
        if not _is_ready(state, candidate, monotonic, config.final_audio_wait_seconds):
            break
        ready.append(candidate)
    if not ready:
        return state, ()
    ends = [c.audio_window[1] if c.audio_window else c.end_ms for c in ready]
    finalized = max([*ends, state.finalized_until_ms or 0])
    new_state = replace(
        state, closed=state.closed[len(ready) :], finalized_until_ms=finalized
    )
    return new_state, tuple(ready)


def prune_audio(state: SessionState) -> tuple[SessionState, int]:
    """Drop PCM and notes that no remaining exposure can use."""
    finalized = state.finalized_until_ms
    if finalized is None:
        return state, 0
    kept = tuple(s for s in state.pcm if s.end_ms > finalized)
    released = sum(s.nbytes for s in state.pcm) - sum(s.nbytes for s in kept)
    notes = tuple(n for n in state.notes if n.end_ms > finalized)
    return replace(state, pcm=kept, notes=notes), released


def close_for_end(
    state: SessionState, *, config: StateConfig, now: datetime, monotonic: float
) -> tuple[SessionState, tuple[ClosedCandidate, ...]]:
    """Session end: close the open exposure; nothing waits any longer."""
    candidate = state.open
    ending = replace(state, open=None, ending=True, awaiting_boundary=False)
    if candidate is None:
        return ending, ()
    spec = _CloseSpec(
        reason=CloseReason.SESSION_END,
        end_audio_ms=candidate.last_frame_ms,
        end_uncertainty_ms=config.default_uncertainty_ms,
        end_guard_ms=0,
    )
    closed = _close(state, candidate, spec, now, monotonic)
    return replace(ending, closed=(*state.closed, closed)), (closed,)


def with_order_seqs(state: SessionState, order_seqs: dict[UUID, int]) -> SessionState:
    """Attach storage-allocated capture-order numbers to closed exposures."""
    closed = tuple(
        replace(c, order_seq=order_seqs.get(c.reel_id, c.order_seq))
        for c in state.closed
    )
    return replace(state, closed=closed)
