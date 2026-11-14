"""Feedback, timeline and deletion services used by the HTTP API.

Storing feedback is storage only: it starts no scheduler, no conversation and
never edits approved values. Deletion is owner-scoped and removes dependent
thumbnails, vectors and feedback, cancels warnings that mention deleted reels,
and clears caches and volatile media.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from judge.contracts.feedback import FeedbackRecord, FeedbackRequest
from judge.contracts.record import ReelRecord
from judge.contracts.storage import ThumbnailRecord, TimelineCursor
from judge.errors import NotFoundError, ValidationFailure
from judge.feedback.retriever import FeedbackRetriever
from judge.ingest.volatile import VolatileSessions
from judge.notifications.outbox import cancel_for_reels
from judge.persistence.interfaces import Storage
from judge.pipeline.judge_pipeline import ValuesGate
from judge.runtime.clock import Clock

_MS = 1000


class FeedbackService:
    """Stores explicit feedback for a reel the caller owns."""

    def __init__(
        self, *, storage: Storage, clock: Clock, retriever: FeedbackRetriever
    ) -> None:
        self._storage = storage
        self._clock = clock
        self._retriever = retriever

    async def submit(self, owner_id: str, request: FeedbackRequest) -> FeedbackRecord:
        """Validate ownership and store the feedback idempotently."""
        async with self._storage.transaction() as tx:
            if await tx.reels.get(owner_id, request.reel_id) is None:
                msg = "reel not found"
                raise NotFoundError(msg)
            stored = await tx.feedback.insert(
                FeedbackRecord(
                    feedback_id=uuid4(),
                    owner_id=owner_id,
                    reel_id=request.reel_id,
                    label=request.label,
                    score=request.score,
                    comment=request.comment,
                    source=request.source,
                    client_feedback_id=request.client_feedback_id,
                    created_at=self._clock.now(),
                )
            )
        self._retriever.invalidate(owner_id)
        return stored


class TimelineService:
    """Owner-scoped, paginated reads of stored reels and thumbnails."""

    def __init__(self, *, storage: Storage) -> None:
        self._storage = storage

    async def list_reels(
        self,
        owner_id: str,
        *,
        session_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[ReelRecord], str | None]:
        """Newest first. Returns the page and the cursor of the next page."""
        try:
            before = TimelineCursor.decode(cursor) if cursor else None
        except ValueError as error:
            msg = "cursor is not valid"
            raise ValidationFailure(msg) from error
        async with self._storage.transaction() as tx:
            page = await tx.reels.list_timeline(
                owner_id, session_id=session_id, limit=limit, before=before
            )
        if len(page) < limit:
            return page, None
        last = page[-1]
        start_ms = round(last.start.timestamp() * _MS)
        return page, TimelineCursor(start_ms=start_ms, reel_id=last.id).encode()

    async def thumbnail(self, owner_id: str, reel_id: UUID) -> ThumbnailRecord:
        """The reel's single retained thumbnail."""
        async with self._storage.transaction() as tx:
            thumbnail = await tx.thumbnails.get(owner_id, reel_id)
        if thumbnail is None:
            msg = "thumbnail not found"
            raise NotFoundError(msg)
        return thumbnail


class DeletionService:
    """Owner-scoped deletion hooks."""

    def __init__(
        self,
        *,
        storage: Storage,
        volatile: VolatileSessions,
        retriever: FeedbackRetriever,
        values_gate: ValuesGate,
    ) -> None:
        self._storage = storage
        self._volatile = volatile
        self._retriever = retriever
        self._values_gate = values_gate

    def _invalidate(self, owner_id: str) -> None:
        self._retriever.invalidate(owner_id)
        self._values_gate.invalidate(owner_id)

    async def delete_reel(self, owner_id: str, reel_id: UUID) -> None:
        """Delete one reel with its thumbnail, vectors and feedback."""
        async with self._storage.transaction() as tx:
            reel = await tx.reels.get(owner_id, reel_id)
            if reel is None:
                msg = "reel not found"
                raise NotFoundError(msg)
            # Same lock order as completion and dispatch (session row, then
            # the user's notification state), so deletion cannot deadlock.
            await tx.sessions.get(reel.session_id, for_update=True)
            await tx.notifications.lock_user_state(owner_id)
            await cancel_for_reels(tx, owner_id, [reel_id])
            await tx.reels.delete(owner_id, reel_id)
        self._invalidate(owner_id)

    async def delete_session(self, owner_id: str, session_id: UUID) -> None:
        """Delete one session and everything recorded for it."""
        async with self._volatile.lock(session_id):
            async with self._storage.transaction() as tx:
                if not await tx.sessions.delete(owner_id, session_id):
                    msg = "session not found"
                    raise NotFoundError(msg)
            self._volatile.drop(session_id)
        self._invalidate(owner_id)

    async def delete_owner_data(self, owner_id: str) -> None:
        """Delete everything the Judge stores for an owner.

        The approved values are not touched: the Judge has read-only access
        and they belong to the memory owner.
        """
        async with self._storage.transaction() as tx:
            await tx.sessions.delete_all(owner_id)
            await tx.notifications.delete_all(owner_id)
            await tx.policy.delete_all(owner_id)
        for session_id in self._volatile.owned_by(owner_id):
            self._volatile.drop(session_id)
        self._invalidate(owner_id)
