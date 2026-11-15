"""The Guardian is never hostile and never leaves the user with a non-answer.

Covers the rules the prompt carries for tone, deleting, and replies that do not fit
the question; the reply the user gets when the model writes nothing; a message sent
instead of a decision on a card; and the forced removal used by developer commands.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from guardian import api, chat_flow, prompts
from guardian.memory import notes, proposals, records, values
from guardian.tests.conftest import NOW, OTHER_USER, USER
from guardian.tests.fakes import reply, tool_call

THREAD = f"{USER}:2026-11-14"
GUARDIAN = " ".join(prompts.GUARDIAN_PROMPT.split())
QUOTE = "gym stuff is great, comparing bodies is not"
FITNESS = f"""## New: Fitness without body comparison (strength: high)
- Want more: none
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "{QUOTE}"
- Evidence: said in chat"""


def propose(call: str = "call_1"):
    args = {"value_id": "new", "new_text": FITNESS, "evidence": "said in chat"}
    return tool_call("propose_value_change", args, call)


def removal(value_id: str, call: str):
    args = {"value_id": value_id, "evidence": "asked in chat"}
    return tool_call("propose_value_removal", args, call)


# --- The prompt ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        # Tone.
        "Never hostile, cold, sarcastic, defensive or scolding",
        "If the user is curt, rude or frustrated, stay kind",
        # Deleting is the user's call.
        "even all of it, agree at once and do it",
        "Do not ask them to justify it, do not try to talk them out of it",
        "Never tell the user that a tool refused",
        "If they ask to remove several values, or all of them",
        "Do not ask whether they are sure, and do not ask why",
        # A reply that does not fit the question still gets a real answer.
        "do not repeat the question and do not brush it off in a line",
        "First answer what they actually said, with real content",
        "two or three concrete examples to pick from",
        "Every reply has text for the user",
    ],
)
def test_the_guardian_prompt_carries_the_rule(rule):
    assert rule in GUARDIAN


# --- A turn with no text -----------------------------------------------------------------------


def test_a_turn_with_no_text_is_asked_for_once_more(start, with_values):
    model = start([reply(""), reply("Fair enough. Which reel was it?")])

    response = api.chat(USER, THREAD, "idk")

    assert response == {
        "reply": "Fair enough. Which reel was it?",
        "pending": None,
        "error": None,
    }
    asked = model.calls[1][-1]
    assert isinstance(asked, HumanMessage)
    assert asked.content == chat_flow.WRITE_THE_REPLY


def test_if_the_model_still_writes_nothing_the_user_gets_a_real_answer(
    start, with_values, store
):
    start([reply(""), reply("")])

    response = api.chat(USER, THREAD, "idk")

    assert response["reply"] == chat_flow.EMPTY_REPLY_FALLBACK
    assert response["error"] is None
    assert len(response["reply"]) > 80  # an answer the user can act on
    logged = records.chat_turns(store, USER)[-1]
    assert logged["text"] == chat_flow.EMPTY_REPLY_FALLBACK


def test_a_card_with_no_words_is_not_asked_for_text(start):
    model = start([propose()])

    response = api.chat(USER, THREAD, f"Honestly, {QUOTE}.")

    assert response["pending"] is not None and response["reply"] is None
    assert len(model.calls) == 1


# --- A message instead of a decision -------------------------------------------------------------


def test_a_message_instead_of_a_decision_sets_the_card_aside_and_is_answered(
    start, store
):
    model = start([propose(), reply("High means it is flagged every time.")])
    api.chat(USER, THREAD, f"Honestly, {QUOTE}.")

    response = api.reply_instead(USER, THREAD, "what does strength mean?")

    assert response == {
        "reply": "High means it is flagged every time.",
        "pending": None,
        "error": None,
    }
    assert api.get_values(USER) == ""
    assert api.pending_card(USER, THREAD) is None
    told = str([m for m in model.calls[1] if isinstance(m, ToolMessage)][-1].content)
    assert "set aside" in told and '"what does strength mean?"' in told
    assert records.user_chat_messages(store, USER)[-1] == "what does strength mean?"


def test_a_card_that_was_set_aside_can_be_proposed_again(start):
    start([propose(), reply("Take your time."), propose("call_2"), reply("Saved.")])
    api.chat(USER, THREAD, f"Honestly, {QUOTE}.")
    api.reply_instead(USER, THREAD, "let me think")

    again = api.chat(USER, THREAD, "okay, add it")
    api.resume(USER, THREAD, {"action": "approve", "edited_text": None})

    assert again["pending"]["value_change"]["new_text"] == FITNESS
    assert api.get_values(USER).startswith("## V1: Fitness without body comparison")


def test_with_no_card_waiting_it_is_an_ordinary_turn(start, with_values):
    start([reply("Hello.")])

    assert api.reply_instead(USER, THREAD, "hi")["reply"] == "Hello."


def test_reply_instead_checks_the_thread_belongs_to_the_user(start):
    start([])

    response = api.reply_instead(OTHER_USER, THREAD, "hi")

    assert response["error"] == chat_flow.ERROR_WRONG_THREAD


def test_the_next_card_of_several_comes_with_a_confirmation(start, with_values):
    start([removal("V1", "c1"), removal("V2", "c2"), reply("All done.")])
    api.chat(USER, THREAD, "delete V1 and V2")

    second = api.resume(USER, THREAD, {"action": "approve", "edited_text": None})

    assert second["reply"] == "Removed V1."
    assert second["pending"]["value_change"]["value_id"] == "V2"
    assert second["error"] is None


# --- Forgetting a note -----------------------------------------------------------------------------


def test_a_note_is_forgotten_when_the_request_was_a_message_or_two_back(
    start, store, datasource, with_values
):
    said = "I scroll when I'm stressed before exams"
    records.append_chat_turn(store, USER, records.ROLE_USER, said, NOW)
    note_id = notes.save_note(store, datasource, USER, said, said, NOW).note_id
    start(
        [
            reply("Which note would you like gone?"),
            tool_call("forget_note", {"note_id": note_id}),
            reply("That note is removed."),
        ]
    )

    api.chat(USER, THREAD, "please delete one of the things you saved about me")
    api.chat(USER, THREAD, "the exam one")

    assert notes.list_notes(store, USER) == []


def test_a_refusal_tells_the_guardian_to_ask_kindly(
    start, store, datasource, with_values
):
    said = "I train three times a week"
    records.append_chat_turn(store, USER, records.ROLE_USER, said, NOW)
    note_id = notes.save_note(store, datasource, USER, said, said, NOW).note_id
    model = start([tool_call("forget_note", {"note_id": note_id}), reply("Okay.")])

    api.chat(USER, THREAD, "what are my values again?")

    told = str([m for m in model.calls[1] if isinstance(m, ToolMessage)][-1].content)
    assert "Do not tell them you refused" in told and "kindly" in told
    assert len(notes.list_notes(store, USER)) == 1


# --- The forced removal, for developer commands ------------------------------------------------------


def test_force_delete_removes_every_value_of_that_user_only(
    start, store, datasource, with_values
):
    start([])
    from guardian.seed.install import install_initial_values

    install_initial_values(store, datasource, OTHER_USER, NOW)

    deleted = api.force_delete_values(USER)

    assert deleted == ["V1", "V2", "V3"]
    assert api.get_values(USER) == "" and not api.has_values(USER)
    assert "## V1:" in api.get_values(OTHER_USER)
    assert api.force_delete_values(USER) == []
    assert api.force_delete_values("not a user!") == []


def test_force_deleted_ids_are_never_used_again(start, store, with_values):
    start([])

    api.force_delete_values(USER)

    assert values.allocate_value_id(store, USER) == "V4"


def test_list_users_names_every_user_with_a_record(
    start, store, datasource, with_values
):
    start([])
    records.append_chat_turn(store, OTHER_USER, records.ROLE_USER, "hi", NOW)

    assert api.list_users() == sorted([USER, OTHER_USER])


def test_set_aside_closes_only_a_waiting_card(store, datasource, with_values):
    created = proposals.create_removal(
        store,
        datasource,
        USER,
        value_id="V2",
        evidence="asked in chat",
        source=proposals.chat_source(THREAD, "c1"),
        now=NOW,
    )

    assert proposals.set_aside(store, USER, created.proposal.id, NOW) is True
    assert proposals.set_aside(store, USER, created.proposal.id, NOW) is False
    assert proposals.set_aside(store, OTHER_USER, created.proposal.id, NOW) is False
    assert proposals.pending_for_thread(store, USER, THREAD) is None
    assert "## V2:" in values.render_values(store, USER)
