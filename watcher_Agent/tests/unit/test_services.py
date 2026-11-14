"""Embedding indexing, feedback, timeline and deletion services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from judge.contracts.feedback import FeedbackLabel, FeedbackRequest, RetrievalMode
from judge.contracts.notification import OutboxStatus
from judge.contracts.record import EmbeddingStatus, EvaluationStatus
from judge.contracts.storage import EmbeddingProfile
from judge.contracts.understanding import Verdict
from judge.errors import NotFoundError, ProviderError, ProviderErrorKind
from judge.understanding.prompt import TextPart
from tests.support.builders import SESSION_ID
from tests.support.harness import Harness
from tests.support.records import make_judged, make_pending, make_session

pytestmark = pytest.mark.unit


@pytest.fixture
async def h() -> AsyncIterator[Harness]:
    harness = await Harness.create()
    yield harness
    await harness.close()


def _transient() -> ProviderError:
    return ProviderError(ProviderErrorKind.TRANSIENT, "synthetic")


def _prompt_text(h: Harness, call: int) -> str:
    parts = h.understanding.calls[call].parts
    return "\n".join(p.text for p in parts if isinstance(p, TextPart))


async def _feedback(
    h: Harness, reel_index: int, comment: str | None, label: str = "disagree"
) -> None:
    reel = (await h.reels())[reel_index]
    h.clock.advance(1)
    await h.runtime.feedback.submit(
        h.owner,
        FeedbackRequest(reel_id=reel.id, label=FeedbackLabel(label), comment=comment),
    )


# --- embedding indexing -----------------------------------------------------


async def test_embedding_failure_never_erases_the_verdict_and_is_retried(
    h: Harness,
) -> None:
    h.embedding.failures = [_transient()]
    await h.play_feed(["conflict"])

    record = (await h.reels())[0]
    assert record.verdict is Verdict.CONFLICT
    assert record.embedding is not None
    assert record.embedding.status is EmbeddingStatus.PENDING
    assert record.embedding.attempts == 1

    h.clock.advance(3)
    await h.runtime.maintenance.tick()

    record = (await h.reels())[0]
    assert record.embedding is not None
    assert record.embedding.status is EmbeddingStatus.INDEXED
    # Retried from stored text: no second understanding call, no media.
    assert len(h.understanding.calls) == 1
    assert h.embedding.calls[0] == h.embedding.calls[1]


async def test_embedding_gives_up_after_bounded_attempts() -> None:
    h = await Harness.create(embedding_max_attempts=2)
    try:
        h.embedding.failures = [_transient(), _transient(), _transient()]
        await h.play_feed(["aligned"])
        h.clock.advance(60)
        await h.runtime.maintenance.tick()

        record = (await h.reels())[0]
        assert record.verdict is Verdict.ALIGNED
        assert record.embedding is not None
        assert record.embedding.status is EmbeddingStatus.FAILED
        assert record.embedding.attempts == 2
        assert len(h.embedding.calls) == 2
    finally:
        await h.close()


async def test_embedding_with_the_wrong_dimension_is_rejected(h: Harness) -> None:
    h.embedding.dimension_override = 384
    await h.play_feed(["aligned"])

    record = (await h.reels())[0]
    assert record.embedding is not None
    assert record.embedding.status is EmbeddingStatus.FAILED
    async with h.runtime.storage.transaction() as tx:
        stored = await tx.embeddings.get(record.id, record.embedding.profile_id)
        hits = await tx.embeddings.nearest(
            h.owner, record.embedding.profile_id, [0.1] * 768, 5
        )
    assert stored is not None
    assert stored.error_code == "embedding_dimension_mismatch"
    assert hits == []


async def test_embeddings_from_another_profile_are_never_mixed(h: Harness) -> None:
    session = make_session(owner_id=h.owner)
    reel = make_pending(session)
    other = EmbeddingProfile(
        profile_id="old-model:384:X:v0",
        model_id="old-model",
        dimension=384,
        task_type="X",
        version="v0",
    )
    async with h.runtime.storage.transaction() as tx:
        await tx.sessions.insert(session)
        await tx.reels.insert_pending(reel)
        await tx.reels.complete(make_judged(reel))
        await tx.embeddings.create_pending(
            reel.reel_id, h.owner, other, now=h.clock.now()
        )

    await h.runtime.maintenance.tick()

    async with h.runtime.storage.transaction() as tx:
        stored = await tx.embeddings.get(reel.reel_id, other.profile_id)
    assert stored is not None and stored.status is EmbeddingStatus.FAILED
    assert stored.error_code == "embedding_profile_mismatch"
    assert h.embedding.calls == []


async def test_indexing_can_be_disabled_without_affecting_judgments() -> None:
    h = await Harness.create(embedding_indexing_enabled=False)
    try:
        await h.play_feed(["aligned"])

        record = (await h.reels())[0]
        assert record.evaluation_status is EvaluationStatus.JUDGED
        assert record.embedding is None
        assert h.embedding.calls == []
    finally:
        await h.close()


# --- feedback ---------------------------------------------------------------


async def test_feedback_is_stored_idempotently_and_owner_checked(h: Harness) -> None:
    await h.play_feed(["conflict"])
    reel = (await h.reels())[0]
    request = FeedbackRequest(
        reel_id=reel.id,
        label=FeedbackLabel.DISAGREE,
        score=0.25,
        comment="This was fine for me.",
        source="timeline",
        client_feedback_id=uuid4(),
    )

    first = await h.runtime.feedback.submit(h.owner, request)
    second = await h.runtime.feedback.submit(h.owner, request)

    assert first == second
    assert (first.score, first.source) == (0.25, "timeline")
    with pytest.raises(NotFoundError):
        await h.runtime.feedback.submit("someone-else-synthetic", request)
    with pytest.raises(NotFoundError):
        await h.runtime.feedback.submit(
            h.owner, FeedbackRequest(reel_id=uuid4(), label=FeedbackLabel.AGREE)
        )


async def test_storing_feedback_calls_no_model_and_changes_no_values(
    h: Harness,
) -> None:
    await h.play_feed(["conflict"])
    calls_before = len(h.understanding.calls)
    values_before = h.values.result

    await _feedback(h, 0, "Please re-rate this one.", label="rerate")

    assert len(h.understanding.calls) == calls_before
    assert h.values.result == values_before
    assert (await h.reels())[0].verdict is Verdict.CONFLICT


async def test_recent_explicit_feedback_becomes_prompt_examples(h: Harness) -> None:
    offset = await h.play_feed(["conflict", "conflict"])
    await _feedback(h, 0, "Form tutorials are what I want.")
    await _feedback(h, 1, None, label="agree")

    await h.play_feed(["neutral"], start_ms=offset + 2000, first_scene=40)

    newest = (await h.reels())[3]
    text = _prompt_text(h, -1)
    assert "Form tutorials are what I want." in text
    assert text.count("label: ") == 2
    assert len(newest.feedback_example_ids) == 2
    assert newest.retrieval_mode is RetrievalMode.RECENT_EXPLICIT_FEEDBACK


async def test_at_most_three_examples_are_ever_supplied(h: Harness) -> None:
    offset = await h.play_feed(["conflict"] * 5)
    for index in range(5):
        await _feedback(h, index, f"synthetic comment {index}")

    await h.play_feed(["neutral"], start_ms=offset + 2000, first_scene=40)

    text = _prompt_text(h, -1)
    assert text.count("label: ") == 3
    assert "synthetic comment 4" in text and "synthetic comment 0" not in text


async def test_examples_are_dropped_whole_not_truncated_and_rerate_needs_a_comment(
    h: Harness,
) -> None:
    offset = await h.play_feed(["conflict"] * 3)
    await _feedback(h, 0, "x" * 601)
    await _feedback(h, 1, None, label="rerate")
    await _feedback(h, 2, "short and usable")

    await h.play_feed(["neutral"], start_ms=offset + 2000, first_scene=40)

    text = _prompt_text(h, -1)
    assert text.count("label: ") == 1
    assert "short and usable" in text and "xxxx" not in text


async def test_examples_from_an_older_values_revision_are_not_used(
    h: Harness,
) -> None:
    offset = await h.play_feed(["conflict"])
    await _feedback(h, 0, "about the old values")
    h.values.result = h.values.result.model_copy(
        update={"content_sha256": "f" * 64, "revision": "8"}
    )
    h.runtime.pipeline.values_gate.invalidate(h.owner)

    await h.play_feed(["neutral"], start_ms=offset + 2000, first_scene=40)

    assert "about the old values" not in _prompt_text(h, -1)
    assert (await h.reels())[2].retrieval_mode is RetrievalMode.NONE


# --- timeline and thumbnails ------------------------------------------------


async def test_timeline_is_paged_newest_first_with_an_opaque_cursor(
    h: Harness,
) -> None:
    await h.play_feed(["neutral"] * 4)

    first, cursor = await h.runtime.timeline.list_reels(
        h.owner, session_id=None, limit=2, cursor=None
    )
    rest, end = await h.runtime.timeline.list_reels(
        h.owner, session_id=SESSION_ID, limit=10, cursor=cursor
    )
    foreign, _ = await h.runtime.timeline.list_reels(
        "someone-else-synthetic", session_id=None, limit=10, cursor=None
    )

    assert cursor is not None and end is None
    assert len(first) == 2 and len(rest) == 2
    starts = [r.start for r in [*first, *rest]]
    assert starts == sorted(starts, reverse=True)
    assert foreign == []


async def test_malformed_cursor_is_a_validation_error(h: Harness) -> None:
    from judge.errors import ValidationFailure

    with pytest.raises(ValidationFailure):
        await h.runtime.timeline.list_reels(
            h.owner, session_id=None, limit=10, cursor="not-a-cursor"
        )


async def test_thumbnail_access_is_owner_scoped(h: Harness) -> None:
    await h.play_feed(["neutral"])
    reel = (await h.reels())[0]

    assert (await h.runtime.timeline.thumbnail(h.owner, reel.id)).reel_id == reel.id
    with pytest.raises(NotFoundError):
        await h.runtime.timeline.thumbnail("someone-else-synthetic", reel.id)
    with pytest.raises(NotFoundError):
        await h.runtime.timeline.thumbnail(h.owner, uuid4())


# --- deletion ---------------------------------------------------------------


async def test_deleting_a_reel_removes_dependents_and_cancels_its_warning(
    h: Harness,
) -> None:
    verdicts = ["conflict", "neutral", "neutral", "neutral", "neutral"] * 3
    await h.play_feed(verdicts)
    conflict = (await h.reels())[0]
    await _feedback(h, 0, "to be deleted with the reel")

    await h.runtime.deletion.delete_reel(h.owner, conflict.id)

    async with h.runtime.storage.transaction() as tx:
        outbox = await tx.notifications.list_outbox(h.owner)
        feedback = await tx.feedback.list_for_owner(h.owner, 10)
        thumbnail = await tx.thumbnails.get(h.owner, conflict.id)
    assert conflict.id not in {r.id for r in await h.reels()}
    assert [i.status for i in outbox] == [OutboxStatus.CANCELLED]
    assert feedback == [] and thumbnail is None
    with pytest.raises(NotFoundError):
        await h.runtime.deletion.delete_reel(h.owner, conflict.id)
    with pytest.raises(NotFoundError):
        await h.runtime.deletion.delete_reel("someone-else-synthetic", uuid4())


async def test_deleting_all_owner_data_clears_storage_and_volatile_media(
    h: Harness,
) -> None:
    await h.play_feed(["conflict"] * 3, audio=True)
    await _feedback(h, 0, "synthetic")
    assert h.runtime.budget.used > 0

    await h.runtime.deletion.delete_owner_data(h.owner)

    async with h.runtime.storage.transaction() as tx:
        assert await tx.sessions.get(SESSION_ID) is None
        assert await tx.feedback.list_for_owner(h.owner, 10) == []
        assert await tx.notifications.list_outbox(h.owner) == []
    assert await h.reels() == []
    assert h.runtime.budget.used == 0
    assert len(h.runtime.volatile) == 0


async def test_deleting_a_session_removes_only_that_session(h: Harness) -> None:
    await h.play_feed(["neutral"] * 2)

    await h.runtime.deletion.delete_session(h.owner, SESSION_ID)

    assert await h.reels() == []
    with pytest.raises(NotFoundError):
        await h.runtime.deletion.delete_session(h.owner, SESSION_ID)
