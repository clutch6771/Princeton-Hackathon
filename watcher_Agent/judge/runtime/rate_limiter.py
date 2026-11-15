"""Rate limiting: token bucket, daily quota and a per-key request limiter.

The limiters are local guards. They do not prove what a provider's own quota
is; verify free-tier limits in the provider console. There is no paid
fallback: when a local limit is hit the work fails explicitly.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from judge.runtime.clock import Clock

SECONDS_PER_MINUTE = 60.0


class RateLimiter(Protocol):
    """Capability: reserve one unit of provider capacity."""

    def try_reserve(self, *, max_wait_seconds: float) -> float | None:
        """Reserve one unit.

        Returns the seconds to wait before using it (0 means now), or ``None``
        when the wait would exceed ``max_wait_seconds``. A refusal consumes
        nothing.
        """
        ...


class TokenBucket:
    """Token bucket that may lend one token against a bounded wait."""

    def __init__(
        self, *, capacity: int, refill_per_second: float, clock: Clock
    ) -> None:
        if capacity <= 0 or refill_per_second <= 0:
            msg = "capacity and refill_per_second must be positive"
            raise ValueError(msg)
        self._capacity = float(capacity)
        self._refill = refill_per_second
        self._clock = clock
        self._tokens = float(capacity)
        self._updated = clock.monotonic()

    @classmethod
    def per_minute(cls, requests: int, clock: Clock) -> TokenBucket:
        """Bucket allowing ``requests`` per minute, with an equal burst."""
        return cls(
            capacity=requests,
            refill_per_second=requests / SECONDS_PER_MINUTE,
            clock=clock,
        )

    def _refresh(self) -> None:
        now = self._clock.monotonic()
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill)
        self._updated = now

    def try_reserve(self, *, max_wait_seconds: float) -> float | None:
        self._refresh()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        wait = (1.0 - self._tokens) / self._refill
        if wait > max_wait_seconds:
            return None
        self._tokens -= 1.0
        return wait


class DailyQuota:
    """Optional cap on provider requests per UTC day."""

    def __init__(self, *, limit: int | None, clock: Clock) -> None:
        self._limit = limit
        self._clock = clock
        self._day: date | None = None
        self._used = 0

    def try_consume(self) -> bool:
        """Count one request; False once today's limit is reached."""
        if self._limit is None:
            return True
        today = self._clock.now().date()
        if today != self._day:
            self._day = today
            self._used = 0
        if self._used >= self._limit:
            return False
        self._used += 1
        return True


class KeyedRateLimiter:
    """Independent per-key request limiters (for example per owner)."""

    def __init__(self, *, per_minute: int, clock: Clock) -> None:
        self._per_minute = per_minute
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, key: str) -> bool:
        """True when ``key`` may make one more request right now."""
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket.per_minute(self._per_minute, self._clock)
            self._buckets[key] = bucket
        return bucket.try_reserve(max_wait_seconds=0) is not None
