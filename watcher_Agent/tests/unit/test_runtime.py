"""Runtime primitives: clock, rate limiting, media budget, bounded queue."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from judge.devfakes.clock import FakeClock
from judge.runtime.clock import SystemClock
from judge.runtime.media_budget import MediaBudget
from judge.runtime.rate_limiter import DailyQuota, KeyedRateLimiter, TokenBucket
from judge.runtime.worker_queue import WorkerQueue

pytestmark = pytest.mark.unit


def test_system_clock_is_timezone_aware_utc() -> None:
    now = SystemClock().now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


async def test_fake_clock_sleep_advances_time_without_waiting() -> None:
    clock = FakeClock(datetime(2026, 11, 14, tzinfo=UTC))
    start_wall, start_mono = clock.now(), clock.monotonic()

    await clock.sleep(600)

    assert clock.now() - start_wall == timedelta(seconds=600)
    assert clock.monotonic() - start_mono == 600


def test_token_bucket_allows_a_burst_up_to_capacity_then_waits() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=2, refill_per_second=1.0, clock=clock)

    assert bucket.try_reserve(max_wait_seconds=0) == 0
    assert bucket.try_reserve(max_wait_seconds=0) == 0
    assert bucket.try_reserve(max_wait_seconds=0) is None
    assert bucket.try_reserve(max_wait_seconds=5) == pytest.approx(1.0)


def test_token_bucket_refills_with_time_but_never_above_capacity() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=2, refill_per_second=1.0, clock=clock)
    bucket.try_reserve(max_wait_seconds=0)
    bucket.try_reserve(max_wait_seconds=0)

    clock.advance(100)

    assert bucket.try_reserve(max_wait_seconds=0) == 0
    assert bucket.try_reserve(max_wait_seconds=0) == 0
    assert bucket.try_reserve(max_wait_seconds=0) is None


def test_token_bucket_refuses_when_wait_exceeds_the_deadline() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, refill_per_second=0.1, clock=clock)
    bucket.try_reserve(max_wait_seconds=0)

    assert bucket.try_reserve(max_wait_seconds=5) is None
    # A refused reservation must not consume anything.
    assert bucket.try_reserve(max_wait_seconds=10) == pytest.approx(10.0)


def test_token_bucket_per_minute_factory() -> None:
    clock = FakeClock()
    bucket = TokenBucket.per_minute(10, clock)

    grants = [bucket.try_reserve(max_wait_seconds=0) for _ in range(11)]

    assert grants[:10] == [0] * 10
    assert grants[10] is None


def test_daily_quota_stops_at_the_limit_and_resets_next_utc_day() -> None:
    clock = FakeClock(datetime(2026, 11, 14, 23, 59, tzinfo=UTC))
    quota = DailyQuota(limit=2, clock=clock)

    assert [quota.try_consume() for _ in range(3)] == [True, True, False]
    clock.advance(120)

    assert quota.try_consume() is True


def test_daily_quota_without_a_limit_always_allows() -> None:
    quota = DailyQuota(limit=None, clock=FakeClock())

    assert all(quota.try_consume() for _ in range(1000))


def test_keyed_rate_limiter_isolates_owners() -> None:
    clock = FakeClock()
    limiter = KeyedRateLimiter(per_minute=1, clock=clock)

    assert limiter.allow("owner-a") is True
    assert limiter.allow("owner-a") is False
    assert limiter.allow("owner-b") is True


def test_media_budget_enforces_session_and_process_limits() -> None:
    budget = MediaBudget(total_limit=100, session_limit=60)
    a, b = uuid4(), uuid4()

    assert budget.try_reserve(a, 60) is True
    assert budget.try_reserve(a, 1) is False
    assert budget.try_reserve(b, 40) is True
    assert budget.try_reserve(b, 1) is False
    assert budget.used == 100
    assert budget.session_used(a) == 60


def test_media_budget_release_returns_capacity_and_forgets_empty_sessions() -> None:
    budget = MediaBudget(total_limit=100, session_limit=60)
    session = uuid4()
    budget.try_reserve(session, 50)

    budget.release(session, 50)

    assert budget.used == 0
    assert budget.session_used(session) == 0
    assert budget.tracked_sessions == 0


def test_media_budget_release_never_goes_negative() -> None:
    budget = MediaBudget(total_limit=100, session_limit=60)
    session = uuid4()
    budget.try_reserve(session, 10)

    budget.release(session, 500)

    assert budget.used == 0


def test_media_budget_rejects_negative_amounts() -> None:
    budget = MediaBudget(total_limit=100, session_limit=60)

    with pytest.raises(ValueError, match="non-negative"):
        budget.try_reserve(uuid4(), -1)


async def test_worker_queue_is_bounded_and_reports_overflow() -> None:
    queue: WorkerQueue[int] = WorkerQueue(capacity=2)

    assert queue.try_submit(1) is True
    assert queue.try_submit(2) is True
    assert queue.try_submit(3) is False
    assert queue.depth == 2


async def test_worker_queue_processes_items_with_bounded_concurrency() -> None:
    queue: WorkerQueue[int] = WorkerQueue(capacity=8)
    seen: list[int] = []
    running = 0
    peak = 0

    async def handler(item: int) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        seen.append(item)
        running -= 1

    queue.start(handler, concurrency=2)
    for item in range(6):
        assert queue.try_submit(item)
    await queue.join()
    await queue.stop()

    assert sorted(seen) == list(range(6))
    assert peak <= 2


async def test_worker_queue_survives_a_failing_handler() -> None:
    queue: WorkerQueue[int] = WorkerQueue(capacity=4)
    seen: list[int] = []

    async def handler(item: int) -> None:
        if item == 1:
            raise RuntimeError("synthetic failure")
        seen.append(item)

    queue.start(handler, concurrency=1)
    for item in (1, 2, 3):
        queue.try_submit(item)
    await queue.join()
    await queue.stop()

    assert seen == [2, 3]


async def test_worker_queue_stop_returns_unprocessed_items_for_cleanup() -> None:
    queue: WorkerQueue[int] = WorkerQueue(capacity=4)
    for item in (1, 2, 3):
        queue.try_submit(item)

    leftover = await queue.stop()

    assert leftover == [1, 2, 3]
    assert queue.depth == 0
