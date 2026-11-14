"""Reel record: the PRD fields plus the documented operational extension.

PRD fields: id, session_id, start, end, thumbnail_url, transcript,
on_screen_text, description, features.*, verdict, confidence, value_ids,
reason.

Schema extension (not in the PRD): ``evaluation_status`` and a *nullable*
``verdict``. Only ``judged`` records carry a verdict. A reel that could not be
judged has ``verdict = null``; it is never recorded as ``neutral``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from judge.contracts.base import CONTRACT_VERSION, Contract, ContractVersion
from judge.contracts.candidate import (
    AudioStatus,
    CloseReason,
    CoverageMetadata,
    SegmentationStatus,
)
from judge.contracts.feedback import RetrievalMode
from judge.contracts.understanding import ReelFeatures, TranscriptStatus, Verdict


def thumbnail_path(reel_id: UUID) -> str:
    """API path of a reel's single retained thumbnail."""
    return f"/thumbs/{reel_id}.jpg"


class EvaluationStatus(StrEnum):
    """Operational state of one observed exposure."""

    PENDING = "pending"
    JUDGED = "judged"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    VALUES_UNAVAILABLE = "values_unavailable"
    FAILED_PROVIDER = "failed_provider"
    FAILED_VALIDATION = "failed_validation"
    FAILED_TIMEOUT = "failed_timeout"
    SKIPPED_OVERLOAD = "skipped_overload"
    LOST_ON_RESTART = "lost_on_restart"

    @property
    def is_terminal(self) -> bool:
        """Everything except ``pending`` is final."""
        return self is not EvaluationStatus.PENDING


class EmbeddingStatus(StrEnum):
    """Indexing state of a judged reel's embedding."""

    PENDING = "pending"
    INDEXED = "indexed"
    FAILED = "failed"


class EmbeddingInfo(Contract):
    """Versioned identity and state of a reel's embedding."""

    profile_id: str
    model_id: str
    dimension: int = Field(gt=0)
    status: EmbeddingStatus
    attempts: int = Field(ge=0)


class StageTimings(Contract):
    """Measured stage durations in milliseconds (server clock).

    ``total_ms`` runs from the observed reel end (last eligible frame, mapped
    to the server clock, approximate) to the moment the outcome was ready to
    commit. It is a measurement, not a guarantee.
    """

    detect_ms: int | None = Field(
        default=None, description="Observed reel end to boundary detection."
    )
    audio_wait_ms: int | None = Field(
        default=None, description="Waiting for the final permitted audio chunk."
    )
    queue_ms: int | None = None
    context_ms: int | None = Field(
        default=None, description="Loading approved values and examples."
    )
    inference_ms: int | None = None
    validation_ms: int | None = None
    persist_ms: int | None = Field(
        default=None, description="Database work, excluding the final commit."
    )
    total_ms: int | None = None


class PendingReel(Contract):
    """Metadata stored when an exposure closes. Never contains media."""

    reel_id: UUID
    owner_id: str
    session_id: UUID
    source_id: str
    order_seq: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1)
    start_ms: int = Field(gt=0)
    end_ms: int = Field(gt=0)
    boundary_uncertainty_ms: int = Field(ge=0)
    first_second_observed: bool
    segmentation_status: SegmentationStatus
    close_reason: CloseReason
    created_at: datetime


class ReelCompletion(Contract):
    """The single terminal outcome committed for a pending reel."""

    reel_id: UUID
    owner_id: str
    session_id: UUID
    evaluation_status: EvaluationStatus
    completed_at: datetime
    coverage: CoverageMetadata | None = None
    audio_status: AudioStatus | None = None
    transcript_status: TranscriptStatus | None = None
    transcript: str = ""
    on_screen_text: str = ""
    description: str = ""
    features: ReelFeatures | None = None
    verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    value_ids: tuple[str, ...] = ()
    reason: str = ""
    values_revision: str | None = None
    values_sha256: str | None = None
    feedback_example_ids: tuple[UUID, ...] = ()
    retrieval_mode: RetrievalMode | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    understanding_attempts: int = Field(default=0, ge=0)
    error_code: str | None = None

    @model_validator(mode="after")
    def _verdict_only_when_judged(self) -> Self:
        if not self.evaluation_status.is_terminal:
            msg = "a completion must carry a terminal evaluation_status"
            raise ValueError(msg)
        judged = self.evaluation_status is EvaluationStatus.JUDGED
        has_judgment = (
            self.verdict is not None
            and self.confidence is not None
            and self.features is not None
            and bool(self.description)
            and bool(self.reason)
        )
        if judged and not has_judgment:
            msg = "a judged reel needs verdict, confidence, features and text"
            raise ValueError(msg)
        if not judged and (
            self.verdict is not None
            or self.confidence is not None
            or self.features is not None
            or self.value_ids
        ):
            msg = "only judged reels may carry a verdict; use null, never neutral"
            raise ValueError(msg)
        return self


class ReelAccountingView(Contract):
    """The few fields ordered notification accounting needs."""

    reel_id: UUID
    order_seq: int
    evaluation_status: EvaluationStatus
    verdict: Verdict | None = None


class PendingReelRef(Contract):
    """Reference to a pending reel, used by deadline and restart sweeps."""

    reel_id: UUID
    owner_id: str
    session_id: UUID
    created_at: datetime


class ReelRecord(Contract):
    """One stored reel exposure, as returned by the timeline."""

    contract_version: ContractVersion = CONTRACT_VERSION
    # --- PRD fields ---------------------------------------------------------
    id: UUID
    session_id: UUID
    start: datetime = Field(description="First eligible frame, capture clock.")
    end: datetime = Field(description="Last eligible frame, capture clock.")
    thumbnail_url: str | None = None
    transcript: str = ""
    on_screen_text: str = ""
    description: str = ""
    features: ReelFeatures | None = None
    verdict: Verdict | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    value_ids: tuple[str, ...] = ()
    reason: str = ""
    # --- Operational extension ----------------------------------------------
    evaluation_status: EvaluationStatus
    owner_id: str
    source_id: str
    order_seq: int = Field(ge=1)
    idempotency_key: str
    boundary_uncertainty_ms: int = Field(ge=0)
    first_second_observed: bool
    segmentation_status: SegmentationStatus
    close_reason: CloseReason
    coverage: CoverageMetadata | None = None
    audio_status: AudioStatus | None = None
    transcript_status: TranscriptStatus | None = None
    values_revision: str | None = None
    values_sha256: str | None = None
    feedback_example_ids: tuple[UUID, ...] = ()
    retrieval_mode: RetrievalMode | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    understanding_attempts: int = Field(default=0, ge=0)
    embedding: EmbeddingInfo | None = None
    timings: StageTimings | None = None
    latency_overrun: bool | None = None
    error_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _verdict_only_when_judged(self) -> Self:
        judged = self.evaluation_status is EvaluationStatus.JUDGED
        if judged != (self.verdict is not None):
            msg = "verdict is set if and only if evaluation_status is 'judged'"
            raise ValueError(msg)
        return self
