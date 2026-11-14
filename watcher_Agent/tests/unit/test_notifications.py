"""Notification policy, ordered accounting and outbox delivery.

Defaults: batches of 15 judged reels, at least 3 conflicts, 600-second
user-scoped cooldown. Warnings explain conflicts with approved values; they
never suppress content and never call it universally bad.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from judge.contracts.notification import (
    BatchOutcome,
    BatchStatus,
    NotificationBatch,
    NotificationPolicyUpdate,
    OutboxItem,
    OutboxStatus,
)
from judge.contracts.record import EvaluationStatus
from judge.contracts.understanding import ModelJudgment, Verdict
from judge.devfakes.providers import fake_judgment
from judge.errors import ProviderError, ProviderErrorKind
from judge.notifications.policy import decide_batch, is_cooling_down
from judge.notifications.text import notification_text
from judge.understanding.prompt import UnderstandingRequest
from tests.support.builders import SESSION_ID
from tests.support.harness import SERVER_START, Harness
from tests.support.records import make_failed, make_judged, make_pending, make_session

pytestmark = pytest.mark.unit

OTHER_SESSION = UUID("22222222-2222-4222-8222-222222222222")
THIRD_SESSION = UUID("33333333-3333-4333-8333-333333333333")
EXPECTED_BODY = "3 of your last 15 reels went against what you told me you wanted."
# Nothing is finalized until the session ends, so a whole feed is judged in
# one burst with no ingest in between.
HELD_UNTIL_SESSION_END: dict[str, Any] = {
    "final_audio_wait_seconds": 100_000,
    "job_deadline_seconds": 200_000,
    "provider_requests_per_minute": 1000,
}


def _feed(conflicts: int, size: int = 15) -> list[str]:
    """``size`` verdicts with ``conflicts`` conflicts spread through them."""
    verdicts = ["neutral"] * size
    for index in range(conflicts):
        verdicts[index * (size // max(conflicts, 1))] = "conflict"
    return verdicts


@pytest.fixture
async def h() -> AsyncIterator[Harness]:
    harness = await Harness.create()
    yield harness
    await harness.close()


async def _batches(
    h: Harness, session_id: UUID = SESSION_ID
) -> list[NotificationBatch]:
    async with h.runtime.storage.transaction() as tx:
        return await tx.notifications.list_batches(h.owner, session_id)


async def _outbox(h: Harness) -> list[OutboxItem]:
    async with h.runtime.storage.transaction() as tx:
        return await tx.notifications.list_outbox(h.owner)


# --- pure policy ------------------------------------------------------------


@pytest.mark.parametrize(
    ("conflicts", "cooling", "pending", "expected"),
    [
        (3, False, False, BatchOutcome.TRIGGERED),
        (15, False, False, BatchOutcome.TRIGGERED),
        (2, False, False, BatchOutcome.BELOW_THRESHOLD),
        (0, True, True, BatchOutcome.BELOW_THRESHOLD),
        (3, True, False, BatchOutcome.SUPPRESSED_COOLDOWN),
        (3, False, True, BatchOutcome.SUPPRESSED_PENDING),
        (3, True, True, BatchOutcome.SUPPRESSED_PENDING),
    ],
)
def test_batch_decision_table(
    conflicts: int, cooling: bool, pending: bool, expected: BatchOutcome
) -> None:
    outcome = decide_batch(
        conflict_count=conflicts,
        minimum_conflicts=3,
        cooling_down=cooling,
        has_pending=pending,
    )

    assert outcome is expected


def test_cooldown_is_measured_from_the_last_dispatch() -> None:
    last = SERVER_START

    assert is_cooling_down(last + timedelta(seconds=599), last, 600) is True
    assert is_cooling_down(last + timedelta(seconds=600), last, 600) is False
    assert is_cooling_down(last, None, 600) is False


def test_warning_text_is_relative_to_the_user_and_never_says_bad() -> None:
    title, body = notification_text(conflict_count=3, batch_size=15)

    assert body == EXPECTED_BODY
    assert title
    assert "bad" not in f"{title} {body}".lower()
    assert "what you told me you wanted" in body


# --- triggering -------------------------------------------------------------


async def test_exactly_three_conflicts_in_fifteen_trigger_one_warning(
    h: Harness,
) -> None:
    offset = await h.play_feed(_feed(conflicts=3))

    response = await h.frame(offset, 10_000)

    reels = (await h.reels())[:15]
    conflict_ids = [r.id for r in reels if r.verdict is Verdict.CONFLICT]
    assert response.notify is not None
    assert response.notify.body == EXPECTED_BODY
    assert list(response.notify.reel_ids) == conflict_ids
    assert response.notify.expires_at > h.clock.now()
    batches = await _batches(h)
    assert [(b.outcome, b.judged_count, b.conflict_count) for b in batches[:1]] == [
        (BatchOutcome.TRIGGERED, 15, 3)
    ]
    assert len(await _outbox(h)) == 1


async def test_two_conflicts_in_fifteen_do_not_trigger(h: Harness) -> None:
    offset = await h.play_feed(_feed(conflicts=2))

    response = await h.frame(offset, 10_000)

    assert response.notify is None
    assert (await _batches(h))[0].outcome is BatchOutcome.BELOW_THRESHOLD
    assert await _outbox(h) == []


async def test_fourteen_judged_reels_do_not_evaluate_the_batch(h: Harness) -> None:
    offset = await h.play_feed(["conflict"] * 14)

    response = await h.frame(offset, 10_000)

    batch = (await _batches(h))[0]
    assert response.notify is None
    assert batch.status is BatchStatus.OPEN and batch.judged_count == 14


async def test_batches_do_not_overlap(h: Harness) -> None:
    # 18 reels: conflicts at positions 13, 14 (batch 1) and 16, 17, 18 (batch 2).
    verdicts = ["neutral"] * 12 + ["conflict"] * 2 + ["neutral"] + ["conflict"] * 3
    await h.play_feed(verdicts)

    first, second = await _batches(h)
    assert (first.judged_count, first.conflict_count) == (15, 2)
    assert first.outcome is BatchOutcome.BELOW_THRESHOLD
    assert (second.judged_count, second.conflict_count) == (3, 3)
    assert second.status is BatchStatus.OPEN


# --- delivery through ingest responses -------------------------------------


async def test_warning_is_delivered_through_the_audio_endpoint_too(
    h: Harness,
) -> None:
    offset = await h.play_feed(_feed(conflicts=3))

    response = await h.audio(offset, seconds=2.0)

    assert response.notify is not None


async def test_acknowledged_warning_is_never_sent_again(h: Harness) -> None:
    offset = await h.play_feed(_feed(conflicts=3))
    first = await h.frame(offset, 10_000)
    assert first.notify is not None
    notification_id = first.notify.notification_id

    acked = await h.frame(offset + 2000, 10_000, acks=[notification_id])
    h.clock.advance(60)
    later = await h.frame(offset + 62_000, 10_000, acks=[notification_id, uuid4()])

    assert acked.notify is None and later.notify is None
    item = (await _outbox(h))[0]
    assert item.status is OutboxStatus.ACKNOWLEDGED
    assert item.dispatch_attempts == 1


async def test_lost_response_is_redelivered_with_the_same_id_after_the_lease(
    h: Harness,
) -> None:
    offset = await h.play_feed(_feed(conflicts=3))
    first = await h.frame(offset, 10_000)
    assert first.notify is not None

    within_lease = await h.frame(offset + 2000, 10_000)
    again = await h.frame(offset + 40_000, 10_000)

    assert within_lease.notify is None
    assert again.notify is not None
    assert again.notify.notification_id == first.notify.notification_id
    assert again.notify.expires_at == first.notify.expires_at
    assert (await _outbox(h))[0].dispatch_attempts == 2


async def test_redelivery_is_bounded_and_ends_unacknowledged() -> None:
    h = await Harness.create(notification_ttl_seconds=10_000)
    try:
        offset = await h.play_feed(_feed(conflicts=3))
        seen = []
        for step in range(6):
            response = await h.frame(offset + step * 40_000, 10_000)
            seen.append(response.notify is not None)

        assert seen == [True, True, True, False, False, False]
        item = (await _outbox(h))[0]
        assert item.status is OutboxStatus.UNACKNOWLEDGED
        assert item.dispatch_attempts == 3
    finally:
        await h.close()


async def test_undelivered_warning_expires_and_is_not_sent_stale() -> None:
    h = await Harness.create(**HELD_UNTIL_SESSION_END)
    try:
        # Nothing finalizes until the session ends, so no ingest follows the
        # evaluation and the warning cannot be delivered in time.
        await h.play_feed(_feed(conflicts=3))
        ended = await h.end_session()
        h.clock.advance(121)
        late = await h.frame(0, 1, session_id=OTHER_SESSION)

        assert ended.notify is None and late.notify is None
        assert (await _outbox(h))[0].status is OutboxStatus.EXPIRED
    finally:
        await h.close()


async def test_pending_warning_reaches_the_next_session_before_it_expires() -> None:
    h = await Harness.create(**HELD_UNTIL_SESSION_END)
    try:
        await h.play_feed(_feed(conflicts=3))
        await h.end_session()
        h.clock.advance(30)

        response = await h.frame(0, 1, session_id=OTHER_SESSION)

        assert response.notify is not None
        assert response.notify.body == EXPECTED_BODY
    finally:
        await h.close()


async def test_second_triggering_batch_is_suppressed_while_one_is_pending() -> None:
    h = await Harness.create(**HELD_UNTIL_SESSION_END)
    try:
        await h.play_feed(_feed(conflicts=3) + _feed(conflicts=5))
        await h.end_session()

        first, second = (await _batches(h))[:2]
        assert first.outcome is BatchOutcome.TRIGGERED
        assert second.outcome is BatchOutcome.SUPPRESSED_PENDING
        assert len(await _outbox(h)) == 1
    finally:
        await h.close()


# --- cooldown ---------------------------------------------------------------


async def test_cooldown_suppresses_and_never_defers_then_lifts_after_600_s(
    h: Harness,
) -> None:
    offset = await h.play_feed(_feed(conflicts=3))
    first = await h.frame(offset, 10_000)
    assert first.notify is not None
    await h.frame(offset + 2000, 10_000, acks=[first.notify.notification_id])

    # A second batch with conflicts completes about 90 s later: suppressed.
    offset = await h.play_feed(
        _feed(conflicts=4), start_ms=offset + 4000, first_scene=50
    )
    during = await h.frame(offset, 10_050)
    # Well after the cooldown, in a new session: the old batch is not replayed.
    h.clock.advance(600)
    quiet = await h.frame(h.now_offset_ms(), 1, session_id=OTHER_SESSION)
    # A fresh batch after the cooldown triggers again (cooldown is per user).
    offset = await h.play_feed(
        _feed(conflicts=3),
        start_ms=h.now_offset_ms() + 2000,
        first_scene=100,
        session_id=THIRD_SESSION,
    )
    after = await h.frame(offset, 10_100, session_id=THIRD_SESSION)

    assert during.notify is None and quiet.notify is None
    assert (await _batches(h))[1].outcome is BatchOutcome.SUPPRESSED_COOLDOWN
    assert after.notify is not None
    assert after.notify.notification_id != first.notify.notification_id
    assert [i.status for i in await _outbox(h)].count(OutboxStatus.LEASED) == 1


# --- ordered accounting -----------------------------------------------------


async def test_failed_observations_are_excluded_but_counted_as_coverage(
    h: Harness,
) -> None:
    def script(request: UnderstandingRequest) -> ModelJudgment | Exception:
        scene = h.scene_of(request)
        if scene in {2, 7}:
            return ProviderError(ProviderErrorKind.PERMANENT, "synthetic")
        verdict = "conflict" if scene in {1, 9, 17} else "neutral"
        return fake_judgment(request, verdict)

    h.script = script
    offset = await h.play_feed(["neutral"] * 17)
    response = await h.frame(offset, 10_000)

    reels = (await h.reels())[:17]
    batch = (await _batches(h))[0]
    assert [r.evaluation_status for r in reels].count(
        EvaluationStatus.FAILED_PROVIDER
    ) == 2
    assert (batch.judged_count, batch.conflict_count, batch.excluded_count) == (
        15,
        3,
        2,
    )
    assert batch.outcome is BatchOutcome.TRIGGERED
    assert response.notify is not None
    assert list(response.notify.reel_ids) == [reels[0].id, reels[8].id, reels[16].id]


async def test_accounting_follows_capture_order_not_completion_order(
    h: Harness,
) -> None:
    session = make_session(owner_id=h.owner)
    pending = [make_pending(session, order_seq=i) for i in (1, 2, 3)]
    async with h.runtime.storage.transaction() as tx:
        await tx.sessions.insert(session)
        for reel in pending:
            await tx.reels.insert_pending(reel)
    complete = h.runtime.completion.complete

    await complete(make_judged(pending[2], Verdict.CONFLICT))
    await complete(make_failed(pending[1]))
    async with h.runtime.storage.transaction() as tx:
        waiting = await tx.sessions.get(session.session_id)
        no_batch = await tx.notifications.get_open_batch(session.session_id)
    await complete(make_judged(pending[0], Verdict.ALIGNED))
    async with h.runtime.storage.transaction() as tx:
        done = await tx.sessions.get(session.session_id)
        batch = await tx.notifications.get_open_batch(session.session_id)
        conflicts = await tx.reels.list_batch_conflict_ids(batch.batch_id)  # type: ignore[union-attr]

    assert waiting is not None and waiting.accounting_cursor == 0
    assert no_batch is None
    assert done is not None and done.accounting_cursor == 3
    assert (done.judged_count, done.conflict_count, done.excluded_count) == (2, 1, 1)
    assert batch is not None
    assert (batch.judged_count, batch.excluded_count) == (2, 1)
    assert conflicts == [pending[2].reel_id]


async def test_each_reel_is_counted_exactly_once(h: Harness) -> None:
    session = make_session(owner_id=h.owner)
    reel = make_pending(session)
    async with h.runtime.storage.transaction() as tx:
        await tx.sessions.insert(session)
        await tx.reels.insert_pending(reel)

    first = await h.runtime.completion.complete(make_judged(reel, Verdict.CONFLICT))
    second = await h.runtime.completion.complete(make_judged(reel, Verdict.CONFLICT))

    async with h.runtime.storage.transaction() as tx:
        loaded = await tx.sessions.get(session.session_id)
    assert (first, second) == (True, False)
    assert loaded is not None and loaded.conflict_count == 1


async def test_incomplete_session_tail_produces_no_warning(h: Harness) -> None:
    await h.play_feed(["conflict"] * 10)

    ended = await h.end_session()
    await h.settle()

    batch = (await _batches(h))[0]
    assert ended.notify is None
    assert batch.outcome is BatchOutcome.INCOMPLETE_TAIL
    assert batch.conflict_count == 10
    assert await _outbox(h) == []


# --- policy configuration ---------------------------------------------------


async def test_default_policy_is_fifteen_three_six_hundred(h: Harness) -> None:
    policy = await h.runtime.policy.get(h.owner)

    assert (policy.batch_size, policy.minimum_conflicts) == (15, 3)
    assert policy.cooldown_seconds == 600
    assert policy.version == 0


async def test_policy_change_is_versioned_and_applies_at_the_next_batch(
    h: Harness,
) -> None:
    # Two conflicts open batch 1 under the default policy (15 / 3 / 600).
    offset = await h.play_feed(["conflict"] * 2)
    update = NotificationPolicyUpdate(
        batch_size=5, minimum_conflicts=1, cooldown_seconds=0
    )

    changed = await h.runtime.policy.update(h.owner, update)
    # The trailing reel plus 12 neutral reels fill batch 1 (still 15 wide);
    # the five conflicts after that form batch 2 under the new policy.
    offset = await h.play_feed(
        ["neutral"] * 12 + ["conflict"] * 5, start_ms=offset + 2000, first_scene=40
    )

    first, second = (await _batches(h))[:2]
    assert changed.version == 1
    assert (first.batch_size, first.policy_version, first.judged_count) == (15, 0, 15)
    assert (second.batch_size, second.policy_version) == (5, 1)
    assert second.outcome is BatchOutcome.TRIGGERED
    assert (await h.runtime.policy.get(h.owner)).version == 1
