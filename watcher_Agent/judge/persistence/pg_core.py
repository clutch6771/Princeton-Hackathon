"""PostgreSQL repositories: sessions, receipts, reels, thumbnails.

All SQL is static and parameterized. Every owner-facing read filters by
``owner_id``. No statement here can store raw media other than the single
bounded thumbnail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from judge.contracts.base import ms_to_datetime
from judge.contracts.record import (
    EmbeddingInfo,
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
from judge.contracts.storage import ThumbnailRecord, TimelineCursor

Conn = AsyncConnection[dict[str, Any]]

_REEL_SELECT = """
SELECT r.*, e.profile_id AS e_profile_id, e.model_id AS e_model_id,
       e.dimension AS e_dimension, e.status AS e_status, e.attempts AS e_attempts
FROM judge.reels r
LEFT JOIN LATERAL (
    SELECT profile_id, model_id, dimension, status, attempts
    FROM judge.reel_embeddings
    WHERE reel_id = r.reel_id
    ORDER BY created_at DESC
    LIMIT 1
) e ON true
"""
_REEL_COLUMNS = (
    "session_id",
    "transcript",
    "on_screen_text",
    "description",
    "features",
    "verdict",
    "confidence",
    "reason",
    "evaluation_status",
    "owner_id",
    "source_id",
    "order_seq",
    "idempotency_key",
    "boundary_uncertainty_ms",
    "first_second_observed",
    "segmentation_status",
    "close_reason",
    "coverage",
    "audio_status",
    "transcript_status",
    "values_revision",
    "values_sha256",
    "retrieval_mode",
    "model_id",
    "prompt_version",
    "understanding_attempts",
    "timings",
    "latency_overrun",
    "error_code",
    "created_at",
    "completed_at",
)


async def fetch_one(
    conn: Conn, query: str, params: Any = None
) -> dict[str, Any] | None:
    """Run a query and return its first row, if any."""
    cursor = await conn.execute(query, params)
    return await cursor.fetchone()


async def fetch_all(conn: Conn, query: str, params: Any = None) -> list[dict[str, Any]]:
    """Run a query and return all rows."""
    cursor = await conn.execute(query, params)
    return await cursor.fetchall()


async def execute(conn: Conn, query: str, params: Any = None) -> int:
    """Run a statement and return the affected row count."""
    cursor = await conn.execute(query, params)
    return cursor.rowcount


def _json(model: Any) -> Jsonb | None:
    return None if model is None else Jsonb(model.model_dump(mode="json"))


def _enum(value: Any) -> str | None:
    return None if value is None else value.value


def reel_record(row: dict[str, Any]) -> ReelRecord:
    """Map a ``_REEL_SELECT`` row to the reel contract."""
    embedding = None
    if row["e_profile_id"] is not None:
        embedding = EmbeddingInfo(
            profile_id=row["e_profile_id"],
            model_id=row["e_model_id"],
            dimension=row["e_dimension"],
            status=row["e_status"],
            attempts=row["e_attempts"],
        )
    reel_id = row["reel_id"]
    payload: dict[str, Any] = {name: row[name] for name in _REEL_COLUMNS}
    payload.update(
        id=reel_id,
        start=ms_to_datetime(row["start_ms"]),
        end=ms_to_datetime(row["end_ms"]),
        thumbnail_url=thumbnail_path(reel_id) if row["has_thumbnail"] else None,
        value_ids=tuple(row["value_ids"]),
        feedback_example_ids=tuple(row["feedback_example_ids"]),
        embedding=embedding,
    )
    return ReelRecord.model_validate(payload)


class PgSessions:
    """Sessions, their accounting cursor, and lifecycle events."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    async def get(
        self, session_id: UUID, *, for_update: bool = False
    ) -> SessionRecord | None:
        query = "SELECT * FROM judge.sessions WHERE session_id = %s"
        if for_update:
            query += " FOR UPDATE"
        row = await fetch_one(self._conn, query, (session_id,))
        return SessionRecord.model_validate(row) if row else None

    async def insert(self, record: SessionRecord) -> bool:
        count = await execute(
            self._conn,
            """
            INSERT INTO judge.sessions (
                session_id, owner_id, source_id, status, started_at,
                last_ingest_at, ended_at, end_reason, next_order_seq,
                accounting_cursor, judged_count, conflict_count, excluded_count
            ) VALUES (
                %(session_id)s, %(owner_id)s, %(source_id)s, %(status)s,
                %(started_at)s, %(last_ingest_at)s, %(ended_at)s, %(end_reason)s,
                %(next_order_seq)s, %(accounting_cursor)s, %(judged_count)s,
                %(conflict_count)s, %(excluded_count)s
            ) ON CONFLICT (session_id) DO NOTHING
            """,
            record.model_dump(mode="python")
            | {"status": record.status.value, "end_reason": _enum(record.end_reason)},
        )
        return count == 1

    async def touch(self, session_id: UUID, last_ingest_at: datetime) -> None:
        await execute(
            self._conn,
            "UPDATE judge.sessions SET last_ingest_at = %s WHERE session_id = %s",
            (last_ingest_at, session_id),
        )

    async def allocate_order_seq(self, session_id: UUID) -> int:
        row = await fetch_one(
            self._conn,
            """
            UPDATE judge.sessions SET next_order_seq = next_order_seq + 1
            WHERE session_id = %s
            RETURNING next_order_seq - 1 AS allocated
            """,
            (session_id,),
        )
        if row is None:
            msg = "cannot allocate an order sequence for an unknown session"
            raise LookupError(msg)
        return int(row["allocated"])

    async def save_accounting(
        self,
        session_id: UUID,
        *,
        cursor: int,
        judged_count: int,
        conflict_count: int,
        excluded_count: int,
    ) -> None:
        await execute(
            self._conn,
            """
            UPDATE judge.sessions
            SET accounting_cursor = %s, judged_count = %s,
                conflict_count = %s, excluded_count = %s
            WHERE session_id = %s
            """,
            (cursor, judged_count, conflict_count, excluded_count, session_id),
        )

    async def set_status(
        self,
        session_id: UUID,
        status: SessionStatus,
        *,
        ended_at: datetime | None = None,
        end_reason: SessionEndReason | None = None,
    ) -> None:
        await execute(
            self._conn,
            """
            UPDATE judge.sessions
            SET status = %s,
                ended_at = COALESCE(%s, ended_at),
                end_reason = COALESCE(%s, end_reason)
            WHERE session_id = %s
            """,
            (status.value, ended_at, _enum(end_reason), session_id),
        )

    async def list_idle_active(self, cutoff: datetime) -> list[SessionRecord]:
        rows = await fetch_all(
            self._conn,
            """
            SELECT * FROM judge.sessions
            WHERE status = 'active' AND last_ingest_at <= %s
            ORDER BY last_ingest_at
            """,
            (cutoff,),
        )
        return [SessionRecord.model_validate(row) for row in rows]

    async def list_by_status(self, status: SessionStatus) -> list[SessionRecord]:
        rows = await fetch_all(
            self._conn,
            "SELECT * FROM judge.sessions WHERE status = %s ORDER BY started_at",
            (status.value,),
        )
        return [SessionRecord.model_validate(row) for row in rows]

    async def add_event(self, event: SessionEvent) -> bool:
        count = await execute(
            self._conn,
            """
            INSERT INTO judge.session_events
                (event_id, session_id, owner_id, event_type, payload, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id, event_type) DO NOTHING
            """,
            (
                event.event_id,
                event.session_id,
                event.owner_id,
                event.event_type,
                _json(event.payload),
                event.created_at,
            ),
        )
        return count == 1

    async def list_events(self, owner_id: str, session_id: UUID) -> list[SessionEvent]:
        rows = await fetch_all(
            self._conn,
            """
            SELECT event_id, session_id, owner_id, event_type, payload, created_at
            FROM judge.session_events
            WHERE owner_id = %s AND session_id = %s
            ORDER BY created_at
            """,
            (owner_id, session_id),
        )
        return [SessionEvent.model_validate(row) for row in rows]

    async def delete(self, owner_id: str, session_id: UUID) -> bool:
        count = await execute(
            self._conn,
            "DELETE FROM judge.sessions WHERE owner_id = %s AND session_id = %s",
            (owner_id, session_id),
        )
        return count == 1

    async def delete_all(self, owner_id: str) -> int:
        return await execute(
            self._conn, "DELETE FROM judge.sessions WHERE owner_id = %s", (owner_id,)
        )


class PgReceipts:
    """Ingest receipts: idempotency and metadata only."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    async def get(self, event_id: UUID) -> IngestReceipt | None:
        row = await fetch_one(
            self._conn,
            "SELECT * FROM judge.ingest_receipts WHERE event_id = %s",
            (event_id,),
        )
        return IngestReceipt.model_validate(row) if row else None

    async def insert(self, receipt: IngestReceipt) -> bool:
        count = await execute(
            self._conn,
            """
            INSERT INTO judge.ingest_receipts (
                event_id, owner_id, session_id, kind, source_seq,
                capture_start_ms, capture_end_ms, received_at, byte_length,
                outcome, process_epoch
            ) VALUES (
                %(event_id)s, %(owner_id)s, %(session_id)s, %(kind)s,
                %(source_seq)s, %(capture_start_ms)s, %(capture_end_ms)s,
                %(received_at)s, %(byte_length)s, %(outcome)s, %(process_epoch)s
            ) ON CONFLICT (event_id) DO NOTHING
            """,
            receipt.model_dump(mode="python") | {"kind": receipt.kind.value},
        )
        return count == 1

    async def prune(self, older_than: datetime) -> int:
        return await execute(
            self._conn,
            "DELETE FROM judge.ingest_receipts WHERE received_at < %s",
            (older_than,),
        )


class PgReels:
    """Reels: one committed outcome per observed exposure."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    async def insert_pending(self, reel: PendingReel) -> bool:
        count = await execute(
            self._conn,
            """
            INSERT INTO judge.reels (
                reel_id, owner_id, session_id, source_id, order_seq,
                idempotency_key, start_ms, end_ms, boundary_uncertainty_ms,
                first_second_observed, segmentation_status, close_reason,
                created_at
            ) VALUES (
                %(reel_id)s, %(owner_id)s, %(session_id)s, %(source_id)s,
                %(order_seq)s, %(idempotency_key)s, %(start_ms)s, %(end_ms)s,
                %(boundary_uncertainty_ms)s, %(first_second_observed)s,
                %(segmentation_status)s, %(close_reason)s, %(created_at)s
            ) ON CONFLICT (reel_id) DO NOTHING
            """,
            reel.model_dump(mode="python")
            | {
                "segmentation_status": reel.segmentation_status.value,
                "close_reason": reel.close_reason.value,
            },
        )
        return count == 1

    async def complete(self, completion: ReelCompletion) -> bool:
        count = await execute(
            self._conn,
            """
            UPDATE judge.reels SET
                evaluation_status = %(evaluation_status)s,
                completed_at = %(completed_at)s,
                coverage = %(coverage)s,
                audio_status = %(audio_status)s,
                transcript_status = %(transcript_status)s,
                transcript = %(transcript)s,
                on_screen_text = %(on_screen_text)s,
                description = %(description)s,
                features = %(features)s,
                verdict = %(verdict)s,
                confidence = %(confidence)s,
                value_ids = %(value_ids)s,
                reason = %(reason)s,
                values_revision = %(values_revision)s,
                values_sha256 = %(values_sha256)s,
                feedback_example_ids = %(feedback_example_ids)s,
                retrieval_mode = %(retrieval_mode)s,
                model_id = %(model_id)s,
                prompt_version = %(prompt_version)s,
                understanding_attempts = %(understanding_attempts)s,
                error_code = %(error_code)s
            WHERE reel_id = %(reel_id)s AND evaluation_status = 'pending'
            """,
            {
                "reel_id": completion.reel_id,
                "evaluation_status": completion.evaluation_status.value,
                "completed_at": completion.completed_at,
                "coverage": _json(completion.coverage),
                "audio_status": _enum(completion.audio_status),
                "transcript_status": _enum(completion.transcript_status),
                "transcript": completion.transcript,
                "on_screen_text": completion.on_screen_text,
                "description": completion.description,
                "features": _json(completion.features),
                "verdict": _enum(completion.verdict),
                "confidence": completion.confidence,
                "value_ids": list(completion.value_ids),
                "reason": completion.reason,
                "values_revision": completion.values_revision,
                "values_sha256": completion.values_sha256,
                "feedback_example_ids": list(completion.feedback_example_ids),
                "retrieval_mode": _enum(completion.retrieval_mode),
                "model_id": completion.model_id,
                "prompt_version": completion.prompt_version,
                "understanding_attempts": completion.understanding_attempts,
                "error_code": completion.error_code,
            },
        )
        return count == 1

    async def set_timings(
        self, reel_id: UUID, timings: StageTimings, latency_overrun: bool | None
    ) -> None:
        await execute(
            self._conn,
            "UPDATE judge.reels SET timings = %s, latency_overrun = %s "
            "WHERE reel_id = %s",
            (_json(timings), latency_overrun, reel_id),
        )

    async def get(self, owner_id: str, reel_id: UUID) -> ReelRecord | None:
        row = await fetch_one(
            self._conn,
            _REEL_SELECT + " WHERE r.owner_id = %s AND r.reel_id = %s",
            (owner_id, reel_id),
        )
        return reel_record(row) if row else None

    async def next_accounting_view(
        self, session_id: UUID, after_order_seq: int
    ) -> ReelAccountingView | None:
        row = await fetch_one(
            self._conn,
            """
            SELECT reel_id, order_seq, evaluation_status, verdict
            FROM judge.reels WHERE session_id = %s AND order_seq > %s
            ORDER BY order_seq LIMIT 1
            """,
            (session_id, after_order_seq),
        )
        return ReelAccountingView.model_validate(row) if row else None

    async def list_timeline(
        self,
        owner_id: str,
        *,
        session_id: UUID | None,
        limit: int,
        before: TimelineCursor | None,
    ) -> list[ReelRecord]:
        rows = await fetch_all(
            self._conn,
            _REEL_SELECT + """
            WHERE r.owner_id = %(owner_id)s
              AND (%(session_id)s::uuid IS NULL OR r.session_id = %(session_id)s)
              AND (%(start_ms)s::bigint IS NULL
                   OR (r.start_ms, r.reel_id) < (%(start_ms)s, %(reel_id)s::uuid))
            ORDER BY r.start_ms DESC, r.reel_id DESC
            LIMIT %(limit)s
            """,
            {
                "owner_id": owner_id,
                "session_id": session_id,
                "start_ms": before.start_ms if before else None,
                "reel_id": before.reel_id if before else None,
                "limit": limit,
            },
        )
        return [reel_record(row) for row in rows]

    async def list_pending(
        self, *, created_before: datetime | None = None
    ) -> list[PendingReelRef]:
        rows = await fetch_all(
            self._conn,
            """
            SELECT reel_id, owner_id, session_id, created_at FROM judge.reels
            WHERE evaluation_status = 'pending'
              AND (%(cutoff)s::timestamptz IS NULL OR created_at < %(cutoff)s)
            ORDER BY created_at
            """,
            {"cutoff": created_before},
        )
        return [PendingReelRef.model_validate(row) for row in rows]

    async def count_pending(self, session_id: UUID) -> int:
        row = await fetch_one(
            self._conn,
            """
            SELECT count(*) AS n FROM judge.reels
            WHERE session_id = %s AND evaluation_status = 'pending'
            """,
            (session_id,),
        )
        return int(row["n"]) if row else 0

    async def count_for_session(self, session_id: UUID) -> int:
        row = await fetch_one(
            self._conn,
            "SELECT count(*) AS n FROM judge.reels WHERE session_id = %s",
            (session_id,),
        )
        return int(row["n"]) if row else 0

    async def delete(self, owner_id: str, reel_id: UUID) -> bool:
        count = await execute(
            self._conn,
            "DELETE FROM judge.reels WHERE owner_id = %s AND reel_id = %s",
            (owner_id, reel_id),
        )
        return count == 1


class PgThumbnails:
    """The single retained thumbnail per reel."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    async def put(self, thumbnail: ThumbnailRecord) -> None:
        await execute(
            self._conn,
            """
            INSERT INTO judge.reel_thumbnails
                (reel_id, owner_id, jpeg, width, height, sha256)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (reel_id) DO UPDATE
            SET jpeg = EXCLUDED.jpeg, width = EXCLUDED.width,
                height = EXCLUDED.height, sha256 = EXCLUDED.sha256
            """,
            (
                thumbnail.reel_id,
                thumbnail.owner_id,
                thumbnail.jpeg,
                thumbnail.width,
                thumbnail.height,
                thumbnail.sha256,
            ),
        )
        await execute(
            self._conn,
            "UPDATE judge.reels SET has_thumbnail = true WHERE reel_id = %s",
            (thumbnail.reel_id,),
        )

    async def get(self, owner_id: str, reel_id: UUID) -> ThumbnailRecord | None:
        row = await fetch_one(
            self._conn,
            """
            SELECT reel_id, owner_id, jpeg, width, height, sha256
            FROM judge.reel_thumbnails WHERE owner_id = %s AND reel_id = %s
            """,
            (owner_id, reel_id),
        )
        if row is None:
            return None
        return ThumbnailRecord.model_validate(row | {"jpeg": bytes(row["jpeg"])})
