"""Tests for the inbox entry points: get_questions and answer_question."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from guardian import api
from guardian.memory import inbox, proposals, values
from guardian.tests.conftest import NOW, OTHER_USER, USER
from guardian.tests.fakes import ScriptedChatModel, reply

HUSTLE = {
    "value_id": "new",
    "new_text": """## New: Less get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, get-rich-quick pitches
- Unless: none
- Suggested wording: "I don't want hustle and get-rich-quick videos"
- Evidence: fb_002, fb_004, fb_007""",
    "evidence": "fb_002, fb_004, fb_007",
}
CARD_PROMPT = "You marked 3 reels like this as not for you. I've suggested wording."
LEARNED_PROMPT = "- You marked 3 hustle reels as not for you. [fb_002]\n\nWhat made them easy to skip?"
APPROVE = {"action": "approve"}


@pytest.fixture(autouse=True)
def configured(store, datasource, clock):
    api.configure(
        store,
        InMemorySaver(),
        datasource,
        clock=clock,
        chat_model=ScriptedChatModel(script=[reply("x")]),
    )


def queue_change(
    store,
    datasource,
    change=HUSTLE,
    prompt=CARD_PROMPT,
    run="run_1",
    when=NOW,
    user=USER,
) -> inbox.Card:
    """Put a pending value-change card in the inbox, as a committed Reflector run would."""
    created = proposals.create_proposal(
        store,
        datasource,
        user,
        source=proposals.reflector_source("s_001", run),
        now=when,
        **change,
    )
    assert created.error is None, created.error
    card = inbox.new_card(
        user,
        inbox.KIND_VALUE_CHANGE,
        prompt,
        when,
        session_id="s_001",
        proposal_id=created.proposal.id,
        run_id=run,
    )
    inbox.add_card(store, card)
    return card


def add_learned(store, when=NOW, card_id="learned_s_001") -> inbox.Card:
    card = inbox.new_card(
        USER,
        inbox.KIND_LEARNED,
        LEARNED_PROMPT,
        when,
        card_id=card_id,
        session_id="s_001",
    )
    inbox.add_card(store, card)
    return card


def refinement(store, unless: str) -> dict[str, str]:
    stored = values.get_value(store, USER, "V1").text
    return {
        "value_id": "V1",
        "new_text": stored.replace("- Unless: none", f"- Unless: {unless}"),
        "evidence": "fb_001",
    }


# --- get_questions --------------------------------------------------------------------


def test_get_questions_returns_pending_cards_in_the_spec_shape(store, datasource):
    learned = add_learned(store, when=NOW - timedelta(minutes=1))
    change = queue_change(store, datasource)

    questions = api.get_questions(USER)

    assert [q["id"] for q in questions] == [learned.id, change.id]
    assert questions[0] == {
        "id": "learned_s_001",
        "kind": "learned",
        "prompt": LEARNED_PROMPT,
        "session_id": "s_001",
        "value_change": None,
        "reel_ids": None,
        "created": NOW - timedelta(minutes=1),
    }
    assert (
        questions[1]["kind"] == "value_change" and questions[1]["prompt"] == CARD_PROMPT
    )
    assert questions[1]["value_change"] == {
        "value_id": "new",
        "old_text": None,
        "new_text": HUSTLE["new_text"],
        "evidence": HUSTLE["evidence"],
    }
    assert isinstance(questions[1]["created"], datetime)


def test_get_questions_hides_staged_and_answered_cards_and_other_users(
    store, datasource
):
    staged = inbox.new_card(
        USER,
        inbox.KIND_LEARNED,
        "staged",
        NOW,
        status=inbox.STATUS_STAGED,
        card_id="staged_1",
    )
    inbox.add_card(store, staged)
    answered = add_learned(store)
    api.answer_question(USER, answered.id, {"action": "dismiss"})

    assert api.get_questions(USER) == []
    assert api.get_questions(OTHER_USER) == [] and api.get_questions("bad id") == []


def test_a_chat_card_does_not_appear_in_the_inbox(store, datasource):
    proposals.create_proposal(
        store,
        datasource,
        USER,
        source=proposals.chat_source(f"{USER}:2026-11-14", "c1"),
        now=NOW,
        **HUSTLE,
    )

    assert api.get_questions(USER) == []


# --- answer_question: value_change -----------------------------------------------------


def test_approving_an_inbox_card_changes_what_get_values_returns(
    store, datasource, with_values
):
    card = queue_change(store, datasource)
    before = api.get_values(USER)

    result = api.answer_question(USER, card.id, APPROVE)

    after = api.get_values(USER)
    assert result == {"ok": True, "error": None}
    assert after != before and after.startswith(before)
    assert "## V4: Less get-rich-quick content (strength: medium)" in after
    assert '- Agreed wording: "I don\'t want hustle and get-rich-quick videos"' in after
    assert api.get_questions(USER) == []
    assert proposals.get_proposal(store, USER, card.proposal_id).status == "approved"


def test_a_valid_edit_stores_the_users_wording(store, datasource):
    card = queue_change(store, datasource)
    edited = HUSTLE["new_text"].replace(
        "I don't want hustle and get-rich-quick videos", "no money-bro stuff"
    )

    result = api.answer_question(
        USER, card.id, {"action": "edit", "edited_text": edited}
    )

    assert result == {"ok": True, "error": None}
    assert '- In my words: "no money-bro stuff"' in api.get_values(USER)


@pytest.mark.parametrize(
    "answer", [{"action": "edit", "edited_text": "less hustle"}, {"action": "edit"}]
)
def test_an_invalid_edit_changes_nothing_and_keeps_the_card(store, datasource, answer):
    card = queue_change(store, datasource)

    result = api.answer_question(USER, card.id, answer)

    assert result["ok"] is False and result["error"]
    assert api.get_values(USER) == ""
    assert [q["id"] for q in api.get_questions(USER)] == [card.id]
    assert api.answer_question(USER, card.id, APPROVE) == {"ok": True, "error": None}


def test_rejecting_a_card_stores_nothing_and_removes_it(store, datasource):
    card = queue_change(store, datasource)

    result = api.answer_question(USER, card.id, {"action": "reject"})

    assert result == {"ok": True, "error": None}
    assert api.get_values(USER) == "" and api.get_questions(USER) == []
    assert proposals.get_proposal(store, USER, card.proposal_id).status == "rejected"


@pytest.mark.parametrize("first", ["approve", "reject"])
def test_a_card_can_be_answered_only_once(store, datasource, first):
    card = queue_change(store, datasource)
    api.answer_question(USER, card.id, {"action": first})
    before = api.get_values(USER)

    again = api.answer_question(USER, card.id, APPROVE)

    assert again["ok"] is False and "already" in again["error"]
    assert api.get_values(USER) == before


def test_another_users_card_is_not_found_and_is_left_alone(store, datasource):
    card = queue_change(store, datasource)

    result = api.answer_question(OTHER_USER, card.id, APPROVE)

    assert result["ok"] is False and "does not exist" in result["error"]
    assert api.get_values(USER) == "" and api.get_values(OTHER_USER) == ""
    assert [q["id"] for q in api.get_questions(USER)] == [card.id]


def test_a_stale_card_is_refused_and_closed(store, datasource, with_values):
    first = queue_change(
        store, datasource, change=refinement(store, "injury rehab"), run="run_1"
    )
    second = queue_change(
        store, datasource, change=refinement(store, "physio progress"), run="run_2"
    )
    api.answer_question(USER, first.id, APPROVE)

    result = api.answer_question(USER, second.id, APPROVE)

    assert result["ok"] is False and "changed" in result["error"]
    assert "Unless: injury rehab" in api.get_values(
        USER
    ) and "physio progress" not in api.get_values(USER)
    assert api.get_questions(USER) == []
    assert (
        proposals.get_proposal(store, USER, second.proposal_id).status == "superseded"
    )


def test_a_new_value_at_the_cap_is_refused_and_the_card_stays(store, datasource):
    card = queue_change(store, datasource)
    for number in range(values.MAX_VALUES):
        change = {
            **HUSTLE,
            "new_text": HUSTLE["new_text"].replace(
                "Less get-rich-quick content", f"Topic {number}"
            ),
        }
        filler = queue_change(store, datasource, change=change, run=f"fill_{number}")
        api.answer_question(USER, filler.id, APPROVE)

    result = api.answer_question(USER, card.id, APPROVE)

    assert result["ok"] is False and str(values.MAX_VALUES) in result["error"]
    assert [q["id"] for q in api.get_questions(USER)] == [card.id]


# --- answer_question: learned cards and invalid answers ---------------------------------


def test_dismissing_a_learned_card_removes_it(store):
    card = add_learned(store)

    assert api.answer_question(USER, card.id, {"action": "dismiss"}) == {
        "ok": True,
        "error": None,
    }
    assert api.get_questions(USER) == []
    assert inbox.get_card(store, USER, card.id).status == inbox.STATUS_ANSWERED


@pytest.mark.parametrize(
    ("kind", "answer"),
    [
        ("learned", {"action": "approve"}),
        ("learned", {"action": "reject"}),
        ("learned", {"action": "rate", "ratings": {"rl_003": "fine"}}),
        ("learned", {}),
        ("value_change", {"action": "dismiss"}),
        ("value_change", {"action": "rate", "ratings": {"rl_003": "fine"}}),
        ("value_change", {"action": "skip"}),
        ("value_change", {"action": "delete everything"}),
        ("value_change", None),
    ],
)
def test_an_action_that_does_not_fit_the_card_is_refused(
    store, datasource, kind, answer
):
    card = add_learned(store) if kind == "learned" else queue_change(store, datasource)

    result = api.answer_question(USER, card.id, answer)

    assert result["ok"] is False and result["error"]
    assert [q["id"] for q in api.get_questions(USER)] == [card.id]
    assert api.get_values(USER) == ""


def test_unknown_question_ids_and_invalid_users_are_refused():
    assert api.answer_question(USER, "q_missing", APPROVE)["ok"] is False
    assert api.answer_question("bad id", "q_missing", APPROVE)["ok"] is False


def test_a_rerate_card_cannot_be_answered_in_this_build(store):
    card = inbox.new_card(
        USER, inbox.KIND_RERATE, "Quick check", NOW, card_id="rerate_1"
    )
    inbox.add_card(store, card)

    result = api.answer_question(USER, card.id, {"action": "skip"})

    assert result["ok"] is False and "not available" in result["error"]
