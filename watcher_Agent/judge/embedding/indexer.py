"""Asynchronous embedding indexing of judged reels.

Indexing happens after the verdict is committed, in its own idempotent
transaction, so embedding latency or failure can neither erase a verdict nor
delay a warning. Retries use the stored description and transcript only:
no media is retained and no second understanding call is made.

One embedding per new reel on the normal path. Vectors are stored only under
the pinned profile; a task from another profile is failed, never mixed.
"""

from __future__ import annotations

import logging
import math
from datetime import timedelta

from judge.config import Settings
from judge.contracts.storage import EmbeddingTask
from judge.errors import EmbeddingDimensionError, ProviderError, StorageUnavailableError
from judge.persistence.interfaces import Storage
from judge.runtime.clock import Clock
from judge.runtime.rate_limiter import RateLimiter
from judge.understanding.provider import EmbeddingProvider

logger = logging.getLogger(__name__)
_BATCH_LIMIT = 8
PROFILE_MISMATCH = "embedding_profile_mismatch"
DIMENSION_MISMATCH = "embedding_dimension_mismatch"
VECTOR_STORE_UNAVAILABLE = "vector_store_unavailable"


def normalize(vector: tuple[float, ...]) -> tuple[float, ...]:
    """L2-normalize (reduced-dimension embeddings are not unit length)."""
    norm = math.sqrt(sum(value * value for value in vector))
    return vector if norm == 0 else tuple(value / norm for value in vector)


class EmbeddingIndexer:
    """Embeds due reels and stores their vectors."""

    def __init__(
        self,
        *,
        settings: Settings,
        storage: Storage,
        clock: Clock,
        provider: EmbeddingProvider | None,
        limiter: RateLimiter,
    ) -> None:
        self._enabled = settings.embedding_indexing_enabled and provider is not None
        self._max_attempts = settings.embedding_max_attempts
        self._retry_base = settings.embedding_retry_base_seconds
        self._storage = storage
        self._clock = clock
        self._provider = provider
        self._limiter = limiter

    async def run_due(self) -> int:
        """Process embeddings that are due. Returns how many were indexed."""
        if not self._enabled or self._provider is None:
            return 0
        async with self._storage.transaction() as tx:
            tasks = await tx.embeddings.list_due(self._clock.now(), _BATCH_LIMIT)
        indexed = 0
        for task in tasks:
            if self._limiter.try_reserve(max_wait_seconds=0) is None:
                break
            indexed += int(await self._index(task, self._provider))
        return indexed

    async def _index(self, task: EmbeddingTask, provider: EmbeddingProvider) -> bool:
        if task.profile_id != provider.profile.profile_id:
            await self._fail(task, task.attempts, PROFILE_MISMATCH)
            return False
        attempts = task.attempts + 1
        try:
            vector = normalize(await provider.embed(task.text))
            if len(vector) != task.dimension:
                raise EmbeddingDimensionError(DIMENSION_MISMATCH)
            async with self._storage.transaction() as tx:
                await tx.embeddings.mark_indexed(
                    task.reel_id, task.profile_id, vector, indexed_at=self._clock.now()
                )
        except EmbeddingDimensionError:
            await self._fail(task, attempts, DIMENSION_MISMATCH)
            return False
        except StorageUnavailableError:
            await self._fail(task, attempts, VECTOR_STORE_UNAVAILABLE)
            return False
        except ProviderError as error:
            await self._retry_or_fail(task, attempts, error.reel_error_code.value)
            return False
        return True

    async def _retry_or_fail(
        self, task: EmbeddingTask, attempts: int, code: str
    ) -> None:
        if attempts >= self._max_attempts:
            await self._fail(task, attempts, code)
            return
        delay = self._retry_base * (2 ** (attempts - 1))
        async with self._storage.transaction() as tx:
            await tx.embeddings.mark_retry(
                task.reel_id,
                task.profile_id,
                attempts=attempts,
                next_attempt_at=self._clock.now() + timedelta(seconds=delay),
                error_code=code,
            )

    async def _fail(self, task: EmbeddingTask, attempts: int, code: str) -> None:
        logger.warning(
            "embedding indexing failed",
            extra={"reel_id": str(task.reel_id), "code": code},
        )
        async with self._storage.transaction() as tx:
            await tx.embeddings.mark_failed(
                task.reel_id, task.profile_id, attempts=attempts, error_code=code
            )
