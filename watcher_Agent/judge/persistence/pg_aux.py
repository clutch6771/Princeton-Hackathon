"""PostgreSQL repositories: embeddings, feedback, policy, notifications."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from judge.contracts.feedback import FeedbackRecord
from judge.contracts.notification import (
    NotificationBatch,
    NotificationPolicy,
    OutboxItem,
    UserNotificationState,
)
from judge.contracts.storage import (
    EmbeddingProfile,
    EmbeddingRecord,
    EmbeddingTask,
    FeedbackExampleRow,
    SimilarReel,
)
from judge.errors import EmbeddingDimensionError, StorageUnavailableError
from judge.persistence.pg_core import Conn, execute, fetch_all, fetch_one

_EMBEDDING_COLUMNS = """
    reel_id, owner_id, profile_id, model_id, dimension, task_type, status,
    attempts, next_attempt_at, error_code, indexed_at
"""
_BATCH_COLUMNS = """
    batch_id, owner_id, session_id, batch_index, policy_version, batch_size,
    minimum_conflicts, cooldown_seconds, status, judged_count, conflict_count,
    excluded_count, outcome, opened_at, evaluated_at
"""
_OUTBOX_COLUMNS = """
    notification_id, owner_id, session_id, batch_id, title, body, reel_ids,
    cooldown_seconds, status, created_at, expires_at, lease_until,
    dispatch_attempts, dispatched_at, acknowledged_at, closed_reason
"""
_POLICY_COLUMNS = "version, batch_size, minimum_conflicts, cooldown_seconds, updated_at"


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


class PgEmbeddings:
    """Indexing metadata always; vectors only when pgvector is enabled."""

    def __init__(self, conn: Conn, *, vector_enabled: bool) -> None:
        self._conn = conn
        self._vector_enabled = vector_enabled

    def _require_vectors(self) -> None:
        if not self._vector_enabled:
            msg = "the vector store is not available on this database"
            raise StorageUnavailableError(msg)

    async def create_pending(
        self,
        reel_id: UUID,
        owner_id: str,
        profile: EmbeddingProfile,
        *,
        now: datetime,
    ) -> None:
        await execute(
            self._conn,
            """
            INSERT INTO judge.reel_embeddings (
                reel_id, profile_id, owner_id, model_id, dimension, task_type,
                status, attempts, next_attempt_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', 0, %s, %s)
            ON CONFLICT (reel_id, profile_id) DO NOTHING
            """,
            (
                reel_id,
                profile.profile_id,
                owner_id,
                profile.model_id,
                profile.dimension,
                profile.task_type,
                now,
                now,
            ),
        )

    async def list_due(self, now: datetime, limit: int) -> list[EmbeddingTask]:
        rows = await fetch_all(
            self._conn,
            """
            SELECT e.reel_id, e.owner_id, e.profile_id, e.model_id, e.dimension,
                   e.attempts, r.description || E'\\n' || r.transcript AS text
            FROM judge.reel_embeddings e
            JOIN judge.reels r ON r.reel_id = e.reel_id
            WHERE e.status = 'pending' AND e.next_attempt_at <= %s
            ORDER BY e.next_attempt_at
            LIMIT %s
            """,
            (now, limit),
        )
        return [EmbeddingTask.model_validate(row) for row in rows]

    async def mark_indexed(
        self,
        reel_id: UUID,
        profile_id: str,
        vector: Sequence[float],
        *,
        indexed_at: datetime,
    ) -> None:
        self._require_vectors()
        record = await self.get(reel_id, profile_id)
        if record is None:
            msg = "no embedding metadata exists for this reel and profile"
            raise LookupError(msg)
        if len(vector) != record.dimension:
            msg = (
                f"embedding dimension {len(vector)} does not match the pinned "
                f"dimension {record.dimension}"
            )
            raise EmbeddingDimensionError(msg)
        await execute(
            self._conn,
            """
            INSERT INTO judge.reel_embedding_vectors
                (reel_id, profile_id, owner_id, embedding)
            VALUES (%s, %s, %s, %s::vector)
            ON CONFLICT (reel_id, profile_id)
            DO UPDATE SET embedding = EXCLUDED.embedding
            """,
            (reel_id, profile_id, record.owner_id, _vector_literal(vector)),
        )
        await execute(
            self._conn,
            """
            UPDATE judge.reel_embeddings
            SET status = 'indexed', indexed_at = %s, next_attempt_at = NULL,
                error_code = NULL
            WHERE reel_id = %s AND profile_id = %s
            """,
            (indexed_at, reel_id, profile_id),
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
        await execute(
            self._conn,
            """
            UPDATE judge.reel_embeddings
            SET attempts = %s, next_attempt_at = %s, error_code = %s
            WHERE reel_id = %s AND profile_id = %s
            """,
            (attempts, next_attempt_at, error_code, reel_id, profile_id),
        )

    async def mark_failed(
        self, reel_id: UUID, profile_id: str, *, attempts: int, error_code: str
    ) -> None:
        await execute(
            self._conn,
            """
            UPDATE judge.reel_embeddings
            SET status = 'failed', attempts = %s, next_attempt_at = NULL,
                error_code = %s
            WHERE reel_id = %s AND profile_id = %s
            """,
            (attempts, error_code, reel_id, profile_id),
        )

    async def get(self, reel_id: UUID, profile_id: str) -> EmbeddingRecord | None:
        row = await fetch_one(
            self._conn,
            f"SELECT {_EMBEDDING_COLUMNS} FROM judge.reel_embeddings "  # noqa: S608  # nosec B608 - constant column list
            "WHERE reel_id = %s AND profile_id = %s",
            (reel_id, profile_id),
        )
        return EmbeddingRecord.model_validate(row) if row else None

    async def nearest(
        self, owner_id: str, profile_id: str, vector: Sequence[float], limit: int
    ) -> list[SimilarReel]:
        self._require_vectors()
        rows = await fetch_all(
            self._conn,
            """
            SELECT reel_id, embedding <=> %s::vector AS distance
            FROM judge.reel_embedding_vectors
            WHERE owner_id = %s AND profile_id = %s
            ORDER BY distance
            LIMIT %s
            """,
            (_vector_literal(vector), owner_id, profile_id, limit),
        )
        return [SimilarReel.model_validate(row) for row in rows]


class PgFeedback:
    """Explicit feedback. Storage only: no scheduler, no values edit."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    async def insert(self, record: FeedbackRecord) -> FeedbackRecord:
        row = await fetch_one(
            self._conn,
            """
            INSERT INTO judge.feedback (
                feedback_id, owner_id, reel_id, label, score, comment, source,
                client_feedback_id, created_at
            ) VALUES (
                %(feedback_id)s, %(owner_id)s, %(reel_id)s, %(label)s, %(score)s,
                %(comment)s, %(source)s, %(client_feedback_id)s, %(created_at)s
            )
            ON CONFLICT (owner_id, client_feedback_id) DO NOTHING
            RETURNING feedback_id
            """,
            record.model_dump(mode="python") | {"label": record.label.value},
        )
        if row is not None:
            return record
        existing = await fetch_one(
            self._conn,
            """
            SELECT feedback_id, owner_id, reel_id, label, score, comment, source,
                   client_feedback_id, created_at
            FROM judge.feedback
            WHERE owner_id = %s AND client_feedback_id = %s
            """,
            (record.owner_id, record.client_feedback_id),
        )
        return FeedbackRecord.model_validate(existing)

    async def list_example_rows(
        self, owner_id: str, values_sha256: str, limit: int
    ) -> list[FeedbackExampleRow]:
        rows = await fetch_all(
            self._conn,
            """
            SELECT * FROM (
                SELECT DISTINCT ON (f.reel_id)
                    f.feedback_id, f.reel_id, f.label, f.comment, f.created_at,
                    r.description AS reel_description, r.verdict AS prior_verdict,
                    r.value_ids AS prior_value_ids, r.values_revision,
                    r.values_sha256
                FROM judge.feedback f
                JOIN judge.reels r ON r.reel_id = f.reel_id
                WHERE f.owner_id = %s
                  AND r.evaluation_status = 'judged'
                  AND r.values_sha256 = %s
                ORDER BY f.reel_id, f.created_at DESC
            ) latest
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (owner_id, values_sha256, limit),
        )
        return [FeedbackExampleRow.model_validate(row) for row in rows]

    async def list_for_owner(self, owner_id: str, limit: int) -> list[FeedbackRecord]:
        rows = await fetch_all(
            self._conn,
            """
            SELECT feedback_id, owner_id, reel_id, label, score, comment, source,
                   client_feedback_id, created_at
            FROM judge.feedback WHERE owner_id = %s
            ORDER BY created_at DESC LIMIT %s
            """,
            (owner_id, limit),
        )
        return [FeedbackRecord.model_validate(row) for row in rows]


class PgPolicy:
    """Versioned notification policy."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    async def get_current(self, owner_id: str) -> NotificationPolicy | None:
        row = await fetch_one(
            self._conn,
            f"SELECT {_POLICY_COLUMNS} FROM judge.notification_policy "  # noqa: S608  # nosec B608 - constant column list
            "WHERE owner_id = %s ORDER BY version DESC LIMIT 1",
            (owner_id,),
        )
        return NotificationPolicy.model_validate(row) if row else None

    async def insert_next(
        self,
        owner_id: str,
        *,
        batch_size: int,
        minimum_conflicts: int,
        cooldown_seconds: int,
        updated_at: datetime,
    ) -> NotificationPolicy:
        row = await fetch_one(
            self._conn,
            f"""
            INSERT INTO judge.notification_policy (
                owner_id, version, batch_size, minimum_conflicts,
                cooldown_seconds, updated_at
            )
            SELECT %s, COALESCE(MAX(version), 0) + 1, %s, %s, %s, %s
            FROM judge.notification_policy WHERE owner_id = %s
            RETURNING {_POLICY_COLUMNS}
            """,  # noqa: S608  # nosec B608 - constant column list
            (
                owner_id,
                batch_size,
                minimum_conflicts,
                cooldown_seconds,
                updated_at,
                owner_id,
            ),
        )
        return NotificationPolicy.model_validate(row)

    async def delete_all(self, owner_id: str) -> None:
        await execute(
            self._conn,
            "DELETE FROM judge.notification_policy WHERE owner_id = %s",
            (owner_id,),
        )


def _batch_params(batch: NotificationBatch) -> dict[str, Any]:
    return batch.model_dump(mode="python") | {
        "status": batch.status.value,
        "outcome": batch.outcome.value if batch.outcome else None,
    }


def _outbox_params(item: OutboxItem) -> dict[str, Any]:
    return item.model_dump(mode="python") | {
        "status": item.status.value,
        "reel_ids": list(item.reel_ids),
    }


def _outbox(row: dict[str, Any]) -> OutboxItem:
    return OutboxItem.model_validate(row | {"reel_ids": tuple(row["reel_ids"])})


class PgNotifications:
    """Batches, outbox and the per-user cooldown anchor."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    async def get_open_batch(self, session_id: UUID) -> NotificationBatch | None:
        row = await fetch_one(
            self._conn,
            f"SELECT {_BATCH_COLUMNS} FROM judge.notification_batches "  # noqa: S608  # nosec B608 - constant column list
            "WHERE session_id = %s AND status = 'open'",
            (session_id,),
        )
        return NotificationBatch.model_validate(row) if row else None

    async def count_batches(self, session_id: UUID) -> int:
        row = await fetch_one(
            self._conn,
            "SELECT count(*) AS n FROM judge.notification_batches "
            "WHERE session_id = %s",
            (session_id,),
        )
        return int(row["n"]) if row else 0

    async def insert_batch(self, batch: NotificationBatch) -> None:
        await execute(
            self._conn,
            f"""
            INSERT INTO judge.notification_batches ({_BATCH_COLUMNS})
            VALUES (
                %(batch_id)s, %(owner_id)s, %(session_id)s, %(batch_index)s,
                %(policy_version)s, %(batch_size)s, %(minimum_conflicts)s,
                %(cooldown_seconds)s, %(status)s, %(judged_count)s,
                %(conflict_count)s, %(excluded_count)s, %(outcome)s,
                %(opened_at)s, %(evaluated_at)s
            )
            """,  # noqa: S608  # nosec B608 - constant column list
            _batch_params(batch),
        )

    async def save_batch(self, batch: NotificationBatch) -> None:
        await execute(
            self._conn,
            """
            UPDATE judge.notification_batches
            SET status = %(status)s, judged_count = %(judged_count)s,
                conflict_count = %(conflict_count)s,
                excluded_count = %(excluded_count)s, outcome = %(outcome)s,
                evaluated_at = %(evaluated_at)s
            WHERE batch_id = %(batch_id)s
            """,
            _batch_params(batch),
        )

    async def list_batches(
        self, owner_id: str, session_id: UUID
    ) -> list[NotificationBatch]:
        rows = await fetch_all(
            self._conn,
            f"SELECT {_BATCH_COLUMNS} FROM judge.notification_batches "  # noqa: S608  # nosec B608 - constant column list
            "WHERE owner_id = %s AND session_id = %s ORDER BY batch_index",
            (owner_id, session_id),
        )
        return [NotificationBatch.model_validate(row) for row in rows]

    async def lock_user_state(self, owner_id: str) -> UserNotificationState:
        await execute(
            self._conn,
            "INSERT INTO judge.user_notification_state (owner_id) VALUES (%s) "
            "ON CONFLICT (owner_id) DO NOTHING",
            (owner_id,),
        )
        row = await fetch_one(
            self._conn,
            "SELECT owner_id, last_dispatch_at FROM judge.user_notification_state "
            "WHERE owner_id = %s FOR UPDATE",
            (owner_id,),
        )
        return UserNotificationState.model_validate(row)

    async def set_last_dispatch(self, owner_id: str, at: datetime) -> None:
        await execute(
            self._conn,
            """
            INSERT INTO judge.user_notification_state (owner_id, last_dispatch_at)
            VALUES (%s, %s)
            ON CONFLICT (owner_id)
            DO UPDATE SET last_dispatch_at = EXCLUDED.last_dispatch_at
            """,
            (owner_id, at),
        )

    async def insert_outbox(self, item: OutboxItem) -> None:
        await execute(
            self._conn,
            f"""
            INSERT INTO judge.notification_outbox ({_OUTBOX_COLUMNS})
            VALUES (
                %(notification_id)s, %(owner_id)s, %(session_id)s, %(batch_id)s,
                %(title)s, %(body)s, %(reel_ids)s, %(cooldown_seconds)s,
                %(status)s, %(created_at)s, %(expires_at)s, %(lease_until)s,
                %(dispatch_attempts)s, %(dispatched_at)s, %(acknowledged_at)s,
                %(closed_reason)s
            )
            """,  # noqa: S608  # nosec B608 - constant column list
            _outbox_params(item),
        )

    async def save_outbox(self, item: OutboxItem) -> None:
        await execute(
            self._conn,
            """
            UPDATE judge.notification_outbox
            SET status = %(status)s, lease_until = %(lease_until)s,
                dispatch_attempts = %(dispatch_attempts)s,
                dispatched_at = %(dispatched_at)s,
                acknowledged_at = %(acknowledged_at)s,
                closed_reason = %(closed_reason)s
            WHERE notification_id = %(notification_id)s
            """,
            _outbox_params(item),
        )

    async def get_outbox(
        self, owner_id: str, notification_id: UUID
    ) -> OutboxItem | None:
        row = await fetch_one(
            self._conn,
            f"SELECT {_OUTBOX_COLUMNS} FROM judge.notification_outbox "  # noqa: S608  # nosec B608 - constant column list
            "WHERE owner_id = %s AND notification_id = %s",
            (owner_id, notification_id),
        )
        return _outbox(row) if row else None

    async def list_undelivered(self, owner_id: str) -> list[OutboxItem]:
        rows = await fetch_all(
            self._conn,
            f"SELECT {_OUTBOX_COLUMNS} FROM judge.notification_outbox "  # noqa: S608  # nosec B608 - constant column list
            "WHERE owner_id = %s AND status IN ('pending', 'leased') "
            "ORDER BY created_at",
            (owner_id,),
        )
        return [_outbox(row) for row in rows]

    async def list_outbox(self, owner_id: str) -> list[OutboxItem]:
        rows = await fetch_all(
            self._conn,
            f"SELECT {_OUTBOX_COLUMNS} FROM judge.notification_outbox "  # noqa: S608  # nosec B608 - constant column list
            "WHERE owner_id = %s ORDER BY created_at",
            (owner_id,),
        )
        return [_outbox(row) for row in rows]

    async def delete_all(self, owner_id: str) -> None:
        await execute(
            self._conn,
            "DELETE FROM judge.notification_outbox WHERE owner_id = %s",
            (owner_id,),
        )
        await execute(
            self._conn,
            "DELETE FROM judge.user_notification_state WHERE owner_id = %s",
            (owner_id,),
        )
