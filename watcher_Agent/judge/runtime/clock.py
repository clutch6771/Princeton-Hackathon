"""Clock capability. Server time governs receipt, idle expiry and cooldown."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of wall-clock time, monotonic time and sleeping."""

    def now(self) -> datetime:
        """Aware UTC wall-clock time."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds, for measuring durations and deadlines."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait without blocking the event loop."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))
