"""Reel candidate: one observed exposure, with honest coverage metadata.

A candidate describes what was *observed* (sampled frames and the audio that
could be kept), never the complete original video.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from judge.contracts.base import CONTRACT_VERSION, Contract, ContractVersion


class SegmentationStatus(StrEnum):
    """How trustworthy the exposure's boundaries are."""

    CLEAN = "clean"
    APPROXIMATE = "approximate"
    UNCERTAIN = "uncertain"


class CloseReason(StrEnum):
    """Why the exposure was closed."""

    BOUNDARY = "boundary"
    LEFT_FEED = "left_feed"
    FRAME_GAP = "frame_gap"
    MAX_DURATION = "max_duration"
    SESSION_END = "session_end"


class AudioStatus(StrEnum):
    """Evidence-backed state of the audio assembled for an exposure."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    UNCERTIFIED = "uncertified"
    PROTECTED = "protected"
    CORRUPT = "corrupt"


class CoverageMetadata(Contract):
    """What was and was not observed for one exposure. All values in ms."""

    observed_ms: int = Field(ge=0, description="First to last eligible frame.")
    frame_count: int = Field(ge=0)
    selected_frame_count: int = Field(ge=0)
    audio_window_ms: int = Field(
        ge=0, description="Span in which audio could be attributed to this reel."
    )
    audio_kept_ms: int = Field(ge=0)
    audio_boundary_guard_ms: int = Field(
        ge=0, description="Discarded because the reel boundary was uncertain."
    )
    audio_uncertified_ms: int = Field(
        ge=0, description="Discarded: no native eligibility certification."
    )
    audio_protected_ms: int = Field(ge=0)
    audio_corrupt_ms: int = Field(ge=0)
    audio_missing_ms: int = Field(ge=0, description="No chunk covered this time.")
    uncorroborated_changes: int = Field(
        ge=0, description="Sharp frame changes treated as in-reel cuts."
    )
    truncated: bool = Field(description="Closed at the maximum exposure length.")

    @property
    def audio_coverage_ratio(self) -> float:
        """Kept audio as a fraction of the attributable window (0 when none)."""
        if self.audio_window_ms <= 0:
            return 0.0
        return min(1.0, self.audio_kept_ms / self.audio_window_ms)


class ReelCandidate(Contract):
    """Metadata of a finalized exposure handed to understanding."""

    contract_version: ContractVersion = CONTRACT_VERSION
    candidate_id: UUID
    owner_id: str
    session_id: UUID
    source_id: str
    order_seq: int = Field(ge=1, description="Capture order within the session.")
    first_event_id: UUID = Field(description="Stable idempotency identity.")
    start_ms: int = Field(gt=0, description="First eligible frame (capture clock).")
    end_ms: int = Field(gt=0, description="Last eligible frame (capture clock).")
    boundary_uncertainty_ms: int = Field(ge=0)
    first_second_observed: bool
    segmentation_status: SegmentationStatus
    close_reason: CloseReason
    coverage: CoverageMetadata
    audio_status: AudioStatus
