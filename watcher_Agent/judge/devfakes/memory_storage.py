"""In-memory storage for development and unit tests.

Implements the same repository interfaces as the PostgreSQL adapter and is
checked by the same contract tests. Transactions are serialized by one lock
and rolled back by restoring a snapshot. Records are immutable, so a shallow
copy of the tables is a complete snapshot.

Development only: startup refuses ``STORAGE_MODE=memory`` in production.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from judge.contracts.base import ms_to_datetime
from judge.contracts.feedback import FeedbackRecord
from judge.contracts.notification import (
    BatchStatus,
    NotificationBatch,
    NotificationPolicy,
    OutboxItem,
    UserNotificationState,
)
from judge.contracts.record import (
    EmbeddingInfo,
    EmbeddingStatus,
    EvaluationStatus,
    PendingReel,
    PendingReelRef,
    ReelAccountingView,
    ReelCompletion,
    ReelRecord,
    StageTimings,
    thumbnail_path,
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
from judge.contracts.understanding import Verdict
from judge.errors import EmbeddingDimensionError

EmbeddingKey = tuple[UUID, str]


@dataclass
class _Tables:
    sessions: dict[UUID, SessionRecord] = field(default_factory=dict)
    events: dict[UUID, SessionEvent] = field(default_factory=dict)
    receipts: dict[UUID, IngestReceipt] = field(default_factory=dict)
    reels: dict[UUID, ReelRecord] = field(default_factory=dict)
    reel_start_ms: dict[UUID, int] = field(default_factory=dict)
    reel_batch: dict[UUID, tuple[UUID, int]] = field(default_factory=dict)
    thumbnails: dict[UUID, ThumbnailRecord] = field(default_factory=dict)
    embeddings: dict[EmbeddingKey, EmbeddingRecord] = field(default_factory=dict)
    vectors: dict[EmbeddingKey, tuple[float, ...]] = field(default_factory=dict)
    feedback: dict[UUID, FeedbackRecord] = field(default_factory=dict)
    policies: dict[str, tuple[NotificationPolicy, ...]] = field(default_factory=dict)
    batches: dict[UUID, NotificationBatch] = field(default_factory=dict)
    outbox: dict[UUID, OutboxItem] = field(default_factory=dict)
    user_state: dict[str, UserNotificationState] = field(default_factory=dict)

    def snapshot(self) -> _Tables:
        return _Tables(**{name: dict(getattr(self, name)) for name in vars(self)})

    def drop_reel(self, reel_id: UUID) -> None:
        self.reels.pop(reel_id, None)
        self.reel_start_ms.pop(reel_id, None)
        self.reel_batch.pop(reel_id, None)
        self.thumbnails.pop(reel_id, None)
        for key in [k for k in self.embeddings if k[0] == reel_id]:
            self.embeddings.pop(key)
            self.vectors.pop(key, None)
        stale = [k for k, f in self.feedback.items() if f.reel_id == reel_id]
        for feedback_id in stale:
            self.feedback.pop(feedback_id)

    def drop_session(self, session_id: UUID) -> None:
        self.sessions.pop(session_id, None)
        for reel_id in [k for k, r in self.reels.items() if r.session_id == session_id]:
            self.drop_reel(reel_id)
        for table in (self.events, self.receipts, self.batches, self.outbox):
            for key in [k for k, v in table.items() if v.session_id == session_id]:
                table.pop(key)


class _Sessions:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def get(
        self, session_id: UUID, *, for_update: bool = False
    ) -> SessionRecord | None:
        return self._t.sessions.get(session_id)

    async def insert(self, record: SessionRecord) -> bool:
        if record.session_id in self._t.sessions:
            return False
        self._t.sessions[record.session_id] = record
        return True

    def _update(self, session_id: UUID, **changes: object) -> None:
        current = self._t.sessions[session_id]
        self._t.sessions[session_id] = current.model_copy(update=changes)

    async def touch(self, session_id: UUID, last_ingest_at: datetime) -> None:
        self._update(session_id, last_ingest_at=last_ingest_at)

    async def allocate_order_seq(self, session_id: UUID) -> int:
        allocated = self._t.sessions[session_id].next_order_seq
        self._update(session_id, next_order_seq=allocated + 1)
        return allocated

    async def save_accounting(
        self,
        session_id: UUID,
        *,
        cursor: int,
        judged_count: int,
        conflict_count: int,
        excluded_count: int,
    ) -> None:
        self._update(
            session_id,
            accounting_cursor=cursor,
            judged_count=judged_count,
            conflict_count=conflict_count,
            excluded_count=excluded_count,
        )

    async def set_status(
        self,
        session_id: UUID,
        status: SessionStatus,
        *,
        ended_at: datetime | None = None,
        end_reason: SessionEndReason | None = None,
    ) -> None:
        current = self._t.sessions[session_id]
        self._update(
            session_id,
            status=status,
            ended_at=ended_at or current.ended_at,
            end_reason=end_reason or current.end_reason,
        )

    async def list_idle_active(self, cutoff: datetime) -> list[SessionRecord]:
        return [
            s
            for s in self._t.sessions.values()
            if s.status is SessionStatus.ACTIVE and s.last_ingest_at <= cutoff
        ]

    async def list_by_status(self, status: SessionStatus) -> list[SessionRecord]:
        return [s for s in self._t.sessions.values() if s.status is status]

    async def add_event(self, event: SessionEvent) -> bool:
        duplicate = any(
            e.session_id == event.session_id and e.event_type == event.event_type
            for e in self._t.events.values()
        )
        if duplicate:
            return False
        self._t.events[event.event_id] = event
        return True

    async def list_events(self, owner_id: str, session_id: UUID) -> list[SessionEvent]:
        return [
            e
            for e in self._t.events.values()
            if e.owner_id == owner_id and e.session_id == session_id
        ]

    async def delete(self, owner_id: str, session_id: UUID) -> bool:
        session = self._t.sessions.get(session_id)
        if session is None or session.owner_id != owner_id:
            return False
        self._t.drop_session(session_id)
        return True

    async def delete_all(self, owner_id: str) -> int:
        owned = [k for k, s in self._t.sessions.items() if s.owner_id == owner_id]
        for session_id in owned:
            self._t.drop_session(session_id)
        return len(owned)


class _Receipts:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def get(self, event_id: UUID) -> IngestReceipt | None:
        return self._t.receipts.get(event_id)

    async def insert(self, receipt: IngestReceipt) -> bool:
        if receipt.event_id in self._t.receipts:
            return False
        self._t.receipts[receipt.event_id] = receipt
        return True

    async def prune(self, older_than: datetime) -> int:
        old = [k for k, r in self._t.receipts.items() if r.received_at < older_than]
        for key in old:
            self._t.receipts.pop(key)
        return len(old)


class _Reels:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def insert_pending(self, reel: PendingReel) -> bool:
        if reel.reel_id in self._t.reels:
            return False
        self._t.reels[reel.reel_id] = ReelRecord(
            id=reel.reel_id,
            session_id=reel.session_id,
            start=ms_to_datetime(reel.start_ms),
            end=ms_to_datetime(reel.end_ms),
            evaluation_status=EvaluationStatus.PENDING,
            owner_id=reel.owner_id,
            source_id=reel.source_id,
            order_seq=reel.order_seq,
            idempotency_key=reel.idempotency_key,
            boundary_uncertainty_ms=reel.boundary_uncertainty_ms,
            first_second_observed=reel.first_second_observed,
            segmentation_status=reel.segmentation_status,
            close_reason=reel.close_reason,
            created_at=reel.created_at,
        )
        self._t.reel_start_ms[reel.reel_id] = reel.start_ms
        return True

    async def complete(self, completion: ReelCompletion) -> bool:
        current = self._t.reels.get(completion.reel_id)
        if current is None or current.evaluation_status.is_terminal:
            return False
        changes = completion.model_dump(exclude={"reel_id", "owner_id", "session_id"})
        changes.update(
            features=completion.features,
            coverage=completion.coverage,
            value_ids=completion.value_ids,
            feedback_example_ids=completion.feedback_example_ids,
        )
        self._t.reels[completion.reel_id] = current.model_copy(update=changes)
        return True

    async def set_timings(
        self, reel_id: UUID, timings: StageTimings, latency_overrun: bool | None
    ) -> None:
        current = self._t.reels.get(reel_id)
        if current is not None:
            self._t.reels[reel_id] = current.model_copy(
                update={"timings": timings, "latency_overrun": latency_overrun}
            )

    def _decorate(self, record: ReelRecord) -> ReelRecord:
        embedding = next(
            (e for (rid, _), e in self._t.embeddings.items() if rid == record.id),
            None,
        )
        info = (
            EmbeddingInfo(
                profile_id=embedding.profile_id,
                model_id=embedding.model_id,
                dimension=embedding.dimension,
                status=embedding.status,
                attempts=embedding.attempts,
            )
            if embedding is not None
            else None
        )
        has_thumbnail = record.id in self._t.thumbnails
        url = thumbnail_path(record.id) if has_thumbnail else None
        return record.model_copy(update={"embedding": info, "thumbnail_url": url})

    async def get(self, owner_id: str, reel_id: UUID) -> ReelRecord | None:
        record = self._t.reels.get(reel_id)
        if record is None or record.owner_id != owner_id:
            return None
        return self._decorate(record)

    async def next_accounting_view(
        self, session_id: UUID, after_order_seq: int
    ) -> ReelAccountingView | None:
        later = [
            r
            for r in self._t.reels.values()
            if r.session_id == session_id and r.order_seq > after_order_seq
        ]
        if not later:
            return None
        record = min(later, key=lambda r: r.order_seq)
        return ReelAccountingView(
            reel_id=record.id,
            order_seq=record.order_seq,
            evaluation_status=record.evaluation_status,
            verdict=record.verdict,
        )

    async def assign_batch(self, reel_id: UUID, batch_id: UUID, position: int) -> None:
        self._t.reel_batch[reel_id] = (batch_id, position)

    async def list_batch_conflict_ids(self, batch_id: UUID) -> list[UUID]:
        members = sorted(
            (position, reel_id)
            for reel_id, (batch, position) in self._t.reel_batch.items()
            if batch == batch_id
        )
        return [
            reel_id
            for _, reel_id in members
            if self._t.reels[reel_id].verdict is Verdict.CONFLICT
        ]

    async def list_timeline(
        self,
        owner_id: str,
        *,
        session_id: UUID | None,
        limit: int,
        before: TimelineCursor | None,
    ) -> list[ReelRecord]:
        def key(record: ReelRecord) -> tuple[int, str]:
            return (self._t.reel_start_ms[record.id], str(record.id))

        owned = [
            r
            for r in self._t.reels.values()
            if r.owner_id == owner_id
            and (session_id is None or r.session_id == session_id)
            and (before is None or key(r) < (before.start_ms, str(before.reel_id)))
        ]
        newest_first = sorted(owned, key=key, reverse=True)
        return [self._decorate(r) for r in newest_first[:limit]]

    async def list_pending(
        self, *, created_before: datetime | None = None
    ) -> list[PendingReelRef]:
        return [
            PendingReelRef(
                reel_id=r.id,
                owner_id=r.owner_id,
                session_id=r.session_id,
                created_at=r.created_at,
            )
            for r in sorted(self._t.reels.values(), key=lambda r: r.created_at)
            if r.evaluation_status is EvaluationStatus.PENDING
            and (created_before is None or r.created_at < created_before)
        ]

    async def count_pending(self, session_id: UUID) -> int:
        return sum(
            r.session_id == session_id
            and r.evaluation_status is EvaluationStatus.PENDING
            for r in self._t.reels.values()
        )

    async def count_for_session(self, session_id: UUID) -> int:
        return sum(r.session_id == session_id for r in self._t.reels.values())

    async def delete(self, owner_id: str, reel_id: UUID) -> bool:
        record = self._t.reels.get(reel_id)
        if record is None or record.owner_id != owner_id:
            return False
        self._t.drop_reel(reel_id)
        return True


class _Thumbnails:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def put(self, thumbnail: ThumbnailRecord) -> None:
        self._t.thumbnails[thumbnail.reel_id] = thumbnail

    async def get(self, owner_id: str, reel_id: UUID) -> ThumbnailRecord | None:
        thumbnail = self._t.thumbnails.get(reel_id)
        if thumbnail is None or thumbnail.owner_id != owner_id:
            return None
        return thumbnail


def _cosine_distance(first: Sequence[float], second: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(first, second, strict=True))
    norms = math.sqrt(sum(a * a for a in first)) * math.sqrt(sum(b * b for b in second))
    return 1.0 - dot / norms if norms else 1.0


class _Embeddings:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def create_pending(
        self,
        reel_id: UUID,
        owner_id: str,
        profile: EmbeddingProfile,
        *,
        now: datetime,
    ) -> None:
        key = (reel_id, profile.profile_id)
        if key in self._t.embeddings:
            return
        self._t.embeddings[key] = EmbeddingRecord(
            reel_id=reel_id,
            owner_id=owner_id,
            profile_id=profile.profile_id,
            model_id=profile.model_id,
            dimension=profile.dimension,
            task_type=profile.task_type,
            status=EmbeddingStatus.PENDING,
            attempts=0,
            next_attempt_at=now,
        )

    async def list_due(self, now: datetime, limit: int) -> list[EmbeddingTask]:
        due = [
            e
            for e in self._t.embeddings.values()
            if e.status is EmbeddingStatus.PENDING
            and e.next_attempt_at is not None
            and e.next_attempt_at <= now
        ]
        due.sort(key=lambda e: e.next_attempt_at or now)
        tasks = []
        for record in due[:limit]:
            reel = self._t.reels[record.reel_id]
            tasks.append(
                EmbeddingTask(
                    reel_id=record.reel_id,
                    owner_id=record.owner_id,
                    profile_id=record.profile_id,
                    model_id=record.model_id,
                    dimension=record.dimension,
                    attempts=record.attempts,
                    text=f"{reel.description}\n{reel.transcript}",
                )
            )
        return tasks

    def _update(self, key: EmbeddingKey, **changes: object) -> None:
        self._t.embeddings[key] = self._t.embeddings[key].model_copy(update=changes)

    async def mark_indexed(
        self,
        reel_id: UUID,
        profile_id: str,
        vector: Sequence[float],
        *,
        indexed_at: datetime,
    ) -> None:
        key = (reel_id, profile_id)
        expected = self._t.embeddings[key].dimension
        if len(vector) != expected:
            msg = f"embedding dimension {len(vector)} does not match {expected}"
            raise EmbeddingDimensionError(msg)
        self._t.vectors[key] = tuple(vector)
        self._update(
            key,
            status=EmbeddingStatus.INDEXED,
            indexed_at=indexed_at,
            next_attempt_at=None,
            error_code=None,
        )

    async def mark_retry(
        self,
        reel_id: UUID,
        profile_id: str,
        *,
        attempts: int,
        next_attempt_at: datetime,
        error_code: str,
    ) -> None:
        self._update(
            (reel_id, profile_id),
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            error_code=error_code,
        )

    async def mark_failed(
        self, reel_id: UUID, profile_id: str, *, attempts: int, error_code: str
    ) -> None:
        self._update(
            (reel_id, profile_id),
            status=EmbeddingStatus.FAILED,
            attempts=attempts,
            next_attempt_at=None,
            error_code=error_code,
        )

    async def get(self, reel_id: UUID, profile_id: str) -> EmbeddingRecord | None:
        return self._t.embeddings.get((reel_id, profile_id))

    async def nearest(
        self, owner_id: str, profile_id: str, vector: Sequence[float], limit: int
    ) -> list[SimilarReel]:
        hits = [
            SimilarReel(reel_id=reel_id, distance=_cosine_distance(vector, stored))
            for (reel_id, profile), stored in self._t.vectors.items()
            if profile == profile_id
            and self._t.embeddings[(reel_id, profile)].owner_id == owner_id
        ]
        return sorted(hits, key=lambda hit: hit.distance)[:limit]


class _Feedback:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def insert(self, record: FeedbackRecord) -> FeedbackRecord:
        if record.client_feedback_id is not None:
            for existing in self._t.feedback.values():
                same_owner = existing.owner_id == record.owner_id
                if same_owner and existing.client_feedback_id == (
                    record.client_feedback_id
                ):
                    return existing
        self._t.feedback[record.feedback_id] = record
        return record

    async def list_example_rows(
        self, owner_id: str, values_sha256: str, limit: int
    ) -> list[FeedbackExampleRow]:
        newest: dict[UUID, FeedbackRecord] = {}
        for record in self._t.feedback.values():
            reel = self._t.reels.get(record.reel_id)
            usable = (
                record.owner_id == owner_id
                and reel is not None
                and reel.evaluation_status is EvaluationStatus.JUDGED
                and reel.values_sha256 == values_sha256
            )
            current = newest.get(record.reel_id)
            if usable and (current is None or record.created_at > current.created_at):
                newest[record.reel_id] = record
        ordered = sorted(newest.values(), key=lambda r: r.created_at, reverse=True)
        return [self._row(record) for record in ordered[:limit]]

    def _row(self, record: FeedbackRecord) -> FeedbackExampleRow:
        reel = self._t.reels[record.reel_id]
        return FeedbackExampleRow(
            feedback_id=record.feedback_id,
            reel_id=record.reel_id,
            label=record.label,
            comment=record.comment,
            created_at=record.created_at,
            reel_description=reel.description,
            prior_verdict=reel.verdict,
            prior_value_ids=reel.value_ids,
            values_revision=reel.values_revision,
            values_sha256=reel.values_sha256,
        )

    async def list_for_owner(self, owner_id: str, limit: int) -> list[FeedbackRecord]:
        owned = [f for f in self._t.feedback.values() if f.owner_id == owner_id]
        return sorted(owned, key=lambda f: f.created_at, reverse=True)[:limit]


class _Policy:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def get_current(self, owner_id: str) -> NotificationPolicy | None:
        versions = self._t.policies.get(owner_id, ())
        return versions[-1] if versions else None

    async def insert_next(
        self,
        owner_id: str,
        *,
        batch_size: int,
        minimum_conflicts: int,
        cooldown_seconds: int,
        updated_at: datetime,
    ) -> NotificationPolicy:
        versions = self._t.policies.get(owner_id, ())
        policy = NotificationPolicy(
            version=len(versions) + 1,
            batch_size=batch_size,
            minimum_conflicts=minimum_conflicts,
            cooldown_seconds=cooldown_seconds,
            updated_at=updated_at,
        )
        self._t.policies[owner_id] = (*versions, policy)
        return policy

    async def delete_all(self, owner_id: str) -> None:
        self._t.policies.pop(owner_id, None)


class _Notifications:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def get_open_batch(self, session_id: UUID) -> NotificationBatch | None:
        for batch in self._t.batches.values():
            if batch.session_id == session_id and batch.status is BatchStatus.OPEN:
                return batch
        return None

    async def count_batches(self, session_id: UUID) -> int:
        return sum(b.session_id == session_id for b in self._t.batches.values())

    async def insert_batch(self, batch: NotificationBatch) -> None:
        self._t.batches[batch.batch_id] = batch

    async def save_batch(self, batch: NotificationBatch) -> None:
        self._t.batches[batch.batch_id] = batch

    async def list_batches(
        self, owner_id: str, session_id: UUID
    ) -> list[NotificationBatch]:
        owned = [
            b
            for b in self._t.batches.values()
            if b.owner_id == owner_id and b.session_id == session_id
        ]
        return sorted(owned, key=lambda b: b.batch_index)

    async def lock_user_state(self, owner_id: str) -> UserNotificationState:
        state = self._t.user_state.get(owner_id)
        if state is None:
            state = UserNotificationState(owner_id=owner_id)
            self._t.user_state[owner_id] = state
        return state

    async def set_last_dispatch(self, owner_id: str, at: datetime) -> None:
        self._t.user_state[owner_id] = UserNotificationState(
            owner_id=owner_id, last_dispatch_at=at
        )

    async def insert_outbox(self, item: OutboxItem) -> None:
        self._t.outbox[item.notification_id] = item

    async def save_outbox(self, item: OutboxItem) -> None:
        self._t.outbox[item.notification_id] = item

    async def get_outbox(
        self, owner_id: str, notification_id: UUID
    ) -> OutboxItem | None:
        item = self._t.outbox.get(notification_id)
        if item is None or item.owner_id != owner_id:
            return None
        return item

    async def list_outbox(self, owner_id: str) -> list[OutboxItem]:
        owned = [i for i in self._t.outbox.values() if i.owner_id == owner_id]
        return sorted(owned, key=lambda i: i.created_at)

    async def list_undelivered(self, owner_id: str) -> list[OutboxItem]:
        return [i for i in await self.list_outbox(owner_id) if i.status.is_undelivered]

    async def delete_all(self, owner_id: str) -> None:
        self._t.user_state.pop(owner_id, None)
        for key in [k for k, i in self._t.outbox.items() if i.owner_id == owner_id]:
            self._t.outbox.pop(key)


class _MemoryTx:
    """Repositories bound to the working copy of the tables."""

    def __init__(self, tables: _Tables) -> None:
        self.sessions = _Sessions(tables)
        self.receipts = _Receipts(tables)
        self.reels = _Reels(tables)
        self.thumbnails = _Thumbnails(tables)
        self.embeddings = _Embeddings(tables)
        self.feedback = _Feedback(tables)
        self.policy = _Policy(tables)
        self.notifications = _Notifications(tables)


class MemoryStorage:
    """Development storage with snapshot-based transactions."""

    def __init__(self) -> None:
        self._tables = _Tables()
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_MemoryTx]:
        async with self._lock:
            working = self._tables.snapshot()
            yield _MemoryTx(working)
            # Reached only when the block did not raise: commit.
            self._tables = working

    async def health(self) -> StorageHealth:
        return StorageHealth(
            ok=True,
            backend="memory",
            vector_available=True,
            detail="development storage; nothing is persisted",
        )

    async def close(self) -> None:
        return None
