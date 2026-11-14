"""Commit one terminal outcome per reel, with everything that depends on it.

One transaction holds: the reel outcome, its single thumbnail, the pending
embedding marker, ordered session counts and
session completion. Either all of it is stored or none of it is.

Guarantee: exactly one committed judgment per reel. This is not exactly-once
remote billing: a provider call whose response was lost may have been billed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID, uuid4

from judge.config import Settings
from judge.contracts.record import EvaluationStatus, ReelCompletion, StageTimings
from judge.contracts.session import (
    SessionCompletedPayload,
    SessionEndReason,
    SessionEvent,
    SessionStatus,
)
from judge.contracts.storage import EmbeddingProfile, ThumbnailRecord
from judge.persistence.interfaces import Storage, StorageTx
from judge.pipeline.accounting import advance_accounting
from judge.runtime.clock import Clock

logger = logging.getLogger(__name__)
_MS = 1000


async def complete_session_if_done(
    tx: StorageTx, session_id: UUID, now: datetime
) -> bool:
    """Finish an ending session once none of its reels is pending.

    Writes one ``session_completed`` lifecycle event for future consumers.
    It does not invoke any other agent.
    """
    session = await tx.sessions.get(session_id, for_update=True)
    if session is None or session.status is not SessionStatus.ENDING:
        return False
    if await tx.reels.count_pending(session_id) > 0:
        return False
    await tx.sessions.set_status(session_id, SessionStatus.ENDED)
    await tx.sessions.add_event(
        SessionEvent(
            event_id=uuid4(),
            session_id=session_id,
            owner_id=session.owner_id,
            payload=SessionCompletedPayload(
                reel_count=await tx.reels.count_for_session(session_id),
                judged_count=session.judged_count,
                conflict_count=session.conflict_count,
                excluded_count=session.excluded_count,
                end_reason=session.end_reason or SessionEndReason.CLIENT_END,
                started_at=session.started_at,
                ended_at=session.ended_at or now,
            ),
            created_at=now,
        )
    )
    return True


class ReelCompletionService:
    """Stores a reel's single terminal outcome and updates session counts."""

    def __init__(self, *, settings: Settings, storage: Storage, clock: Clock) -> None:
        self._storage = storage
        self._clock = clock
        self._profile = EmbeddingProfile.from_settings(settings)
        self._index = settings.embedding_indexing_enabled
        self._target_ms = settings.reel_end_to_verdict_target_seconds * _MS

    async def complete(
        self,
        completion: ReelCompletion,
        *,
        thumbnail: ThumbnailRecord | None = None,
        timings: StageTimings | None = None,
    ) -> bool:
        """Commit the outcome. False when the reel was already finalized."""
        started = self._clock.monotonic()
        async with self._storage.transaction() as tx:
            session = await tx.sessions.get(completion.session_id, for_update=True)
            if session is None or not await tx.reels.complete(completion):
                return False
            now = self._clock.now()
            judged = completion.evaluation_status is EvaluationStatus.JUDGED
            if judged and thumbnail is not None:
                await tx.thumbnails.put(thumbnail)
            if judged and self._index:
                await tx.embeddings.create_pending(
                    completion.reel_id, completion.owner_id, self._profile, now=now
                )
            session = await advance_accounting(tx, session)
            await complete_session_if_done(tx, session.session_id, now)
            if timings is not None:
                await self._store_timings(tx, completion.reel_id, timings, started)
        return True

    async def _store_timings(
        self, tx: StorageTx, reel_id: UUID, timings: StageTimings, started: float
    ) -> None:
        persist_ms = int((self._clock.monotonic() - started) * _MS)
        total = None if timings.total_ms is None else timings.total_ms + persist_ms
        final = timings.model_copy(update={"persist_ms": persist_ms, "total_ms": total})
        overrun = None if total is None else total > self._target_ms
        if overrun:
            logger.warning(
                "reel end to verdict overran the target",
                extra={"reel_id": str(reel_id), "total_ms": total},
            )
        await tx.reels.set_timings(reel_id, final, overrun)
