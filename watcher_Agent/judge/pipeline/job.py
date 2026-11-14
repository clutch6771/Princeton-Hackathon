"""A judge job: one finalized exposure with its volatile media."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from judge.audio.assembler import AudioAssembler
from judge.audio.pcm import PCM_SAMPLE_RATE
from judge.config import Settings
from judge.contracts.candidate import CoverageMetadata, ReelCandidate
from judge.contracts.storage import ThumbnailRecord
from judge.ingest.session_state import ClosedCandidate, SessionState
from judge.segmentation.frame_selector import KeptFrame
from judge.transcriber.prepare import PreparedAudio, prepare_audio

_MIN_THUMBNAIL_QUALITY = 30
_QUALITY_STEP = 10


@dataclass(frozen=True, slots=True)
class JudgeJob:
    """Everything the pipeline needs for one exposure. Held only in RAM."""

    candidate: ReelCandidate
    frames: tuple[KeptFrame, ...]
    audio: PreparedAudio
    created_at: datetime
    observed_end_at: datetime | None
    closed_monotonic: float
    ready_monotonic: float

    @property
    def reserved_bytes(self) -> int:
        """Bytes of raw media this job holds."""
        return sum(frame.nbytes for frame in self.frames) + self.audio.nbytes


def _coverage(
    closed: ClosedCandidate, frames: tuple[KeptFrame, ...], audio: PreparedAudio
) -> CoverageMetadata:
    return CoverageMetadata(
        observed_ms=closed.end_ms - closed.start_ms,
        frame_count=closed.frame_count,
        selected_frame_count=len(frames),
        audio_window_ms=audio.window_ms,
        audio_kept_ms=audio.kept_ms,
        audio_boundary_guard_ms=closed.boundary_guard_ms,
        audio_uncertified_ms=audio.uncertified_ms,
        audio_protected_ms=audio.protected_ms,
        audio_corrupt_ms=audio.corrupt_ms,
        audio_missing_ms=audio.missing_ms,
        uncorroborated_changes=closed.uncorroborated_changes,
        truncated=closed.truncated,
    )


def build_job(
    state: SessionState,
    closed: ClosedCandidate,
    *,
    settings: Settings,
    assembler: AudioAssembler,
    ready_monotonic: float,
) -> JudgeJob:
    """Assemble the permitted audio and pick the frames for one exposure."""
    assembled = assembler.assemble(state.pcm, state.notes, closed.audio_window)
    audio = prepare_audio(
        assembled,
        sample_rate=PCM_SAMPLE_RATE,
        min_ms_for_model=settings.min_audio_ms_for_model,
    )
    frames = closed.selection.frames(settings.judge_selected_frames)
    candidate = ReelCandidate(
        candidate_id=closed.reel_id,
        owner_id=state.owner_id,
        session_id=state.session_id,
        source_id=state.source_id,
        order_seq=max(closed.order_seq, 1),
        first_event_id=closed.first_event_id,
        start_ms=closed.start_ms,
        end_ms=closed.end_ms,
        boundary_uncertainty_ms=closed.boundary_uncertainty_ms,
        first_second_observed=closed.first_second_observed,
        segmentation_status=closed.segmentation_status,
        close_reason=closed.close_reason,
        coverage=_coverage(closed, frames, audio),
        audio_status=audio.status,
    )
    return JudgeJob(
        candidate=candidate,
        frames=frames,
        audio=audio,
        created_at=closed.closed_at,
        observed_end_at=state.clock.to_server_time(closed.end_ms),
        closed_monotonic=closed.closed_monotonic,
        ready_monotonic=ready_monotonic,
    )


def make_thumbnail(job: JudgeJob, settings: Settings) -> ThumbnailRecord | None:
    """Re-encode one privacy-screened frame as the single bounded thumbnail.

    The image is decoded and re-encoded in memory, which drops any embedded
    metadata. Returns ``None`` when it cannot be made small enough.
    """
    if not job.frames:
        return None
    source = job.frames[len(job.frames) // 2]
    image = Image.open(io.BytesIO(source.jpeg)).convert("RGB")
    edge = settings.thumbnail_max_long_edge_px
    image.thumbnail((edge, edge))
    quality = settings.thumbnail_jpeg_quality
    while quality >= _MIN_THUMBNAIL_QUALITY:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        data = buffer.getvalue()
        if len(data) <= settings.thumbnail_max_bytes:
            return ThumbnailRecord(
                reel_id=job.candidate.candidate_id,
                owner_id=job.candidate.owner_id,
                jpeg=data,
                width=image.width,
                height=image.height,
                sha256=hashlib.sha256(data).hexdigest(),
            )
        quality -= _QUALITY_STEP
    return None
