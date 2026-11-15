"""A controllable clock for tests and deterministic development runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

_DEFAULT_START = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
_MONOTONIC_START = 1_000.0


class FakeClock:
    """Clock whose time only moves when told to."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or _DEFAULT_START
        self._monotonic = _MONOTONIC_START

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        """Move both wall-clock and monotonic time forward."""
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds

    async def sleep(self, seconds: float) -> None:
        self.advance(max(0.0, seconds))
        await asyncio.sleep(0)
