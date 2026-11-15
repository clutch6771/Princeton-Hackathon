"""Storage-facing records that are not wire contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field

from judge.config import Settings
from judge.contracts.base import Contract
from judge.contracts.feedback import FeedbackLabel
from judge.contracts.record import EmbeddingStatus
from judge.contracts.understanding import Verdict


class EmbeddingProfile(Contract):
    """Pinned identity of one vector space. Profiles are never mixed."""

    profile_id: str
    model_id: str
    dimension: int = Field(gt=0)
    task_type: str
    version: str

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """The profile configured for new embeddings."""
        return cls(
            profile_id=settings.embedding_profile_id,
            model_id=settings.embedding_model_id,
            dimension=settings.embedding_dimension,
            task_type=settings.embedding_task_type,
            version=settings.embedding_profile_version,
        )


class EmbeddingTask(Contract):
    """A reel whose embedding is due. Built from stored text only."""

    reel_id: UUID
    owner_id: str
    profile_id: str
    model_id: str
    dimension: int
    attempts: int = Field(ge=0)
    text: str


class EmbeddingRecord(Contract):
    """Indexing metadata of one reel embedding."""

    reel_id: UUID
    owner_id: str
    profile_id: str
    model_id: str
    dimension: int
    task_type: str
    status: EmbeddingStatus
    attempts: int = Field(ge=0)
    next_attempt_at: datetime | None = None
    error_code: str | None = None
    indexed_at: datetime | None = None


class SimilarReel(Contract):
    """One result of an exact nearest-neighbour search within a profile."""

    reel_id: UUID
    distance: float


class ThumbnailRecord(Contract):
    """The single privacy-screened thumbnail kept for an accepted reel."""

    reel_id: UUID
    owner_id: str
    jpeg: bytes = Field(repr=False)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    sha256: str


class FeedbackExampleRow(Contract):
    """Feedback joined with the reel it is about, for example retrieval."""

    feedback_id: UUID
    reel_id: UUID
    label: FeedbackLabel
    comment: str | None = None
    created_at: datetime
    reel_description: str
    prior_verdict: Verdict | None = None
    prior_value_ids: tuple[str, ...] = ()
    values_revision: str | None = None
    values_sha256: str | None = None


class TimelineCursor(Contract):
    """Keyset cursor for the timeline: newest first by capture start."""

    start_ms: int
    reel_id: UUID

    def encode(self) -> str:
        """Opaque string form used in the API."""
        return f"{self.start_ms}:{self.reel_id}"

    @classmethod
    def decode(cls, value: str) -> Self:
        """Parse the opaque form. Raises ``ValueError`` when malformed."""
        start, _, reel = value.partition(":")
        return cls(start_ms=int(start), reel_id=UUID(reel))


class StorageHealth(Contract):
    """Readiness information about the database. Contains no secrets."""

    ok: bool
    backend: str
    vector_available: bool = False
    vector_version: str | None = None
    vector_dimension: int | None = None
    detail: str | None = None
