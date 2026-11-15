"""Finalize closed exposures: bounded wait for the final audio, then enqueue.

A closed exposure waits only for the permitted final audio interval, until
the audio watermark passes its window or the documented deadline elapses. The
wait is a check performed here (after each ingest and on every maintenance
tick); no HTTP request is ever held open for it.
"""

from __future__ import annotations

import logging
from uuid import UUID

from judge.audio.assembler import AudioAssembler
from judge.config import Settings
from judge.contracts.record import EvaluationStatus
from judge.errors import ReelErrorCode
from judge.ingest.session_state import (
    ClosedCandidate,
    StateConfig,
    prune_audio,
    take_ready,
)
from judge.ingest.volatile import VolatileSessions
from judge.pipeline.completion import ReelCompletionService
from judge.pipeline.job import JudgeJob, build_job
from judge.pipeline.outcomes import failed_outcome
from judge.runtime.clock import Clock
from judge.runtime.media_budget import MediaBudget
from judge.runtime.worker_queue import WorkerQueue

logger = logging.getLogger(__name__)


class CandidateFinalizer:
    """Turns ready exposures into queued judge jobs."""

    def __init__(
        self,
        *,
        settings: Settings,
        clock: Clock,
        volatile: VolatileSessions,
        budget: MediaBudget,
        assembler: AudioAssembler,
        queue: WorkerQueue[JudgeJob],
        completion: ReelCompletionService,
    ) -> None:
        self._settings = settings
        self._config = StateConfig.from_settings(settings)
        self._clock = clock
        self._volatile = volatile
        self._budget = budget
        self._assembler = assembler
        self._queue = queue
        self._completion = completion

    async def process(self, session_id: UUID) -> int:
        """Finalize every ready exposure of one session."""
        async with self._volatile.lock(session_id):
            state = self._volatile.get(session_id)
            if state is None:
                return 0
            monotonic = self._clock.monotonic()
            remaining, ready = take_ready(
                state, monotonic=monotonic, config=self._config
            )
            jobs = [
                build_job(
                    state,
                    closed,
                    settings=self._settings,
                    assembler=self._assembler,
                    ready_monotonic=monotonic,
                )
                for closed in ready
            ]
            pruned, released = prune_audio(remaining)
            self._volatile.put(pruned)
            self._budget.release(session_id, released)
        for closed, job in zip(ready, jobs, strict=True):
            try:
                await self._submit(closed, job)
            except Exception:
                # One failing submission must not strand the others. The reel
                # stays pending and the deadline sweep finalizes it.
                logger.exception(
                    "submitting an exposure failed",
                    extra={"reel_id": str(job.candidate.candidate_id)},
                )
        return len(ready)

    async def process_all(self) -> int:
        """Finalize ready exposures of every tracked session."""
        total = 0
        for session_id in self._volatile.session_ids():
            total += await self.process(session_id)
        return total

    async def _submit(self, closed: ClosedCandidate, job: JudgeJob) -> None:
        session_id = job.candidate.session_id
        # The job keeps only the selected frames and the assembled clip.
        self._budget.release(session_id, closed.selection.nbytes)
        if not self._budget.try_reserve(session_id, job.reserved_bytes):
            await self._skip(job, ReelErrorCode.MEDIA_BUDGET)
            return
        if not self._queue.try_submit(job):
            self._budget.release(session_id, job.reserved_bytes)
            await self._skip(job, ReelErrorCode.QUEUE_FULL)

    async def _skip(self, job: JudgeJob, code: ReelErrorCode) -> None:
        logger.warning(
            "exposure skipped under overload",
            extra={"reel_id": str(job.candidate.candidate_id), "code": code.value},
        )
        await self._completion.complete(
            failed_outcome(
                job, EvaluationStatus.SKIPPED_OVERLOAD, code, now=self._clock.now()
            )
        )
