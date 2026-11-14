"""Session lifecycle, ingestion admission rules, overload and recovery."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from judge.contracts.notification import AckStatus
from judge.contracts.record import EvaluationStatus
from judge.contracts.session import (
    SessionEndReason,
    SessionEndRequest,
    SessionStatus,
)
from judge.devfakes.memory_storage import MemoryStorage
from judge.errors import (
    ClockSkewError,
    NotFoundError,
    OverloadedError,
    PrivacyCapabilityUnavailableError,
    SessionEndedError,
    SourceConflictError,
    UnsupportedMediaError,
)
from tests.support.builders import SESSION_ID, privacy
from tests.support.harness import Harness
from tests.support.media import make_png
from tests.support.records import make_pending, make_session

pytestmark = pytest.mark.unit

OTHER_SESSION = UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture
async def h() -> AsyncIterator[Harness]:
    harness = await Harness.create()
    yield harness
    await harness.close()


# --- admission --------------------------------------------------------------


async def test_first_valid_ingest_creates_the_session_with_the_client_uuid(
    h: Harness,
) -> None:
    response = await h.frame(0, 1)

    async with h.runtime.storage.transaction() as tx:
        session = await tx.sessions.get(SESSION_ID)
    assert response.status is AckStatus.ACCEPTED
    assert response.session_id == SESSION_ID
    assert session is not None
    assert session.status is SessionStatus.ACTIVE
    assert session.owner_id == h.owner


async def test_duplicate_event_is_acknowledged_once_and_not_processed_twice(
    h: Harness,
) -> None:
    event_id = uuid4()
    first = await h.frame(0, 1, event_id=str(event_id))

    second = await h.frame(0, 1, event_id=str(event_id))

    state = h.runtime.volatile.get(SESSION_ID)
    assert first.status is AckStatus.ACCEPTED
    assert second.status is AckStatus.DUPLICATE
    assert second.prior_outcome == "accepted"
    assert second.admitted_by_current_process is True
    assert state is not None and state.open is not None
    assert state.open.frame_count == 1


async def test_out_of_order_frame_is_acknowledged_as_late_and_dropped(
    h: Harness,
) -> None:
    await h.frame(10_000, 1)
    used_before = h.runtime.budget.used

    late = await h.frame(4000, 1)

    assert late.status is AckStatus.LATE_DISCARDED
    assert h.runtime.budget.used == used_before


async def test_capture_time_ahead_of_the_session_clock_is_rejected(
    h: Harness,
) -> None:
    await h.frame(0, 1)
    h.clock.advance(1)

    with pytest.raises(ClockSkewError):
        observation_offset = 120_000
        # Bypass the harness clock sync: the server clock stays where it is.
        h._sync_clock = lambda offset_ms: None  # type: ignore[method-assign]
        await h.frame(observation_offset, 1)


async def test_second_capture_source_cannot_join_a_bound_session(h: Harness) -> None:
    await h.frame(0, 1)

    with pytest.raises(SourceConflictError):
        await h.frame(2000, 1, source_id="android-capture-synthetic")


async def test_non_jpeg_upload_is_rejected_without_creating_a_session(
    h: Harness,
) -> None:
    with pytest.raises(UnsupportedMediaError):
        await h.frame(0, 1, jpeg=make_png())

    async with h.runtime.storage.transaction() as tx:
        assert await tx.sessions.get(SESSION_ID) is None
    assert h.runtime.budget.used == 0


async def test_enforce_mode_rejects_uploads_without_a_native_gate() -> None:
    h = await Harness.create(privacy_mode="enforce")
    try:
        with pytest.raises(PrivacyCapabilityUnavailableError):
            await h.frame(0, 1, privacy=privacy(capability="dev_fake"))
        accepted = await h.frame(0, 1)

        assert accepted.status is AckStatus.ACCEPTED
        assert h.understanding.calls == []
    finally:
        await h.close()


async def test_receipts_store_metadata_and_never_the_payload(h: Harness) -> None:
    event_id = uuid4()
    await h.frame(0, 1, event_id=str(event_id))

    async with h.runtime.storage.transaction() as tx:
        receipt = await tx.receipts.get(event_id)
    assert receipt is not None
    assert receipt.outcome == "accepted"
    assert receipt.byte_length > 0
    assert all(
        not isinstance(v, bytes | bytearray) for v in receipt.model_dump().values()
    )


# --- overload ---------------------------------------------------------------


async def test_media_budget_overflow_is_an_explicit_503_not_an_offload() -> None:
    h = await Harness.create(session_media_budget_bytes=2000)
    try:
        with pytest.raises(OverloadedError) as excinfo:
            await h.frame(0, 1)

        assert excinfo.value.retry_after_seconds is not None
        assert h.runtime.budget.used == 0
    finally:
        await h.close()


async def test_too_many_active_sessions_is_an_explicit_overload() -> None:
    h = await Harness.create(max_active_sessions=1)
    try:
        await h.frame(0, 1)

        with pytest.raises(OverloadedError):
            await h.frame(0, 1, session_id=OTHER_SESSION)
    finally:
        await h.close()


async def test_full_worker_queue_records_a_skipped_reel_and_frees_its_media() -> None:
    h = await Harness.create(worker_queue_capacity=1)
    try:
        await h.runtime.queue.stop()
        for scene, start in ((1, 0), (2, 6000), (3, 12_000)):
            await h.play_reel(scene, start, audio=False)
        await h.frame(18_000, 4)
        h.clock.advance(7)
        await h.runtime.maintenance.tick()

        statuses = [r.evaluation_status for r in await h.reels()]
        assert statuses.count(EvaluationStatus.SKIPPED_OVERLOAD) == 2
        skipped = [r for r in await h.reels() if r.error_code == "queue_full"]
        assert len(skipped) == 2
        assert all(r.verdict is None for r in skipped)
    finally:
        await h.close()


# --- session end ------------------------------------------------------------


async def test_session_end_flushes_the_tail_and_writes_one_lifecycle_event(
    h: Harness,
) -> None:
    h.verdict(2, "conflict")
    await h.play_reel(1, 0, audio=True)
    await h.play_reel(2, 6000, audio=True)

    response = await h.end_session()
    await h.settle()

    reels = await h.reels()
    async with h.runtime.storage.transaction() as tx:
        session = await tx.sessions.get(SESSION_ID)
        events = await tx.sessions.list_events(h.owner, SESSION_ID)
    assert response.status in {SessionStatus.ENDING, SessionStatus.ENDED}
    assert [r.evaluation_status for r in reels] == [EvaluationStatus.JUDGED] * 2
    assert session is not None
    assert session.status is SessionStatus.ENDED
    assert session.end_reason is SessionEndReason.CLIENT_END
    assert [e.event_type for e in events] == ["session_completed"]
    payload = events[0].payload
    assert (payload.reel_count, payload.judged_count) == (2, 2)
    assert payload.conflict_count == 1
    assert len(h.understanding.calls) == 2


async def test_session_end_is_idempotent(h: Harness) -> None:
    await h.play_reel(1, 0)
    await h.end_session()
    await h.settle()

    again = await h.end_session()

    async with h.runtime.storage.transaction() as tx:
        events = await tx.sessions.list_events(h.owner, SESSION_ID)
    assert again.status is SessionStatus.ENDED
    assert len(events) == 1
    assert len(await h.reels()) == 1


async def test_ended_session_cannot_be_reused(h: Harness) -> None:
    await h.frame(0, 1)
    await h.end_session()

    with pytest.raises(SessionEndedError):
        await h.frame(2000, 1)


async def test_ending_an_unknown_or_foreign_session_is_not_found(h: Harness) -> None:
    with pytest.raises(NotFoundError):
        await h.end_session(uuid4())

    foreign = make_session(owner_id="someone-else-synthetic")
    async with h.runtime.storage.transaction() as tx:
        await tx.sessions.insert(foreign)
    with pytest.raises(NotFoundError):
        await h.runtime.lifecycle.end_session(
            h.owner, SessionEndRequest(session_id=foreign.session_id)
        )


async def test_session_with_no_reels_completes_immediately(h: Harness) -> None:
    await h.audio(0, seconds=2.0)

    response = await h.end_session()

    assert response.status is SessionStatus.ENDED
    assert await h.reels() == []


# --- idle timeout -----------------------------------------------------------


async def test_session_closes_after_600_seconds_without_accepted_ingest(
    h: Harness,
) -> None:
    await h.play_reel(1, 0)

    h.clock.advance(590)
    await h.runtime.maintenance.tick()
    async with h.runtime.storage.transaction() as tx:
        before = await tx.sessions.get(SESSION_ID)
    h.clock.advance(10)
    await h.settle(wait_seconds=0)

    async with h.runtime.storage.transaction() as tx:
        after = await tx.sessions.get(SESSION_ID)
        events = await tx.sessions.list_events(h.owner, SESSION_ID)
    assert before is not None and before.status is SessionStatus.ACTIVE
    assert after is not None and after.status is SessionStatus.ENDED
    assert after.end_reason is SessionEndReason.IDLE_TIMEOUT
    assert len(events) == 1
    assert [r.evaluation_status for r in await h.reels()] == [EvaluationStatus.JUDGED]
    assert h.runtime.budget.used == 0


async def test_privacy_excluded_uploads_do_not_keep_a_session_alive(
    h: Harness,
) -> None:
    await h.frame(0, 1)
    h.clock.advance(400)
    await h.frame(2000, 1, privacy=privacy(eligibility="ineligible", surface="other"))
    h.clock.advance(250)

    await h.settle(wait_seconds=0)

    async with h.runtime.storage.transaction() as tx:
        session = await tx.sessions.get(SESSION_ID)
    assert session is not None and session.status is SessionStatus.ENDED


# --- deadlines and restart --------------------------------------------------


async def test_stuck_pending_reel_is_failed_so_the_cursor_cannot_block(
    h: Harness,
) -> None:
    session = make_session(owner_id=h.owner)
    stuck = make_pending(session, order_seq=1, created_at=h.clock.now())
    async with h.runtime.storage.transaction() as tx:
        await tx.sessions.insert(session)
        await tx.reels.insert_pending(stuck)

    h.clock.advance(34)
    await h.runtime.maintenance.tick()
    async with h.runtime.storage.transaction() as tx:
        early = await tx.reels.get(h.owner, stuck.reel_id)
    h.clock.advance(2)
    await h.runtime.maintenance.tick()

    async with h.runtime.storage.transaction() as tx:
        record = await tx.reels.get(h.owner, stuck.reel_id)
        loaded = await tx.sessions.get(session.session_id)
    assert early is not None and early.evaluation_status is EvaluationStatus.PENDING
    assert record is not None
    assert record.evaluation_status is EvaluationStatus.FAILED_TIMEOUT
    assert record.error_code == "job_deadline"
    assert loaded is not None
    assert (loaded.accounting_cursor, loaded.excluded_count) == (1, 1)


async def test_restart_marks_lost_media_instead_of_pretending_to_recover() -> None:
    storage = MemoryStorage()
    crashed = await Harness.create(
        storage=storage, final_audio_wait_seconds=100_000, job_deadline_seconds=200_000
    )
    event_id = uuid4()
    await crashed.frame(0, 1, event_id=str(event_id))
    await crashed.play_reel(1, 2000, audio=False)
    await crashed.frame(8000, 2)
    # The process dies here: one reel is pending, its media was only in RAM.
    await crashed.runtime.queue.stop()

    restarted = await Harness.create(storage=storage)
    try:
        lost = (await restarted.reels())[0]
        duplicate = await restarted.frame(0, 1, event_id=str(event_id))

        assert lost.evaluation_status is EvaluationStatus.LOST_ON_RESTART
        assert lost.verdict is None
        assert lost.error_code == "restart"
        assert duplicate.status is AckStatus.DUPLICATE
        assert duplicate.admitted_by_current_process is False
        assert restarted.understanding.calls == []
    finally:
        await restarted.close()
