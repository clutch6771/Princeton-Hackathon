"""Session end, idle expiry, deadline sweeps and restart recovery.

A session closes on ``POST /session/end`` or after the idle timeout without
an accepted ingest. Closing flushes the permitted tail and stores a lifecycle
event. It never invokes another agent.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from judge.config import Settings
from judge.contracts.record import EvaluationStatus
from judge.contracts.session import (
    SessionEndReason,
    SessionEndRequest,
    SessionEndResponse,
    SessionStatus,
)
from judge.embedding.indexer import EmbeddingIndexer
from judge.errors import NotFoundError, ReelErrorCode
from judge.ingest.service import store_pending
from judge.ingest.session_state import StateConfig, close_for_end
from judge.ingest.volatile import VolatileSessions
from judge.notifications.outbox import NotificationDispatcher
from judge.persistence.interfaces import Storage
from judge.pipeline.completion import ReelCompletionService, complete_session_if_done
from judge.pipeline.finalizer import CandidateFinalizer
from judge.pipeline.outcomes import lost_outcome
from judge.runtime.clock import Clock

logger = logging.getLogger(__name__)
_RECEIPT_PRUNE_INTERVAL_SECONDS = 3600.0


class SessionLifecycle:
    """Ends sessions explicitly or by idle timeout."""

    def __init__(
        self,
        *,
        settings: Settings,
        clock: Clock,
        storage: Storage,
        volatile: VolatileSessions,
        finalizer: CandidateFinalizer,
        dispatcher: NotificationDispatcher,
    ) -> None:
        self._config = StateConfig.from_settings(settings)
        self._idle = timedelta(seconds=settings.session_idle_timeout_seconds)
        self._clock = clock
        self._storage = storage
        self._volatile = volatile
        self._finalizer = finalizer
        self._dispatcher = dispatcher

    async def end_session(
        self,
        owner_id: str,
        request: SessionEndRequest,
        *,
        reason: SessionEndReason = SessionEndReason.CLIENT_END,
    ) -> SessionEndResponse:
        """Idempotently stop ingest and flush the bounded tail."""
        session_id = request.session_id
        async with self._volatile.lock(session_id):
            now, monotonic = self._clock.now(), self._clock.monotonic()
            state = self._volatile.get(session_id)
            async with self._storage.transaction() as tx:
                session = await tx.sessions.get(session_id, for_update=True)
                if session is None or session.owner_id != owner_id:
                    msg = "session not found"
                    raise NotFoundError(msg)
                notify = await self._dispatcher.acknowledge_and_dispatch(
                    tx, owner_id, request.acknowledged_notification_ids, now
                )
                status = session.status
                if status is SessionStatus.ACTIVE:
                    if state is not None:
                        state, closed = close_for_end(
                            state, config=self._config, now=now, monotonic=monotonic
                        )
                        state = await store_pending(tx, state, closed, now)
                    await tx.sessions.set_status(
                        session_id,
                        SessionStatus.ENDING,
                        ended_at=now,
                        end_reason=reason,
                    )
                    done = await complete_session_if_done(tx, session_id, now)
                    status = SessionStatus.ENDED if done else SessionStatus.ENDING
            if state is not None:
                self._volatile.put(state)
        await self._finalizer.process(session_id)
        self._volatile.drop(session_id)
        return SessionEndResponse(session_id=session_id, status=status, notify=notify)

    async def expire_idle(self) -> int:
        """End sessions with no accepted ingest for the idle timeout."""
        cutoff = self._clock.now() - self._idle
        async with self._storage.transaction() as tx:
            idle = await tx.sessions.list_idle_active(cutoff)
        for session in idle:
            logger.info(
                "session idle timeout", extra={"session": str(session.session_id)}
            )
            await self.end_session(
                session.owner_id,
                SessionEndRequest(session_id=session.session_id),
                reason=SessionEndReason.IDLE_TIMEOUT,
            )
        return len(idle)


class Maintenance:
    """Periodic, time-driven work. ``tick`` is safe to call at any time."""

    def __init__(
        self,
        *,
        settings: Settings,
        clock: Clock,
        storage: Storage,
        finalizer: CandidateFinalizer,
        lifecycle: SessionLifecycle,
        completion: ReelCompletionService,
        indexer: EmbeddingIndexer,
    ) -> None:
        self._overdue = timedelta(
            seconds=settings.job_deadline_seconds + settings.job_sweep_grace_seconds
        )
        self._retention = timedelta(seconds=settings.receipt_retention_seconds)
        self._clock = clock
        self._storage = storage
        self.finalizer = finalizer
        self._lifecycle = lifecycle
        self._completion = completion
        self._indexer = indexer
        self._last_prune = clock.monotonic()

    async def tick(self) -> None:
        """Run every time-driven duty once."""
        await self.finalizer.process_all()
        await self._lifecycle.expire_idle()
        await self._fail_overdue()
        await self._indexer.run_due()
        await self._prune_receipts()

    async def _fail_overdue(self) -> int:
        """Finalize stuck reels so one job can never block the cursor."""
        now = self._clock.now()
        async with self._storage.transaction() as tx:
            overdue = await tx.reels.list_pending(created_before=now - self._overdue)
        for ref in overdue:
            await self._completion.complete(
                lost_outcome(
                    ref,
                    EvaluationStatus.FAILED_TIMEOUT,
                    ReelErrorCode.JOB_DEADLINE,
                    now=now,
                )
            )
        return len(overdue)

    async def _prune_receipts(self) -> None:
        if self._clock.monotonic() - self._last_prune < _RECEIPT_PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = self._clock.monotonic()
        async with self._storage.transaction() as tx:
            await tx.receipts.prune(self._clock.now() - self._retention)

    async def recover_after_restart(self) -> int:
        """Mark reels whose volatile media died with the previous process.

        Stored receipts are not proof that media is recoverable: it is not.
        """
        now = self._clock.now()
        async with self._storage.transaction() as tx:
            pending = await tx.reels.list_pending()
            ending = await tx.sessions.list_by_status(SessionStatus.ENDING)
        for ref in pending:
            await self._completion.complete(
                lost_outcome(
                    ref,
                    EvaluationStatus.LOST_ON_RESTART,
                    ReelErrorCode.RESTART,
                    now=now,
                )
            )
        await self._finish_ending(tuple(session.session_id for session in ending))
        if pending:
            logger.warning("reels lost on restart", extra={"count": len(pending)})
        return len(pending)

    async def _finish_ending(self, session_ids: tuple[UUID, ...]) -> None:
        for session_id in session_ids:
            async with self._storage.transaction() as tx:
                await complete_session_if_done(tx, session_id, self._clock.now())
