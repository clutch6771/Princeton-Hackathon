"""Tests for reflect_pending: review records, staging, restore and safe retry.

A scripted model plays the Reflector, so each run is deterministic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from guardian import api, reflect_flow, reflector
from guardian.datasource import SEED_DIR, FakeDataSource
from guardian.memory import inbox, locks, proposals, reflector_files, reviews, values
from guardian.tests.conftest import NOW, OTHER_USER, USER
from guardian.tests.fakes import ScriptedChatModel, reply, tool_call

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
SESSION = "s_001"

HUSTLE_CHANGE = {
    "value_id": "new",
    "new_text": """## New: Less get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, get-rich-quick pitches
- Unless: none
- Suggested wording: "I don't want hustle and get-rich-quick videos"
- Evidence: fb_002, fb_004, fb_007""",
    "evidence": "fb_002, fb_004, fb_007",
}
CARD_PROMPT = "You marked 3 reels like this as not for you. I've suggested wording; edit it to make it yours."
QUESTION = "What makes a rehab story feel different from a transformation video to you?"
BULLETS = [
    "You marked 3 hustle and get-rich-quick reels as not for you. [fb_002, fb_004, fb_007]",
    "You disagreed with 5 flags on injury rehab reels. [fb_001, fb_003, fb_005]",
]
HYPOTHESES = """## H1: Hustle content is unwanted
- Evidence: s_001 (3 not_for_me marks: fb_002, fb_004, fb_007)
- Sessions seen: 7 · Last seen: 2026-11-13
- Status: watching"""
BRIEF = "Student who follows rehab and training content. Dislikes hustle pitches."


def summary(
    bullets: list[str] | None = None,
    question: str | None = QUESTION,
    session: str = SESSION,
) -> str:
    listed = "\n".join(
        f"- {bullet}" for bullet in (BULLETS if bullets is None else bullets)
    )
    closing = f"\n\n## A question for you\n{question}" if question else ""
    return f"# Session {session}\nMostly rehab and training, plus three hustle reels.\n\n## What I learned\n{listed}{closing}"


def write(path: str, content: str, call: str) -> AIMessage:
    return tool_call("write_file", {"file_path": path, "content": content}, call)


def good_run(
    session: str = SESSION,
    change: dict[str, Any] | None = HUSTLE_CHANGE,
    text: str | None = None,
) -> list[AIMessage]:
    """A complete, valid review of one session."""
    queue_args = {"kind": "value_change", "prompt": CARD_PROMPT, "value_change": change}
    steps = [
        tool_call(
            "write_todos",
            {"todos": [{"content": "Review", "status": "in_progress"}]},
            f"{session}-1",
        ),
        tool_call("dump_session", {"session_id": session}, f"{session}-2"),
        tool_call("dump_feedback", {"since": EPOCH.isoformat()}, f"{session}-3"),
        write("/memories/hypotheses.md", HYPOTHESES, f"{session}-4"),
        tool_call("queue_question", queue_args, f"{session}-5") if change else None,
        write("/memories/brief.md", BRIEF, f"{session}-6"),
        write(
            f"/memories/sessions/{session}.md",
            text or summary(session=session),
            f"{session}-7",
        ),
        reply("Review complete."),
    ]
    return [step for step in steps if step is not None]


def one_session_source(clock, **options: Any) -> FakeDataSource:
    reels = [
        r
        for r in json.loads((SEED_DIR / "reels.json").read_text())
        if r["session_id"] == SESSION
    ]
    feedback = json.loads((SEED_DIR / "feedback.json").read_text())
    return FakeDataSource.from_records(
        USER, reels=reels, feedback=feedback, clock=clock, **options
    )


@pytest.fixture
def source(clock) -> FakeDataSource:
    return one_session_source(clock)


@pytest.fixture
def reflect(store, source, clock):
    """Configure the package with a scripted Reflector; returns a function that runs it."""

    def run(
        script: list[AIMessage], datasource: FakeDataSource | None = None, when=clock
    ) -> tuple[int, ScriptedChatModel]:
        model = ScriptedChatModel(script=script)
        api.configure(
            store,
            InMemorySaver(),
            datasource or source,
            clock=when,
            reflector_model=model,
        )
        return api.reflect_pending(USER), model

    return run


def cards(
    store, kind: str | None = None, status: str = inbox.STATUS_PENDING
) -> list[inbox.Card]:
    return [
        c
        for c in inbox.list_cards(store, USER, status=status)
        if kind in (None, c.kind)
    ]


# --- A successful review ------------------------------------------------------------


def test_review_produces_a_learned_card_and_a_value_change_card(reflect, store):
    reviewed, _ = reflect(good_run())

    assert reviewed == 1
    (learned,) = cards(store, "learned")
    assert learned.id == "learned_s_001" and learned.session_id == SESSION
    assert learned.prompt == f"- {BULLETS[0]}\n- {BULLETS[1]}\n\n{QUESTION}"
    (change,) = cards(store, "value_change")
    assert change.prompt == CARD_PROMPT
    proposal = proposals.get_proposal(store, USER, change.proposal_id)
    assert (
        proposal.status == "pending" and "Suggested wording" in proposal.proposed_text
    )
    assert proposal.source["session_id"] == SESSION
    assert cards(store, status=inbox.STATUS_STAGED) == []


def test_review_writes_the_reflectors_files_and_not_the_values(
    reflect, store, with_values
):
    before = values.render_values(store, USER)

    reflect(good_run())

    assert reflector_files.read_file(store, USER, reflector_files.BRIEF) == BRIEF
    assert reflector_files.read_file(store, USER, "sessions/s_001.md") == summary()
    assert values.render_values(store, USER) == before
    assert reviews.get_review(store, USER, SESSION).status == reviews.STATUS_DONE


def test_sessions_seen_is_recomputed_from_distinct_session_ids(reflect, store):
    reflect(good_run())

    stored = reflector_files.read_file(store, USER, reflector_files.HYPOTHESES)
    assert "- Sessions seen: 1 · Last seen: 2026-11-13" in stored
    assert "Sessions seen: 7" not in stored


def test_run_input_names_the_session_and_gives_values_as_read_only_text(
    reflect, with_values
):
    _, model = reflect(good_run())

    first = [str(m.content) for m in model.calls[0] if isinstance(m, HumanMessage)][0]
    assert "Review session s_001." in first
    assert "since: 2000-01-01" in first
    assert "## V1: Fitness without body comparison" in first and "read-only" in first


# --- Repeat calls ---------------------------------------------------------------------


def test_a_second_call_reviews_nothing_and_does_not_call_the_model(reflect, store):
    reflect(good_run())

    reviewed, model = reflect(good_run())

    assert reviewed == 0 and model.calls == []
    assert len(cards(store, "learned")) == 1 and len(cards(store, "value_change")) == 1


def test_each_finished_session_is_reviewed_once_and_the_cursor_moves(
    reflect, store, datasource
):
    second = summary(
        bullets=["Two reels conflicted with your values. [rl_014, rl_016]"],
        session="s_002",
    )
    script = good_run("s_001") + good_run("s_002", change=None, text=second)

    reviewed, model = reflect(script, datasource=datasource)

    openers = [
        str(m[-1].content) for m in model.calls if isinstance(m[-1], HumanMessage)
    ]
    assert reviewed == 2
    assert "Review session s_001." in openers[0] and "since: 2000-01-01" in openers[0]
    assert (
        "Review session s_002." in openers[1]
        and f"since: {NOW.isoformat()}" in openers[1]
    )
    assert {c.id for c in cards(store, "learned")} == {"learned_s_001", "learned_s_002"}


def test_a_learned_card_that_was_answered_is_not_added_again(reflect, store):
    reflect(good_run())
    (learned,) = cards(store, "learned")
    inbox.mark_answered(store, learned, NOW)
    reviews.reset(store, USER, SESSION)

    reflect(good_run(change=None))

    assert cards(store, "learned") == []
    assert inbox.get_card(store, USER, "learned_s_001").status == inbox.STATUS_ANSWERED


def test_a_rejected_change_is_not_queued_again_but_the_review_still_completes(
    reflect, store
):
    reflect(good_run())
    (change,) = cards(store, "value_change")
    proposals.decide(
        store, USER, change.proposal_id, action="reject", edited_text=None, now=NOW
    )
    inbox.mark_answered(store, change, NOW)
    reviews.reset(store, USER, SESSION)

    reviewed, _ = reflect(good_run())

    assert reviewed == 1
    assert cards(store, "value_change") == []


# --- Failure and retry ------------------------------------------------------------------


def failing_run() -> list[AIMessage]:
    """Changes the brief and hypotheses and queues a card, then the model stops answering."""
    return good_run()[:6]


def test_a_failed_run_leaves_files_and_inbox_as_they_were(reflect, store):
    files = reflector_files.files_backend(store, USER)
    files.write("/brief.md", "Old brief.")
    files.write("/hypotheses.md", "## H9: Old pattern\n- Evidence: s_000")

    reviewed, _ = reflect(failing_run())

    assert reviewed == 0
    assert reflector_files.read_file(store, USER, reflector_files.BRIEF) == "Old brief."
    assert reflector_files.read_file(
        store, USER, reflector_files.HYPOTHESES
    ).startswith("## H9: Old pattern")
    assert reflector_files.read_file(store, USER, "sessions/s_001.md") == ""
    assert (
        inbox.list_cards(store, USER) == []
        and proposals.list_proposals(store, USER) == []
    )
    review = reviews.get_review(store, USER, SESSION)
    assert (review.status, review.attempts) == (reviews.STATUS_NOT_STARTED, 1)


def test_the_retry_after_a_failure_produces_no_duplicate_cards(reflect, store):
    reflect(failing_run())

    reviewed, _ = reflect(good_run())

    assert reviewed == 1
    assert len(cards(store, "learned")) == 1 and len(cards(store, "value_change")) == 1
    assert len(proposals.list_proposals(store, USER)) == 1
    assert reviews.get_review(store, USER, SESSION).attempts == 2


def test_a_session_is_skipped_after_three_failed_attempts(reflect, store):
    for _ in range(reviews.MAX_ATTEMPTS):
        reflect(failing_run())

    reviewed, model = reflect(good_run())

    assert reviews.MAX_ATTEMPTS == 3
    assert reviewed == 0 and model.calls == []
    assert reviews.get_review(store, USER, SESSION).status == reviews.STATUS_FAILED


def test_a_run_that_reaches_the_call_limit_fails_and_is_rolled_back(reflect, store):
    # Seen once in a live run, at the old limit of 25: the review kept updating its
    # to-do list. The limit is 35 now; reaching it still fails the run cleanly.
    todo = {"todos": [{"content": "Review", "status": "in_progress"}]}
    endless = [tool_call("write_todos", todo, f"c{n}") for n in range(60)]

    reviewed, model = reflect(endless)

    assert reviewed == 0
    assert len(model.calls) == reflector.REFLECTOR_MODEL_CALL_LIMIT == 35
    assert cards(store) == [] and cards(store, status=inbox.STATUS_STAGED) == []
    assert reviews.get_review(store, USER, SESSION).attempts == 1
    assert reviews.get_review(store, USER, SESSION).status == reviews.STATUS_NOT_STARTED


@pytest.mark.parametrize(
    "bad_summary",
    [
        summary(bullets=[]),
        summary(bullets=["You marked reels as not for you. [fb_999]"]),
        summary(bullets=["You marked reels as not for you."]),
    ],
)
def test_a_summary_without_valid_bullets_fails_the_run(reflect, store, bad_summary):
    reviewed, _ = reflect(good_run(text=bad_summary))

    assert reviewed == 0
    assert inbox.list_cards(store, USER) == []
    assert reflector_files.read_file(store, USER, reflector_files.BRIEF) == ""


@pytest.mark.parametrize("question", [None, "You should watch less of this."])
def test_a_missing_closing_question_does_not_fail_the_run(reflect, store, question):
    reviewed, _ = reflect(good_run(text=summary(question=question)))

    assert reviewed == 1
    (learned,) = cards(store, "learned")
    assert learned.prompt == f"- {BULLETS[0]}\n- {BULLETS[1]}"
    assert len(cards(store, "value_change")) == 1
    assert reviews.get_review(store, USER, SESSION).status == reviews.STATUS_DONE


def test_a_run_that_writes_no_summary_fails(reflect, store):
    reviewed, _ = reflect(
        [step for step in good_run() if "sessions/" not in str(step.tool_calls)]
    )

    assert reviewed == 0 and inbox.list_cards(store, USER) == []


def test_only_the_first_three_bullets_reach_the_learned_card(reflect, store):
    four = [f"Point {n} about hustle reels. [fb_002]" for n in range(1, 5)]

    reflect(good_run(text=summary(bullets=four)))

    (learned,) = cards(store, "learned")
    assert learned.prompt.count("\n- ") == 2 and "Point 4" not in learned.prompt
    assert learned.prompt.endswith(QUESTION)


# --- One run at a time, crashed runs, unfinished sessions ---------------------------------


def test_a_call_during_a_run_returns_zero_without_starting_another(reflect, store):
    reviews.begin(store, USER, SESSION, "run_other", NOW - timedelta(minutes=2))

    reviewed, model = reflect(good_run())

    assert reviewed == 0 and model.calls == []
    assert reviews.get_review(store, USER, SESSION).run_id == "run_other"


def test_a_call_while_this_process_is_reviewing_returns_zero(reflect, store):
    with locks.attempt(USER, locks.REFLECT) as held:
        reviewed, model = reflect(good_run())

    assert held is True

    assert reviewed == 0 and model.calls == []


def test_a_run_left_running_for_ten_minutes_is_treated_as_crashed_and_retried(
    reflect, store, source
):
    files = reflector_files.files_backend(store, USER)
    files.write("/brief.md", "Old brief.")
    reviews.begin(store, USER, SESSION, "run_crashed", NOW - timedelta(minutes=11))
    files.write("/brief.md", "Half-written brief from the crashed run.")
    proposals.create_proposal(
        store,
        source,
        USER,
        source=proposals.reflector_source(SESSION, "run_crashed"),
        now=NOW,
        status=proposals.STATUS_STAGED,
        **HUSTLE_CHANGE,
    )

    reviewed, _ = reflect(good_run())

    assert reviewed == 1
    assert reflector_files.read_file(store, USER, reflector_files.BRIEF) == BRIEF
    assert len(proposals.list_proposals(store, USER)) == 1
    assert reviews.get_review(store, USER, SESSION).attempts == 2


def test_a_crashed_run_is_rolled_back_even_if_the_retry_also_fails(reflect, store):
    files = reflector_files.files_backend(store, USER)
    files.write("/brief.md", "Old brief.")
    reviews.begin(store, USER, SESSION, "run_crashed", NOW - timedelta(minutes=11))
    files.write("/brief.md", "Half-written brief from the crashed run.")

    reflect([reply("I did nothing.")])

    assert reflector_files.read_file(store, USER, reflector_files.BRIEF) == "Old brief."


def test_a_session_still_in_progress_is_not_reviewed(reflect, clock):
    last_reel = max(
        s["last_reel_at"] for s in one_session_source(clock).list_sessions(USER)
    )
    open_source = one_session_source(clock, open_sessions=frozenset({SESSION}))

    soon, early_model = reflect(
        good_run(),
        datasource=open_source,
        when=lambda: last_reel + timedelta(minutes=5),
    )
    later, _ = reflect(
        good_run(),
        datasource=open_source,
        when=lambda: last_reel + timedelta(minutes=11),
    )

    assert soon == 0 and early_model.calls == []
    assert later == 1


class WithEmptySession:
    """A data source with one extra finished session that has no judged reels, as the
    real Judge reports a session whose reels could not be judged."""

    EMPTY = "s_empty"

    def __init__(self, inner: FakeDataSource) -> None:
        self._inner = inner

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        long_ago = NOW - timedelta(hours=3)
        empty = {
            "id": self.EMPTY,
            "start": long_ago,
            "last_reel_at": long_ago,
            "stopped": True,
        }
        return [empty, *self._inner.list_sessions(user_id)]

    def get_session_reels(self, user_id: str, session_id: str) -> list[Any]:
        if session_id == self.EMPTY:
            return []
        return self._inner.get_session_reels(user_id, session_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_a_finished_session_with_no_judged_reels_is_not_reviewed(reflect, store, clock):
    nothing = FakeDataSource.from_records(USER, reels=[], feedback=[], clock=clock)
    only_empty = WithEmptySession(nothing)

    first, model = reflect([], datasource=only_empty)
    second, again = reflect([], datasource=only_empty)

    assert (first, second) == (0, 0)
    assert model.calls == [] and again.calls == []
    skipped = reviews.get_review(store, USER, WithEmptySession.EMPTY)
    assert skipped.status == reviews.STATUS_SKIPPED and skipped.attempts == 0
    assert cards(store) == []


def test_an_empty_session_does_not_stop_the_others_being_reviewed(
    reflect, store, source
):
    reviewed, _ = reflect(good_run(), datasource=WithEmptySession(source))

    assert reviewed == 1
    assert [c.id for c in cards(store, "learned")] == ["learned_s_001"]
    assert (
        reviews.get_review(store, USER, WithEmptySession.EMPTY).status
        == reviews.STATUS_SKIPPED
    )
    assert reviews.get_review(store, USER, SESSION).status == reviews.STATUS_DONE


def test_no_model_is_built_when_only_empty_sessions_are_waiting(store, clock):
    nothing = FakeDataSource.from_records(USER, reels=[], feedback=[], clock=clock)
    api.configure(store, InMemorySaver(), WithEmptySession(nothing), clock=clock)

    def never() -> Any:
        raise AssertionError("no model should be built for an empty session")

    assert reflect_flow.reflect_pending(api._require_deps(), USER, never) == 0


def test_another_user_and_an_invalid_user_have_nothing_reviewed(reflect, store):
    _, model = reflect(good_run())
    calls_after_first = len(model.calls)

    assert api.reflect_pending(OTHER_USER) == 0
    assert api.reflect_pending("not a valid id") == 0
    assert len(model.calls) == calls_after_first
    assert inbox.list_cards(store, OTHER_USER) == []


def test_memory_files_stay_within_their_caps_across_five_sessions(store, clock):
    all_reels = json.loads((SEED_DIR / "reels.json").read_text())
    base = [r for r in all_reels if r["session_id"] == SESSION]
    reels = [
        {**r, "id": f"{r['id']}x{n}", "session_id": f"s_10{n}"}
        for n in range(1, 6)
        for r in base
    ]
    source = FakeDataSource.from_records(USER, reels=reels, feedback=[], clock=clock)
    script: list[AIMessage] = []
    for n in range(1, 6):
        session = f"s_10{n}"
        text = summary(
            bullets=[f"Session {n} had five conflicts. [{session}]"], session=session
        )
        script += good_run(session, change=None, text=text)
    model = ScriptedChatModel(script=script)
    api.configure(store, InMemorySaver(), source, clock=clock, reflector_model=model)

    assert api.reflect_pending(USER) == 5
    brief = reflector_files.read_file(store, USER, reflector_files.BRIEF)
    hypotheses = reflector_files.read_file(store, USER, reflector_files.HYPOTHESES)
    assert len(brief.split()) <= reflector_files.BRIEF_WORD_CAP
    assert hypotheses.count("## H") <= reflector_files.MAX_HYPOTHESES
    assert len(cards(store)) == 5
