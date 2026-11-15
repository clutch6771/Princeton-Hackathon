"""Wire the Judge runtime from typed, injected capabilities.

``build_runtime`` takes every external capability as an argument, so tests
and development use fakes while production uses the Gemini and PostgreSQL
adapters built by ``create_runtime``.

The runtime holds volatile media in this process, so run exactly one process
(one uvicorn worker).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import uuid4

from judge.audio.assembler import PcmAudioAssembler
from judge.audio.codec import AacDecoder, PyAvAacDecoder
from judge.audio.pcm import PCM_SAMPLE_RATE
from judge.config import Settings
from judge.embedding.indexer import EmbeddingIndexer
from judge.feedback.retriever import RecentFeedbackRetriever
from judge.feedback.service import DeletionService, FeedbackService, TimelineService
from judge.ingest.service import IngestService
from judge.ingest.volatile import VolatileSessions
from judge.persistence.interfaces import Storage
from judge.pipeline.completion import ReelCompletionService
from judge.pipeline.finalizer import CandidateFinalizer
from judge.pipeline.job import JudgeJob
from judge.pipeline.judge_pipeline import JudgePipeline, ValuesGate
from judge.pipeline.lifecycle import Maintenance, SessionLifecycle
from judge.privacy.eligibility import BackendEligibilityGate
from judge.runtime.clock import Clock, SystemClock
from judge.runtime.media_budget import MediaBudget
from judge.runtime.rate_limiter import DailyQuota, KeyedRateLimiter, TokenBucket
from judge.runtime.worker_queue import WorkerQueue
from judge.segmentation.segmenter import HashReelSegmenter, SegmenterConfig
from judge.understanding.provider import EmbeddingProvider, UnderstandingProvider
from judge.values.reader import CachedValuesReader, ValuesReader

logger = logging.getLogger(__name__)
_MS = 1000


@dataclass
class JudgeRuntime:
    """Every service of the running Judge backend."""

    settings: Settings
    clock: Clock
    storage: Storage
    budget: MediaBudget
    volatile: VolatileSessions
    queue: WorkerQueue[JudgeJob]
    gate: BackendEligibilityGate
    ingest: IngestService
    lifecycle: SessionLifecycle
    maintenance: Maintenance
    pipeline: JudgePipeline
    completion: ReelCompletionService
    feedback: FeedbackService
    timeline: TimelineService
    deletion: DeletionService
    ingest_limiter: KeyedRateLimiter
    api_limiter: KeyedRateLimiter
    understanding: UnderstandingProvider
    _ticker: asyncio.Task[None] | None = None

    async def start(self, *, run_ticker: bool = False) -> None:
        """Recover from a previous process, then start the workers."""
        await self.maintenance.recover_after_restart()
        self.queue.start(
            self.pipeline.run, concurrency=self.settings.worker_concurrency
        )
        if run_ticker:
            self._ticker = asyncio.create_task(self._tick_forever(), name="judge-tick")

    async def _tick_forever(self) -> None:
        while True:
            await asyncio.sleep(self.settings.tick_interval_seconds)
            try:
                await self.maintenance.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("maintenance tick failed")

    async def stop(self) -> None:
        """Stop work and release every byte of volatile media."""
        if self._ticker is not None:
            self._ticker.cancel()
            await asyncio.gather(self._ticker, return_exceptions=True)
            self._ticker = None
        for job in await self.queue.stop():
            self.budget.release(job.candidate.session_id, job.reserved_bytes)
        self.volatile.drop_all()
        await self.storage.close()


def build_runtime(
    settings: Settings,
    *,
    storage: Storage,
    understanding: UnderstandingProvider,
    embedding: EmbeddingProvider | None,
    values: ValuesReader,
    clock: Clock | None = None,
    decoder: AacDecoder | None = None,
) -> JudgeRuntime:
    """Assemble the runtime from injected capabilities."""
    clock = clock or SystemClock()
    budget = MediaBudget(
        total_limit=settings.media_budget_bytes,
        session_limit=settings.session_media_budget_bytes,
    )
    volatile = VolatileSessions(
        max_sessions=settings.max_active_sessions, budget=budget
    )
    queue: WorkerQueue[JudgeJob] = WorkerQueue(capacity=settings.worker_queue_capacity)
    completion = ReelCompletionService(settings=settings, storage=storage, clock=clock)
    finalizer = CandidateFinalizer(
        settings=settings,
        clock=clock,
        volatile=volatile,
        budget=budget,
        assembler=PcmAudioAssembler(
            sample_rate=PCM_SAMPLE_RATE, complete_ratio=settings.audio_complete_ratio
        ),
        queue=queue,
        completion=completion,
    )
    retriever = RecentFeedbackRetriever(
        storage=storage,
        max_examples=settings.max_feedback_examples,
        comment_max_chars=settings.feedback_example_max_chars,
    )
    values_gate = ValuesGate(
        CachedValuesReader(
            values, ttl_seconds=settings.values_cache_ttl_seconds, clock=clock
        ),
        clock=clock,
        wait_seconds=settings.values_wait_seconds,
        poll_seconds=settings.values_poll_seconds,
    )
    pipeline = JudgePipeline(
        settings=settings,
        clock=clock,
        provider=understanding,
        values_gate=values_gate,
        retriever=retriever,
        completion=completion,
        budget=budget,
        limiter=TokenBucket.per_minute(settings.provider_requests_per_minute, clock),
        daily_quota=DailyQuota(limit=settings.provider_requests_per_day, clock=clock),
    )
    gate = BackendEligibilityGate(settings.privacy_mode)
    ingest = IngestService(
        settings=settings,
        clock=clock,
        storage=storage,
        gate=gate,
        segmenter=HashReelSegmenter(
            SegmenterConfig(
                frame_threshold=settings.segment_frame_hash_threshold,
                overlay_threshold=settings.segment_overlay_hash_threshold,
                gap_ms=int(settings.segment_gap_seconds * _MS),
            )
        ),
        decoder=decoder or PyAvAacDecoder(),
        volatile=volatile,
        budget=budget,
        finalizer=finalizer,
        process_epoch=uuid4(),
    )
    lifecycle = SessionLifecycle(
        settings=settings,
        clock=clock,
        storage=storage,
        volatile=volatile,
        finalizer=finalizer,
    )
    indexer = EmbeddingIndexer(
        settings=settings,
        storage=storage,
        clock=clock,
        provider=embedding,
        limiter=TokenBucket.per_minute(settings.embedding_requests_per_minute, clock),
    )
    return JudgeRuntime(
        settings=settings,
        clock=clock,
        storage=storage,
        budget=budget,
        volatile=volatile,
        queue=queue,
        gate=gate,
        ingest=ingest,
        lifecycle=lifecycle,
        maintenance=Maintenance(
            settings=settings,
            clock=clock,
            storage=storage,
            finalizer=finalizer,
            lifecycle=lifecycle,
            completion=completion,
            indexer=indexer,
        ),
        pipeline=pipeline,
        completion=completion,
        feedback=FeedbackService(storage=storage, clock=clock, retriever=retriever),
        timeline=TimelineService(storage=storage),
        deletion=DeletionService(
            storage=storage,
            volatile=volatile,
            retriever=retriever,
            values_gate=values_gate,
        ),
        ingest_limiter=KeyedRateLimiter(
            per_minute=settings.ingest_requests_per_minute, clock=clock
        ),
        api_limiter=KeyedRateLimiter(
            per_minute=settings.api_requests_per_minute, clock=clock
        ),
        understanding=understanding,
    )
