"""Bridge Guardian's synchronous DataSource to the running Judge repositories.

Construct on the server event loop; invoke from a worker thread, as with all
Guardian entry points. Reads use Guardian's existing record/query helpers on
a fresh database snapshot. No seed data, second database or model is used.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime
from typing import Any
from uuid import UUID

from guardian.datasource import (
    FakeDataSource,
    Feedback,
    FeedbackExample,
    FeedProfile,
    NewFeedback,
    Reel,
    ReelSummary,
    Session,
)
from judge.bootstrap import JudgeRuntime
from judge.contracts.feedback import FeedbackLabel, FeedbackRequest
from judge.contracts.record import EvaluationStatus, ReelRecord
from judge.contracts.session import SessionStatus
from judge.contracts.storage import TimelineCursor


class JudgeDataSource:
    """Real, owner-scoped Judge records implementing guardian.datasource.DataSource.

    FakeDataSource is used only as the existing in-memory query implementation
    over freshly loaded real records. Its seed loader and feedback writer are
    never called. Feedback writes go through the Judge's shared service.
    """

    def __init__(self, runtime: JudgeRuntime) -> None:
        self._runtime = runtime
        self._loop = asyncio.get_running_loop()

    def _call[T](self, operation: Coroutine[Any, Any, T]) -> T:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            operation.close()
            raise RuntimeError("Call Guardian and its DataSource from a worker thread")
        return asyncio.run_coroutine_threadsafe(operation, self._loop).result()

    async def _snapshot(self, user_id: str) -> FakeDataSource:
        records: list[ReelRecord] = []
        before = None
        async with self._runtime.storage.transaction() as tx:
            while True:
                page = await tx.reels.list_timeline(
                    user_id, session_id=None, limit=100, before=before
                )
                records.extend(
                    r for r in page if r.evaluation_status is EvaluationStatus.JUDGED
                )
                if len(page) < 100:
                    break
                last = page[-1]
                before = TimelineCursor(
                    start_ms=round(last.start.timestamp() * 1000), reel_id=last.id
                )
            # Existing feedback repository takes a limit; this is a demo-sized
            # history, not a model prompt. Prompt examples remain capped at 3.
            feedback = await tx.feedback.list_for_owner(user_id, 100_000)
            active = await tx.sessions.list_by_status(SessionStatus.ACTIVE)
            ending = await tx.sessions.list_by_status(SessionStatus.ENDING)
        return FakeDataSource.from_records(
            user_id,
            reels=[r.model_dump(mode="python") for r in records],
            feedback=[
                {
                    "id": str(f.feedback_id),
                    "reel_id": str(f.reel_id),
                    "ts": f.created_at,
                    "label": f.label.value,
                    "comment": f.comment,
                    "source": f.source or "app",
                }
                for f in feedback
                if f.label.value != "rerate"
            ],
            clock=self._runtime.clock.now,
            unlisted_sessions=frozenset(),
            open_sessions=frozenset(
                str(s.session_id) for s in [*active, *ending] if s.owner_id == user_id
            ),
        )

    def get_reels(self, user_id: str, reel_ids: list[str]) -> list[Reel]:
        return self._call(self._snapshot(user_id)).get_reels(user_id, reel_ids)

    def list_sessions(self, user_id: str) -> list[Session]:
        return self._call(self._sessions(user_id))

    async def _sessions(self, user_id: str) -> list[Session]:
        async with self._runtime.storage.transaction() as tx:
            sessions = [
                s
                for status in SessionStatus
                for s in await tx.sessions.list_by_status(status)
                if s.owner_id == user_id
            ]
        return [
            Session(
                id=str(s.session_id),
                start=s.started_at,
                last_reel_at=s.last_ingest_at,
                stopped=s.status is SessionStatus.ENDED,
            )
            for s in sorted(sessions, key=lambda s: s.started_at)
        ]

    def get_session_reels(self, user_id: str, session_id: str) -> list[Reel]:
        return self._call(self._snapshot(user_id)).get_session_reels(
            user_id, session_id
        )

    def search_reels(self, user_id: str, query: str, k: int) -> list[ReelSummary]:
        return self._call(self._snapshot(user_id)).search_reels(user_id, query, k)

    def find_similar_feedback(
        self, user_id: str, text: str, k: int
    ) -> list[FeedbackExample]:
        return self._call(self._snapshot(user_id)).find_similar_feedback(
            user_id, text, k
        )

    def get_feedback(self, user_id: str, since: datetime) -> list[Feedback]:
        return self._call(self._snapshot(user_id)).get_feedback(user_id, since)

    def feed_profile(
        self, user_id: str, session_id: str | None, since: datetime | None
    ) -> FeedProfile:
        return self._call(self._snapshot(user_id)).feed_profile(
            user_id, session_id, since
        )

    def record_feedback(self, user_id: str, feedback: NewFeedback) -> str:
        if not feedback.get("reel_id"):
            raise ValueError("Judge feedback requires a reel_id")
        request = FeedbackRequest(
            reel_id=UUID(feedback["reel_id"]),
            label=FeedbackLabel(feedback.get("label", "")),
            comment=feedback.get("comment"),
            source=feedback.get("source", "chat"),
        )
        stored = self._call(self._runtime.feedback.submit(user_id, request))
        return str(stored.feedback_id)
