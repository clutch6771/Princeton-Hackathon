"""Repository contract: identical behaviour for every storage backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from judge.contracts.notification import (
    BatchOutcome,
    BatchStatus,
    OutboxStatus,
)
from judge.contracts.record import (
    EmbeddingStatus,
    EvaluationStatus,
    PendingReel,
    StageTimings,
)
from judge.contracts.session import (
    SessionEndReason,
    SessionRecord,
    SessionStatus,
)
from judge.contracts.storage import TimelineCursor
from judge.contracts.understanding import Verdict
from judge.persistence.interfaces import Storage
from tests.support.records import (
    NOW,
    OTHER_OWNER,
    OWNER,
    PROFILE,
    VALUES_SHA,
    at,
    make_batch,
    make_failed,
    make_feedback,
    make_judged,
    make_outbox,
    make_pending,
    make_receipt,
    make_session,
    make_session_event,
    make_thumbnail,
)


@dataclass(frozen=True)
class Seeded:
    session: SessionRecord
    reels: tuple[PendingReel, ...]


async def seed(storage: Storage, reels: int = 0, **session_overrides: Any) -> Seeded:
    """Insert one session and ``reels`` pending reels in capture order."""
    session = make_session(**session_overrides)
    pending = tuple(make_pending(session, order_seq=i + 1) for i in range(reels))
    async with storage.transaction() as tx:
        assert await tx.sessions.insert(session)
        for reel in pending:
            assert await tx.reels.insert_pending(reel)
    return Seeded(session, pending)


# --- sessions ---------------------------------------------------------------


async def test_session_round_trip_and_duplicate_insert(storage: Storage) -> None:
    seeded = await seed(storage)

    async with storage.transaction() as tx:
        loaded = await tx.sessions.get(seeded.session.session_id)
        again = await tx.sessions.insert(seeded.session)

    assert loaded == seeded.session
    assert again is False


async def test_unknown_session_is_none(storage: Storage) -> None:
    async with storage.transaction() as tx:
        assert await tx.sessions.get(uuid4(), for_update=True) is None


async def test_touch_updates_last_accepted_ingest(storage: Storage) -> None:
    seeded = await seed(storage)

    async with storage.transaction() as tx:
        await tx.sessions.touch(seeded.session.session_id, at(42))
        loaded = await tx.sessions.get(seeded.session.session_id)

    assert loaded is not None and loaded.last_ingest_at == at(42)


async def test_order_sequence_is_allocated_monotonically(storage: Storage) -> None:
    seeded = await seed(storage)
    session_id = seeded.session.session_id

    async with storage.transaction() as tx:
        allocated = [await tx.sessions.allocate_order_seq(session_id) for _ in range(3)]
        loaded = await tx.sessions.get(session_id)

    assert allocated == [1, 2, 3]
    assert loaded is not None and loaded.next_order_seq == 4


async def test_accounting_and_status_are_saved(storage: Storage) -> None:
    seeded = await seed(storage)
    session_id = seeded.session.session_id

    async with storage.transaction() as tx:
        await tx.sessions.save_accounting(
            session_id, cursor=5, judged_count=4, conflict_count=2, excluded_count=1
        )
        await tx.sessions.set_status(
            session_id,
            SessionStatus.ENDED,
            ended_at=at(90),
            end_reason=SessionEndReason.IDLE_TIMEOUT,
        )
        loaded = await tx.sessions.get(session_id)

    assert loaded is not None
    assert (loaded.accounting_cursor, loaded.judged_count) == (5, 4)
    assert (loaded.conflict_count, loaded.excluded_count) == (2, 1)
    assert loaded.status is SessionStatus.ENDED
    assert loaded.ended_at == at(90)
    assert loaded.end_reason is SessionEndReason.IDLE_TIMEOUT


async def test_idle_listing_returns_only_stale_active_sessions(
    storage: Storage,
) -> None:
    stale = await seed(storage, last_ingest_at=at(-700))
    fresh = await seed(storage, last_ingest_at=at(-10))
    ended = await seed(
        storage, last_ingest_at=at(-900), status=SessionStatus.ENDED, ended_at=NOW
    )

    async with storage.transaction() as tx:
        idle = await tx.sessions.list_idle_active(at(-600))
        ended_list = await tx.sessions.list_by_status(SessionStatus.ENDED)

    assert [s.session_id for s in idle] == [stale.session.session_id]
    assert fresh.session.session_id not in {s.session_id for s in idle}
    assert [s.session_id for s in ended_list] == [ended.session.session_id]


async def test_lifecycle_event_is_written_once(storage: Storage) -> None:
    seeded = await seed(storage)
    event = make_session_event(seeded.session)

    async with storage.transaction() as tx:
        first = await tx.sessions.add_event(event)
        second = await tx.sessions.add_event(make_session_event(seeded.session))
        events = await tx.sessions.list_events(OWNER, seeded.session.session_id)
        foreign = await tx.sessions.list_events(OTHER_OWNER, seeded.session.session_id)

    assert (first, second) == (True, False)
    assert events == [event]
    assert foreign == []


async def test_failed_transaction_rolls_everything_back(storage: Storage) -> None:
    session = make_session()

    with pytest.raises(RuntimeError, match="synthetic"):
        async with storage.transaction() as tx:
            await tx.sessions.insert(session)
            await tx.reels.insert_pending(make_pending(session))
            raise RuntimeError("synthetic failure")

    async with storage.transaction() as tx:
        assert await tx.sessions.get(session.session_id) is None


async def test_deleting_a_session_removes_all_dependent_rows(storage: Storage) -> None:
    seeded = await seed(storage, reels=1)
    reel = seeded.reels[0]
    batch = make_batch(seeded.session)
    item = make_outbox(batch)
    receipt = make_receipt(seeded.session)
    async with storage.transaction() as tx:
        await tx.reels.complete(make_judged(reel, Verdict.CONFLICT))
        await tx.thumbnails.put(make_thumbnail(reel))
        await tx.embeddings.create_pending(reel.reel_id, OWNER, PROFILE, now=NOW)
        await tx.feedback.insert(make_feedback(reel))
        await tx.notifications.insert_batch(batch)
        await tx.notifications.insert_outbox(item)
        await tx.receipts.insert(receipt)
        await tx.sessions.add_event(make_session_event(seeded.session))

    async with storage.transaction() as tx:
        wrong_owner = await tx.sessions.delete(OTHER_OWNER, seeded.session.session_id)
        deleted = await tx.sessions.delete(OWNER, seeded.session.session_id)

    async with storage.transaction() as tx:
        assert (wrong_owner, deleted) == (False, True)
        assert await tx.sessions.get(seeded.session.session_id) is None
        assert await tx.reels.get(OWNER, reel.reel_id) is None
        assert await tx.thumbnails.get(OWNER, reel.reel_id) is None
        assert await tx.embeddings.get(reel.reel_id, PROFILE.profile_id) is None
        assert await tx.feedback.list_for_owner(OWNER, 10) == []
        assert await tx.notifications.list_outbox(OWNER) == []
        assert await tx.receipts.get(receipt.event_id) is None


async def test_delete_all_removes_only_that_owners_sessions(storage: Storage) -> None:
    mine = await seed(storage)
    theirs = await seed(storage, owner_id=OTHER_OWNER)

    async with storage.transaction() as tx:
        removed = await tx.sessions.delete_all(OWNER)
        mine_after = await tx.sessions.get(mine.session.session_id)
        theirs_after = await tx.sessions.get(theirs.session.session_id)

    assert removed == 1
    assert mine_after is None and theirs_after is not None


# --- receipts ---------------------------------------------------------------


async def test_receipt_is_idempotent_and_holds_metadata_only(storage: Storage) -> None:
    seeded = await seed(storage)
    receipt = make_receipt(seeded.session)

    async with storage.transaction() as tx:
        first = await tx.receipts.insert(receipt)
        second = await tx.receipts.insert(receipt)
        loaded = await tx.receipts.get(receipt.event_id)

    assert (first, second) == (True, False)
    assert loaded == receipt
    assert not any(isinstance(v, bytes) for v in receipt.model_dump().values())


async def test_old_receipts_are_pruned(storage: Storage) -> None:
    seeded = await seed(storage)
    old = make_receipt(seeded.session, received_at=at(-90_000))
    recent = make_receipt(seeded.session, received_at=at(-10))
    async with storage.transaction() as tx:
        await tx.receipts.insert(old)
        await tx.receipts.insert(recent)

    async with storage.transaction() as tx:
        pruned = await tx.receipts.prune(at(-86_400))
        assert await tx.receipts.get(old.event_id) is None
        assert await tx.receipts.get(recent.event_id) is not None

    assert pruned == 1


# --- reels ------------------------------------------------------------------


async def test_pending_reel_has_no_verdict_and_no_media(storage: Storage) -> None:
    seeded = await seed(storage, reels=1)
    pending = seeded.reels[0]

    async with storage.transaction() as tx:
        record = await tx.reels.get(OWNER, pending.reel_id)
        again = await tx.reels.insert_pending(pending)

    assert record is not None
    assert record.evaluation_status is EvaluationStatus.PENDING
    assert record.verdict is None and record.thumbnail_url is None
    assert int(record.start.timestamp() * 1000) == pending.start_ms
    assert int(record.end.timestamp() * 1000) == pending.end_ms
    assert record.idempotency_key == pending.idempotency_key
    assert again is False


async def test_completion_is_committed_exactly_once(storage: Storage) -> None:
    seeded = await seed(storage, reels=1)
    pending = seeded.reels[0]
    judged = make_judged(pending, Verdict.CONFLICT)

    async with storage.transaction() as tx:
        first = await tx.reels.complete(judged)
        second = await tx.reels.complete(make_failed(pending))
        record = await tx.reels.get(OWNER, pending.reel_id)

    assert (first, second) == (True, False)
    assert record is not None
    assert record.evaluation_status is EvaluationStatus.JUDGED
    assert record.verdict is Verdict.CONFLICT
    assert record.value_ids == ("V3",)
    assert record.features == judged.features
    assert record.coverage == judged.coverage
    assert record.transcript == "Keep your chest up."
    assert record.values_sha256 == VALUES_SHA
    assert record.model_id == "fake-understanding"
    assert record.understanding_attempts == 1
    assert record.completed_at == NOW


async def test_failed_reel_keeps_a_null_verdict_and_its_error_code(
    storage: Storage,
) -> None:
    seeded = await seed(storage, reels=1)
    pending = seeded.reels[0]

    async with storage.transaction() as tx:
        await tx.reels.complete(make_failed(pending))
        record = await tx.reels.get(OWNER, pending.reel_id)

    assert record is not None
    assert record.evaluation_status is EvaluationStatus.FAILED_PROVIDER
    assert record.verdict is None and record.confidence is None
    assert record.error_code == "provider_timeout"


async def test_completing_a_missing_reel_does_nothing(storage: Storage) -> None:
    seeded = await seed(storage)
    ghost = make_pending(seeded.session)

    async with storage.transaction() as tx:
        assert await tx.reels.complete(make_failed(ghost)) is False


async def test_timings_are_stored_with_the_reel(storage: Storage) -> None:
    seeded = await seed(storage, reels=1)
    pending = seeded.reels[0]
    timings = StageTimings(inference_ms=2300, persist_ms=12, total_ms=7400)

    async with storage.transaction() as tx:
        await tx.reels.complete(make_judged(pending))
        await tx.reels.set_timings(pending.reel_id, timings, True)
        record = await tx.reels.get(OWNER, pending.reel_id)

    assert record is not None
    assert record.timings == timings
    assert record.latency_overrun is True


async def test_reels_are_owner_scoped(storage: Storage) -> None:
    seeded = await seed(storage, reels=1)

    async with storage.transaction() as tx:
        assert await tx.reels.get(OTHER_OWNER, seeded.reels[0].reel_id) is None
        assert await tx.reels.delete(OTHER_OWNER, seeded.reels[0].reel_id) is False
        assert await tx.reels.get(OWNER, seeded.reels[0].reel_id) is not None


async def test_accounting_view_is_looked_up_by_capture_order(storage: Storage) -> None:
    seeded = await seed(storage, reels=2)
    session_id = seeded.session.session_id

    async with storage.transaction() as tx:
        await tx.reels.complete(make_judged(seeded.reels[1], Verdict.CONFLICT))
        first = await tx.reels.next_accounting_view(session_id, 0)
        second = await tx.reels.next_accounting_view(session_id, 1)
        third = await tx.reels.next_accounting_view(session_id, 2)
        await tx.reels.delete(OWNER, seeded.reels[0].reel_id)
        after_gap = await tx.reels.next_accounting_view(session_id, 0)

    assert first is not None and first.evaluation_status is EvaluationStatus.PENDING
    assert second is not None and second.verdict is Verdict.CONFLICT
    assert second.reel_id == seeded.reels[1].reel_id
    assert third is None
    # A deleted reel leaves a gap in the numbering; the next reel is found.
    assert after_gap is not None and after_gap.order_seq == 2


async def test_batch_membership_lists_conflicts_in_position_order(
    storage: Storage,
) -> None:
    seeded = await seed(storage, reels=3)
    batch = make_batch(seeded.session)
    verdicts = [Verdict.CONFLICT, Verdict.ALIGNED, Verdict.CONFLICT]

    async with storage.transaction() as tx:
        await tx.notifications.insert_batch(batch)
        for position, (reel, verdict) in enumerate(
            zip(seeded.reels, verdicts, strict=True), start=1
        ):
            await tx.reels.complete(make_judged(reel, verdict))
            await tx.reels.assign_batch(reel.reel_id, batch.batch_id, position)
        conflicts = await tx.reels.list_batch_conflict_ids(batch.batch_id)

    assert conflicts == [seeded.reels[0].reel_id, seeded.reels[2].reel_id]


async def test_timeline_is_newest_first_paged_and_owner_scoped(
    storage: Storage,
) -> None:
    seeded = await seed(storage, reels=5)
    other = await seed(storage, reels=1, owner_id=OTHER_OWNER)
    newest_first = [r.reel_id for r in reversed(seeded.reels)]

    async with storage.transaction() as tx:
        page_one = await tx.reels.list_timeline(
            OWNER, session_id=None, limit=2, before=None
        )
        last = page_one[-1]
        cursor = TimelineCursor(
            start_ms=int(last.start.timestamp() * 1000), reel_id=last.id
        )
        page_two = await tx.reels.list_timeline(
            OWNER, session_id=None, limit=10, before=cursor
        )
        by_session = await tx.reels.list_timeline(
            OWNER, session_id=other.session.session_id, limit=10, before=None
        )

    assert [r.id for r in page_one] == newest_first[:2]
    assert [r.id for r in page_two] == newest_first[2:]
    assert by_session == []


async def test_pending_listing_and_counts(storage: Storage) -> None:
    seeded = await seed(storage, reels=3)
    session_id = seeded.session.session_id
    old = make_pending(seeded.session, order_seq=4, created_at=at(-100))

    async with storage.transaction() as tx:
        await tx.reels.insert_pending(old)
        await tx.reels.complete(make_judged(seeded.reels[0]))
        overdue = await tx.reels.list_pending(created_before=at(-35))
        everything = await tx.reels.list_pending()
        pending_count = await tx.reels.count_pending(session_id)
        total = await tx.reels.count_for_session(session_id)

    assert [ref.reel_id for ref in overdue] == [old.reel_id]
    assert len(everything) == 3
    assert (pending_count, total) == (3, 4)


async def test_deleting_a_reel_removes_its_thumbnail_vector_and_feedback(
    storage: Storage,
) -> None:
    seeded = await seed(storage, reels=1)
    reel = seeded.reels[0]
    async with storage.transaction() as tx:
        await tx.reels.complete(make_judged(reel))
        await tx.thumbnails.put(make_thumbnail(reel))
        await tx.embeddings.create_pending(reel.reel_id, OWNER, PROFILE, now=NOW)
        await tx.feedback.insert(make_feedback(reel))

    async with storage.transaction() as tx:
        assert await tx.reels.delete(OWNER, reel.reel_id) is True

    async with storage.transaction() as tx:
        assert await tx.reels.get(OWNER, reel.reel_id) is None
        assert await tx.thumbnails.get(OWNER, reel.reel_id) is None
        assert await tx.embeddings.get(reel.reel_id, PROFILE.profile_id) is None
        assert await tx.feedback.list_for_owner(OWNER, 10) == []


# --- thumbnails -------------------------------------------------------------


async def test_one_thumbnail_per_reel_and_owner_scoped(storage: Storage) -> None:
    seeded = await seed(storage, reels=1)
    reel = seeded.reels[0]

    async with storage.transaction() as tx:
        await tx.reels.complete(make_judged(reel))
        await tx.thumbnails.put(make_thumbnail(reel, b"\xff\xd8\xfffirst"))
        await tx.thumbnails.put(make_thumbnail(reel, b"\xff\xd8\xffsecond"))
        thumb = await tx.thumbnails.get(OWNER, reel.reel_id)
        foreign = await tx.thumbnails.get(OTHER_OWNER, reel.reel_id)
        record = await tx.reels.get(OWNER, reel.reel_id)

    assert thumb is not None and thumb.jpeg == b"\xff\xd8\xffsecond"
    assert foreign is None
    assert record is not None
    assert record.thumbnail_url == f"/thumbs/{reel.reel_id}.jpg"


# --- embeddings (metadata) --------------------------------------------------


async def test_embedding_work_is_derived_from_stored_text_only(
    storage: Storage,
) -> None:
    seeded = await seed(storage, reels=2)
    judged, unjudged = seeded.reels

    async with storage.transaction() as tx:
        await tx.reels.complete(make_judged(judged))
        await tx.embeddings.create_pending(judged.reel_id, OWNER, PROFILE, now=NOW)
        await tx.embeddings.create_pending(judged.reel_id, OWNER, PROFILE, now=NOW)
        not_yet = await tx.embeddings.list_due(at(-1), 10)
        due = await tx.embeddings.list_due(at(1), 10)
        record = await tx.reels.get(OWNER, judged.reel_id)
        other = await tx.reels.get(OWNER, unjudged.reel_id)

    assert not_yet == []
    assert len(due) == 1
    assert due[0].reel_id == judged.reel_id
    assert due[0].text == "A trainer demonstrates squat form.\nKeep your chest up."
    assert due[0].attempts == 0
    assert record is not None and record.embedding is not None
    assert record.embedding.status is EmbeddingStatus.PENDING
    assert record.embedding.profile_id == PROFILE.profile_id
    assert other is not None and other.embedding is None


async def test_embedding_retry_and_failure_are_recorded(storage: Storage) -> None:
    seeded = await seed(storage, reels=1)
    reel = seeded.reels[0]
    profile_id = PROFILE.profile_id

    async with storage.transaction() as tx:
        await tx.reels.complete(make_judged(reel))
        await tx.embeddings.create_pending(reel.reel_id, OWNER, PROFILE, now=NOW)
        await tx.embeddings.mark_retry(
            reel.reel_id,
            profile_id,
            attempts=1,
            next_attempt_at=at(30),
            error_code="provider_timeout",
        )
        too_early = await tx.embeddings.list_due(at(10), 10)
        retry = await tx.embeddings.list_due(at(31), 10)
        await tx.embeddings.mark_failed(
            reel.reel_id, profile_id, attempts=5, error_code="provider_quota"
        )
        after_failure = await tx.embeddings.list_due(at(1000), 10)
        record = await tx.embeddings.get(reel.reel_id, profile_id)

    assert too_early == []
    assert [task.attempts for task in retry] == [1]
    assert after_failure == []
    assert record is not None
    assert record.status is EmbeddingStatus.FAILED
    assert (record.attempts, record.error_code) == (5, "provider_quota")


# --- embeddings (vectors, require pgvector on PostgreSQL) -------------------


def _vector(*leading: float) -> list[float]:
    return [*leading, *([0.0] * (PROFILE.dimension - len(leading)))]


async def test_vector_upsert_is_idempotent_and_searchable(
    vector_storage: Storage,
) -> None:
    seeded = await seed(vector_storage, reels=2)
    near, far = seeded.reels

    async with vector_storage.transaction() as tx:
        for reel in seeded.reels:
            await tx.reels.complete(make_judged(reel))
            await tx.embeddings.create_pending(reel.reel_id, OWNER, PROFILE, now=NOW)
        profile_id = PROFILE.profile_id
        await tx.embeddings.mark_indexed(
            near.reel_id, profile_id, _vector(1.0), indexed_at=at(1)
        )
        await tx.embeddings.mark_indexed(
            near.reel_id, profile_id, _vector(1.0), indexed_at=at(2)
        )
        await tx.embeddings.mark_indexed(
            far.reel_id, profile_id, _vector(0.0, 1.0), indexed_at=at(1)
        )
        hits = await tx.embeddings.nearest(OWNER, profile_id, _vector(0.9, 0.1), 5)
        foreign = await tx.embeddings.nearest(OTHER_OWNER, profile_id, _vector(1.0), 5)
        other_space = await tx.embeddings.nearest(
            OWNER, "other-model:768:X:v9", _vector(1.0), 5
        )
        record = await tx.embeddings.get(near.reel_id, profile_id)
        due = await tx.embeddings.list_due(at(1000), 10)

    assert [hit.reel_id for hit in hits] == [near.reel_id, far.reel_id]
    assert hits[0].distance < hits[1].distance
    assert foreign == [] and other_space == []
    assert record is not None and record.status is EmbeddingStatus.INDEXED
    assert due == []


async def test_vector_with_the_wrong_dimension_is_rejected(
    vector_storage: Storage,
) -> None:
    seeded = await seed(vector_storage, reels=1)
    reel = seeded.reels[0]
    async with vector_storage.transaction() as tx:
        await tx.reels.complete(make_judged(reel))
        await tx.embeddings.create_pending(reel.reel_id, OWNER, PROFILE, now=NOW)

    with pytest.raises(Exception, match="dimension"):
        async with vector_storage.transaction() as tx:
            await tx.embeddings.mark_indexed(
                reel.reel_id, PROFILE.profile_id, [0.1] * 384, indexed_at=NOW
            )

    async with vector_storage.transaction() as tx:
        record = await tx.embeddings.get(reel.reel_id, PROFILE.profile_id)
    assert record is not None and record.status is EmbeddingStatus.PENDING


# --- feedback ---------------------------------------------------------------


async def test_feedback_insert_is_idempotent_per_client_id(storage: Storage) -> None:
    seeded = await seed(storage, reels=1)
    reel = seeded.reels[0]
    client_id = uuid4()
    first = make_feedback(reel, client_feedback_id=client_id)
    retry = make_feedback(reel, client_feedback_id=client_id, comment="changed")

    async with storage.transaction() as tx:
        stored = await tx.feedback.insert(first)
        repeated = await tx.feedback.insert(retry)
        listed = await tx.feedback.list_for_owner(OWNER, 10)

    assert stored == first
    assert repeated == first
    assert listed == [first]


async def test_feedback_examples_are_recent_per_reel_and_revision_bound(
    storage: Storage,
) -> None:
    seeded = await seed(storage, reels=4)
    current_a, current_b, stale, unjudged = seeded.reels
    async with storage.transaction() as tx:
        await tx.reels.complete(make_judged(current_a, Verdict.CONFLICT))
        await tx.reels.complete(make_judged(current_b, Verdict.ALIGNED))
        await tx.reels.complete(make_judged(stale, values_sha256="c" * 64))
        await tx.feedback.insert(
            make_feedback(current_a, comment="older", created_at=at(1))
        )
        await tx.feedback.insert(
            make_feedback(current_a, comment="newer", created_at=at(5))
        )
        await tx.feedback.insert(make_feedback(current_b, created_at=at(3)))
        await tx.feedback.insert(make_feedback(stale, created_at=at(9)))
        await tx.feedback.insert(make_feedback(unjudged, created_at=at(10)))

    async with storage.transaction() as tx:
        rows = await tx.feedback.list_example_rows(OWNER, VALUES_SHA, 10)
        limited = await tx.feedback.list_example_rows(OWNER, VALUES_SHA, 1)
        foreign = await tx.feedback.list_example_rows(OTHER_OWNER, VALUES_SHA, 10)

    assert [(r.reel_id, r.comment) for r in rows] == [
        (current_a.reel_id, "newer"),
        (current_b.reel_id, "Synthetic comment."),
    ]
    assert rows[0].prior_verdict is Verdict.CONFLICT
    assert rows[0].prior_value_ids == ("V3",)
    assert rows[0].reel_description == "A trainer demonstrates squat form."
    assert rows[0].values_sha256 == VALUES_SHA
    assert len(limited) == 1 and foreign == []


# --- policy -----------------------------------------------------------------


async def test_policy_versions_increase_and_latest_wins(storage: Storage) -> None:
    async with storage.transaction() as tx:
        nothing = await tx.policy.get_current(OWNER)
        first = await tx.policy.insert_next(
            OWNER,
            batch_size=15,
            minimum_conflicts=3,
            cooldown_seconds=600,
            updated_at=NOW,
        )
        second = await tx.policy.insert_next(
            OWNER,
            batch_size=10,
            minimum_conflicts=2,
            cooldown_seconds=300,
            updated_at=at(5),
        )
        current = await tx.policy.get_current(OWNER)
        foreign = await tx.policy.get_current(OTHER_OWNER)

    assert nothing is None and foreign is None
    assert (first.version, second.version) == (1, 2)
    assert current == second


# --- notifications ----------------------------------------------------------


async def test_batch_lifecycle(storage: Storage) -> None:
    seeded = await seed(storage)
    session_id = seeded.session.session_id
    batch = make_batch(seeded.session)

    async with storage.transaction() as tx:
        assert await tx.notifications.get_open_batch(session_id) is None
        await tx.notifications.insert_batch(batch)
        opened = await tx.notifications.get_open_batch(session_id)
        closed = batch.model_copy(
            update={
                "status": BatchStatus.EVALUATED,
                "judged_count": 15,
                "conflict_count": 3,
                "excluded_count": 1,
                "outcome": BatchOutcome.TRIGGERED,
                "evaluated_at": at(9),
            }
        )
        await tx.notifications.save_batch(closed)
        after = await tx.notifications.get_open_batch(session_id)
        count = await tx.notifications.count_batches(session_id)
        listed = await tx.notifications.list_batches(OWNER, session_id)

    assert opened == batch
    assert after is None
    assert count == 1
    assert listed == [closed]


async def test_user_state_is_created_on_first_lock_and_updated(
    storage: Storage,
) -> None:
    async with storage.transaction() as tx:
        fresh = await tx.notifications.lock_user_state(OWNER)
        await tx.notifications.set_last_dispatch(OWNER, at(7))
        updated = await tx.notifications.lock_user_state(OWNER)

    assert fresh.last_dispatch_at is None
    assert updated.last_dispatch_at == at(7)


async def test_outbox_items_are_saved_listed_oldest_first_and_owner_scoped(
    storage: Storage,
) -> None:
    seeded = await seed(storage)
    older = make_outbox(make_batch(seeded.session, batch_index=1), created_at=at(1))
    newer = make_outbox(make_batch(seeded.session, batch_index=2), created_at=at(2))

    async with storage.transaction() as tx:
        for index, item in enumerate((newer, older), start=1):
            batch = make_batch(
                seeded.session,
                batch_id=item.batch_id,
                batch_index=index,
                status=BatchStatus.EVALUATED,
                outcome=BatchOutcome.TRIGGERED,
                evaluated_at=NOW,
            )
            await tx.notifications.insert_batch(batch)
            await tx.notifications.insert_outbox(item)
        undelivered = await tx.notifications.list_undelivered(OWNER)
        leased = older.model_copy(
            update={
                "status": OutboxStatus.LEASED,
                "lease_until": at(30),
                "dispatch_attempts": 1,
                "dispatched_at": at(3),
            }
        )
        await tx.notifications.save_outbox(leased)
        acked = newer.model_copy(
            update={"status": OutboxStatus.ACKNOWLEDGED, "acknowledged_at": at(4)}
        )
        await tx.notifications.save_outbox(acked)
        still = await tx.notifications.list_undelivered(OWNER)
        loaded = await tx.notifications.get_outbox(OWNER, older.notification_id)
        foreign = await tx.notifications.get_outbox(OTHER_OWNER, older.notification_id)
        everything = await tx.notifications.list_outbox(OWNER)

    assert [i.notification_id for i in undelivered] == [
        older.notification_id,
        newer.notification_id,
    ]
    assert still == [leased]
    assert loaded == leased and foreign is None
    assert {i.status for i in everything} == {
        OutboxStatus.LEASED,
        OutboxStatus.ACKNOWLEDGED,
    }


async def test_owner_deletion_clears_policy_and_notification_state(
    storage: Storage,
) -> None:
    async with storage.transaction() as tx:
        await tx.policy.insert_next(
            OWNER,
            batch_size=15,
            minimum_conflicts=3,
            cooldown_seconds=600,
            updated_at=NOW,
        )
        await tx.notifications.lock_user_state(OWNER)
        await tx.notifications.set_last_dispatch(OWNER, at(1))

    async with storage.transaction() as tx:
        await tx.policy.delete_all(OWNER)
        await tx.notifications.delete_all(OWNER)
        policy = await tx.policy.get_current(OWNER)
        state = await tx.notifications.lock_user_state(OWNER)

    assert policy is None
    assert state.last_dispatch_at is None


async def test_health_reports_the_backend(storage: Storage) -> None:
    health = await storage.health()

    assert health.ok is True
    assert health.backend in {"memory", "postgres"}
