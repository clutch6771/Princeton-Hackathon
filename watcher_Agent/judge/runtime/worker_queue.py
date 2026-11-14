"""Bounded in-process worker queue.

Capacity is finite, so overload is explicit: ``try_submit`` returns False and
the caller records a skipped outcome and releases the media.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class WorkerQueue[T]:
    """A bounded queue drained by a fixed number of worker tasks."""

    def __init__(self, *, capacity: int) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=capacity)
        self._workers: list[asyncio.Task[None]] = []

    @property
    def depth(self) -> int:
        """Items waiting for a worker."""
        return self._queue.qsize()

    def try_submit(self, item: T) -> bool:
        """Enqueue without waiting; False when the queue is full."""
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            return False
        return True

    def start(
        self, handler: Callable[[T], Awaitable[None]], *, concurrency: int
    ) -> None:
        """Start ``concurrency`` workers that call ``handler`` per item."""
        if self._workers:
            msg = "worker queue already started"
            raise RuntimeError(msg)
        self._workers = [
            asyncio.create_task(self._run(handler), name=f"judge-worker-{index}")
            for index in range(concurrency)
        ]

    async def _run(self, handler: Callable[[T], Awaitable[None]]) -> None:
        while True:
            item = await self._queue.get()
            try:
                await handler(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The handler owns its own failure outcome; this is the last
                # line of defence so one bad job cannot kill the worker.
                logger.exception("worker handler failed")
            finally:
                self._queue.task_done()

    async def join(self) -> None:
        """Wait until every submitted item has been handled."""
        await self._queue.join()

    async def stop(self) -> list[T]:
        """Cancel workers and return items that were never started."""
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        leftover: list[T] = []
        while not self._queue.empty():
            leftover.append(self._queue.get_nowait())
            self._queue.task_done()
        return leftover
