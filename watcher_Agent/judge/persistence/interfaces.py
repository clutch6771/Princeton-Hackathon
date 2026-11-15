"""Typed repository interfaces behind which storage is swappable.

Repositories are deliberately primitive (load, insert, save). Ordering and
lifecycle rules live in services, so the in-memory development
storage and the PostgreSQL adapter cannot drift apart in behaviour.

Every method runs inside the transaction that produced the ``StorageTx``.
Errors: adapters raise ``StorageUnavailableError`` when the database cannot be
reached; constraint violations propagate as adapter exceptions and roll the
transaction back.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from judge.contracts.feedback import FeedbackRecord
from judge.contracts.record import (
    PendingReel,
    PendingReelRef,
    ReelAccountingView,
    ReelCompletion,
    ReelRecord,
    StageTimings,
)
from judge.contracts.session import (
    IngestReceipt,
    SessionEndReason,
    SessionEvent,
    SessionRecord,
    SessionStatus,
)
from judge.contracts.storage import (
    EmbeddingProfile,
    EmbeddingRecord,
    EmbeddingTask,
    FeedbackExampleRow,
    SimilarReel,
    StorageHealth,
    ThumbnailRecord,
    TimelineCursor,
)


class SessionRepository(Protocol):
    """Sessions: lifecycle, activity, ordering cursor, counts, events."""

    async def get(
        self, session_id: UUID, *, for_update: bool = False
    ) -> SessionRecord | None: ...

    async def insert(self, record: SessionRecord) -> bool:
        """Insert; False when the session already exists."""
        ...

    async def touch(self, session_id: UUID, last_ingest_at: datetime) -> None: ...

    async def allocate_order_seq(self, session_id: UUID) -> int:
        """Return the next capture-order number and advance the counter."""
        ...

    async def save_accounting(
        self,
        session_id: UUID,
        *,
        cursor: int,
        judged_count: int,
        conflict_count: int,
        excluded_count: int,
    ) -> None: ...

    async def set_status(
        self,
        session_id: UUID,
        status: SessionStatus,
        *,
        ended_at: datetime | None = None,
        end_reason: SessionEndReason | None = None,
    ) -> None: ...

    async def list_idle_active(self, cutoff: datetime) -> list[SessionRecord]:
        """Active sessions whose last accepted ingest is at or before cutoff."""
        ...

    async def list_by_status(self, status: SessionStatus) -> list[SessionRecord]: ...

    async def add_event(self, event: SessionEvent) -> bool:
        """Insert a lifecycle event once per (session, type)."""
        ...

    async def list_events(
        self, owner_id: str, session_id: UUID
    ) -> list[SessionEvent]: ...

    async def delete(self, owner_id: str, session_id: UUID) -> bool: ...

    async def delete_all(self, owner_id: str) -> int: ...


class ReceiptRepository(Protocol):
    """Ingest receipts: idempotency and metadata only, never payloads."""

    async def get(self, event_id: UUID) -> IngestReceipt | None: ...

    async def insert(self, receipt: IngestReceipt) -> bool:
        """Insert; False when the event id was already recorded."""
        ...

    async def prune(self, older_than: datetime) -> int: ...


class ReelRepository(Protocol):
    """Reels: the PRD record plus operational and provenance fields."""

    async def insert_pending(self, reel: PendingReel) -> bool:
        """Insert a pending reel; False when it already exists."""
        ...

    async def complete(self, completion: ReelCompletion) -> bool:
        """Move pending to a terminal state exactly once; False otherwise."""
        ...

    async def set_timings(
        self, reel_id: UUID, timings: StageTimings, latency_overrun: bool | None
    ) -> None: ...

    async def get(self, owner_id: str, reel_id: UUID) -> ReelRecord | None: ...

    async def next_accounting_view(
        self, session_id: UUID, after_order_seq: int
    ) -> ReelAccountingView | None:
        """The session's first reel, in capture order, after a sequence number."""
        ...

    async def list_timeline(
        self,
        owner_id: str,
        *,
        session_id: UUID | None,
        limit: int,
        before: TimelineCursor | None,
    ) -> list[ReelRecord]: ...

    async def list_pending(
        self, *, created_before: datetime | None = None
    ) -> list[PendingReelRef]: ...

    async def count_pending(self, session_id: UUID) -> int: ...

    async def count_for_session(self, session_id: UUID) -> int: ...

    async def delete(self, owner_id: str, reel_id: UUID) -> bool: ...


class ThumbnailRepository(Protocol):
    """At most one sanitized, bounded JPEG per accepted reel."""

    async def put(self, thumbnail: ThumbnailRecord) -> None: ...

    async def get(self, owner_id: str, reel_id: UUID) -> ThumbnailRecord | None: ...


class EmbeddingRepository(Protocol):
    """Versioned vectors and indexing metadata."""

    async def create_pending(
        self,
        reel_id: UUID,
        owner_id: str,
        profile: EmbeddingProfile,
        *,
        now: datetime,
    ) -> None: ...

    async def list_due(self, now: datetime, limit: int) -> list[EmbeddingTask]: ...

    async def mark_indexed(
        self,
        reel_id: UUID,
        profile_id: str,
        vector: Sequence[float],
        *,
        indexed_at: datetime,
    ) -> None:
        """Idempotent upsert of the vector; rejects a wrong dimension."""
        ...

    async def mark_retry(
        self,
        reel_id: UUID,
        profile_id: str,
        *,
        attempts: int,
        next_attempt_at: datetime,
        error_code: str,
    ) -> None: ...

    async def mark_failed(
        self, reel_id: UUID, profile_id: str, *, attempts: int, error_code: str
    ) -> None: ...

    async def get(self, reel_id: UUID, profile_id: str) -> EmbeddingRecord | None: ...

    async def nearest(
        self, owner_id: str, profile_id: str, vector: Sequence[float], limit: int
    ) -> list[SimilarReel]:
        """Exact search inside one profile; never across vector spaces."""
        ...


class FeedbackRepository(Protocol):
    """Explicit feedback, owned by the user who gave it."""

    async def insert(self, record: FeedbackRecord) -> FeedbackRecord:
        """Insert; returns the existing record for a repeated client id."""
        ...

    async def list_example_rows(
        self, owner_id: str, values_sha256: str, limit: int
    ) -> list[FeedbackExampleRow]:
        """Newest feedback first, one row per reel, judged reels only."""
        ...

    async def list_for_owner(
        self, owner_id: str, limit: int
    ) -> list[FeedbackRecord]: ...


class StorageTx(Protocol):
    """Repositories bound to one open transaction."""

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def receipts(self) -> ReceiptRepository: ...

    @property
    def reels(self) -> ReelRepository: ...

    @property
    def thumbnails(self) -> ThumbnailRepository: ...

    @property
    def embeddings(self) -> EmbeddingRepository: ...

    @property
    def feedback(self) -> FeedbackRepository: ...


class Storage(Protocol):
    """The persistent application database."""

    def transaction(self) -> AbstractAsyncContextManager[StorageTx]:
        """Open a transaction; commit on success, roll back on error."""
        ...

    async def health(self) -> StorageHealth: ...

    async def close(self) -> None: ...
