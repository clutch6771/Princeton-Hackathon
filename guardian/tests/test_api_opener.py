"""Tests for the Guardian speaking first: `chat` with an empty message
(docs/GUARDIAN_REFLECTION_ADDENDUM.md)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from guardian import api, chat_flow
from guardian.memory import notes, records
from guardian.tests.conftest import NOW, OTHER_USER, USER
from guardian.tests.fakes import FailingChatModel, ScriptedChatModel, reply, tool_call

THREAD = f"{USER}:2026-11-14"
QUOTE = "gym stuff is great, comparing bodies is not"
FITNESS = f"""## New: Fitness without body comparison (strength: high)
- Want more: none
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "{QUOTE}"
- Evidence: said in chat"""
DRIFT = {"entry": "drift_notification", "reel_ids": ["rl_014", "rl_015"]}


def sent(model: ScriptedChatModel, call: int = -1) -> str:
    return [str(m.content) for m in model.calls[call] if isinstance(m, HumanMessage)][
        -1
    ]


def test_an_empty_message_makes_the_guardian_speak_first(start, store):
    model = start([reply("Hi! What would you like to see less of in your feed?")])

    response = api.chat(USER, THREAD, "")

    assert response == {
        "reply": "Hi! What would you like to see less of in your feed?",
        "pending": None,
        "error": None,
    }
    opening = sent(model)
    assert opening.startswith("CONTEXT") and opening.endswith("END CONTEXT")
    assert "entry: onboarding" in opening and chat_flow.OPENED_CHAT in opening
    assert records.user_chat_messages(store, USER) == []  # the user said nothing


def test_a_message_of_only_spaces_is_an_empty_message(start):
    model = start([reply("Hi!")])

    assert api.chat(USER, THREAD, "  \n ")["reply"] == "Hi!"
    assert chat_flow.OPENED_CHAT in sent(model)


def test_an_unanswered_opener_is_returned_again_without_calling_the_model(start):
    model = start([reply("Hi! What would you like to see less of?")])

    first = api.chat(USER, THREAD, "")
    second = api.chat(USER, THREAD, "")

    assert first == second and first["error"] is None
    assert len(model.calls) == 1


def test_reopening_the_chat_returns_the_guardians_last_unanswered_message(
    start, with_values
):
    model = start([reply("Does that match how you see it?")])
    api.chat(USER, THREAD, "why was that flagged?")

    reopened = api.chat(USER, THREAD, "")

    assert reopened["reply"] == "Does that match how you see it?"
    assert len(model.calls) == 1


def test_with_values_and_nothing_flagged_the_opening_is_an_ordinary_chat_entry(
    start, with_values
):
    model = start([reply("Does V1 still fit how you want your feed to feel?")])

    api.chat(USER, THREAD, "")

    opening = sent(model)
    assert "entry: chat" in opening and chat_flow.OPENED_CHAT in opening
    assert "reel_ids" not in opening


def test_once_the_user_has_answered_the_next_opening_follows_from_the_new_reply(
    start, with_values
):
    model = start([reply("One."), reply("Two.")])

    api.chat(USER, THREAD, "")
    api.chat(USER, THREAD, "ok")
    reopened = api.chat(USER, THREAD, "")

    assert reopened["reply"] == "Two."
    assert len(model.calls) == 2


def test_a_new_notification_is_explained_even_when_an_older_message_is_unanswered(
    start, with_values
):
    model = start([reply("Opener."), reply("Explained.")])
    api.chat(USER, THREAD, "")

    explained = api.chat(USER, THREAD, "", DRIFT)
    same_notification = api.chat(USER, THREAD, "", DRIFT)
    plain = api.chat(USER, THREAD, "")

    assert [r["reply"] for r in (explained, same_notification, plain)] == [
        "Explained."
    ] * 3
    assert len(model.calls) == 2
    notified = sent(model, 1)
    assert "reel_ids: rl_014, rl_015" in notified
    assert (
        chat_flow.LOOK_UP_REELS_FIRST in notified and chat_flow.OPENED_CHAT in notified
    )


def test_an_empty_message_while_a_card_is_pending_returns_the_card(start):
    args = {"value_id": "new", "new_text": FITNESS, "evidence": "said in chat"}
    model = start([tool_call("propose_value_change", args)])
    card = api.chat(USER, THREAD, f"Honestly, {QUOTE}.")["pending"]

    again = api.chat(USER, THREAD, "")

    assert again == {
        "reply": None,
        "pending": card,
        "error": chat_flow.ERROR_DECIDE_FIRST,
    }
    assert len(model.calls) == 1


def test_after_a_card_is_decided_reopening_returns_the_reply_that_followed(start):
    args = {"value_id": "new", "new_text": FITNESS, "evidence": "said in chat"}
    model = start(
        [tool_call("propose_value_change", args), reply("Saved as V1. Anything else?")]
    )
    api.chat(USER, THREAD, f"Honestly, {QUOTE}.")
    api.resume(USER, THREAD, {"action": "approve", "edited_text": None})

    assert api.chat(USER, THREAD, "")["reply"] == "Saved as V1. Anything else?"
    assert len(model.calls) == 2


def test_an_opener_that_failed_is_not_remembered(store, datasource, clock):
    saver = InMemorySaver()
    api.configure(store, saver, datasource, clock=clock, chat_model=FailingChatModel())
    failed = api.chat(USER, THREAD, "")
    working = ScriptedChatModel(script=[reply("Hi!")])
    api.configure(store, saver, datasource, clock=clock, chat_model=working)

    assert failed["error"] == chat_flow.ERROR_UNAVAILABLE and failed["reply"] is None
    assert api.chat(USER, THREAD, "")["reply"] == "Hi!"
    assert len(working.calls) == 1


def test_an_opener_can_draw_on_what_the_user_said_before(
    start, with_values, store, datasource
):
    said = "I scroll when I'm stressed before exams"
    records.append_chat_turn(store, USER, records.ROLE_USER, said, NOW)
    notes.save_note(store, datasource, USER, said, said, NOW)
    model = start(
        [
            tool_call("read_memory", {"name": "notes"}),
            reply("Last time you said you scroll when you're stressed. How was today?"),
        ]
    )

    response = api.chat(USER, THREAD, "")

    read = [m for m in model.calls[1] if isinstance(m, ToolMessage)][-1]
    assert said in str(read.content)
    assert response["reply"].startswith("Last time you said")


def test_each_thread_and_each_user_has_its_own_opener(start, with_values):
    model = start([reply("One."), reply("Two."), reply("Three.")])

    api.chat(USER, THREAD, "")
    next_day = api.chat(USER, f"{USER}:2026-11-15", "")
    someone_else = api.chat(OTHER_USER, f"{OTHER_USER}:2026-11-14", "")

    assert (next_day["reply"], someone_else["reply"]) == ("Two.", "Three.")
    assert len(model.calls) == 3


def test_a_message_that_is_not_text_is_an_error(start):
    model = start([reply("unused")])

    response = api.chat(USER, THREAD, None)  # type: ignore[arg-type]

    assert response["error"] == chat_flow.ERROR_BAD_MESSAGE and model.calls == []
