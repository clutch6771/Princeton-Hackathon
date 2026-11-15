"""Tests for reel explanation and feedback in chat: the read tools, the value-citation
check, record_feedback and save_note. A scripted model stands in for Gemini.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from deepagents.backends import StoreBackend
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from guardian import api, middleware, tools_chat
from guardian.datasource import SEED_DIR, FakeDataSource
from guardian.memory import checks, keys, notes, records
from guardian.tests.conftest import NOW, OTHER_USER, USER
from guardian.tests.fakes import ScriptedChatModel, reply, tool_call

THREAD = f"{USER}:2026-11-14"
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
DRIFT = {"entry": "drift_notification", "reel_ids": ["rl_014", "rl_015", "rl_016"]}
OPEN, CLOSE = middleware.UNTRUSTED_OPEN, middleware.UNTRUSTED_CLOSE
STRESS = "I scroll when I'm stressed before exams"


def seen(model: ScriptedChatModel, call: int) -> list[str]:
    """Tool results the model was shown on a given call."""
    return [str(m.content) for m in model.calls[call] if isinstance(m, ToolMessage)]


def is_labelled(content: str) -> bool:
    return content.startswith(OPEN) and content.endswith(CLOSE)


# --- get_reels and the drift notification ----------------------------------------------


def test_get_reels_gives_the_agent_each_reel_as_labelled_data(start, with_values):
    ids = ["rl_014", "rl_015", "rl_missing"]
    model = start([tool_call("get_reels", {"reel_ids": ids}), reply("ok")])

    api.chat(USER, THREAD, "why these?", context=DRIFT)

    (content,) = seen(model, 1)
    assert is_labelled(content)
    for expected in (
        "rl_014",
        "Before-and-after body transformation montage",
        "90 days. No excuses.",
        "conflict",
        "V1",
        "Shows body comparison, which V1 says you want less of.",
        "rate these five physiques",
        "Not found: rl_missing",
    ):
        assert expected in content


def test_get_reels_cuts_long_transcripts_to_400_characters(store, clock):
    record = {
        **json.loads((SEED_DIR / "reels.json").read_text())[0],
        "transcript": "word " * 300,
    }
    datasource = FakeDataSource.from_records(USER, reels=[record], feedback=[])
    model = ScriptedChatModel(
        script=[tool_call("get_reels", {"reel_ids": [record["id"]]}), reply("ok")]
    )
    api.configure(store, InMemorySaver(), datasource, clock=clock, chat_model=model)

    api.chat(USER, THREAD, "what was this?")

    line = next(
        text for text in seen(model, 1)[0].splitlines() if text.startswith("Transcript")
    )
    excerpt = line.split(": ", 1)[1]
    assert tools_chat.TRANSCRIPT_EXCERPT_CHARACTERS == 400
    assert len(excerpt) <= 401 and excerpt.endswith("…")


def test_another_user_cannot_read_the_demo_users_reels(start):
    model = start([tool_call("get_reels", {"reel_ids": ["rl_014"]}), reply("ok")])

    api.chat(OTHER_USER, f"{OTHER_USER}:2026-11-14", "show me rl_014")

    (content,) = seen(model, 1)
    assert "Not found: rl_014" in content and "body transformation" not in content


def test_drift_notification_is_explained_with_stored_value_ids(start, with_values):
    explanation = "rl_014 was a transformation montage, which touches V1. rl_016 was a shouting panel: V2."
    model = start(
        [tool_call("get_reels", {"reel_ids": DRIFT["reel_ids"]}), reply(explanation)]
    )

    response = api.chat(USER, THREAD, "why did I get this?", context=DRIFT)

    assert response == {"reply": explanation, "pending": None, "error": None}
    assert len(model.calls) == 2
    assert "reel_ids: rl_014, rl_015, rl_016" in str(model.calls[0][-1].content)


# --- Value-citation check ---------------------------------------------------------------


def test_reply_citing_an_unknown_value_is_retried_once_and_history_is_cleaned(
    start, with_values
):
    model = start(
        [reply("That touches V9."), reply("That touches V1."), reply("Sure.")]
    )

    response = api.chat(USER, THREAD, "why?")
    api.chat(USER, THREAD, "thanks")

    assert response["reply"] == "That touches V1."
    correction = [
        str(m.content) for m in model.calls[1] if isinstance(m, HumanMessage)
    ][-1]
    assert "V9" in correction and "V1, V2, V3" in correction
    later_history = [
        str(m.content) for m in model.calls[2] if not isinstance(m, SystemMessage)
    ]
    assert not any("V9" in text for text in later_history)
    assert len(model.calls) == 3


def test_second_unknown_citation_is_replaced_with_a_plain_message(
    start, store, with_values
):
    model = start([reply("That touches V9."), reply("Then it must be V8.")])

    response = api.chat(USER, THREAD, "why?")

    assert response == {
        "reply": middleware.NO_VALUE_MATCH_REPLY,
        "pending": None,
        "error": None,
    }
    assert len(model.calls) == 2
    assert (
        records.chat_turns(store, USER)[-1]["text"] == middleware.NO_VALUE_MATCH_REPLY
    )


def test_reply_citing_only_stored_values_is_left_alone(start, with_values):
    model = start([reply("That one touches V2, and the other V3.")])

    response = api.chat(USER, THREAD, "why?")

    assert response["reply"] == "That one touches V2, and the other V3."
    assert len(model.calls) == 1


def test_user_with_no_values_never_gets_an_invented_citation(start):
    model = start(
        [reply("That touches V1."), reply("Nothing you've saved matches yet.")]
    )

    response = api.chat(USER, THREAD, "why?")

    assert response["reply"] == "Nothing you've saved matches yet."
    assert len(model.calls) == 2


def test_text_written_alongside_a_tool_call_is_not_checked(start, with_values):
    call = tool_call("get_reels", {"reel_ids": ["rl_014"]})
    model = start(
        [
            AIMessage(content="Comparing with V9...", tool_calls=call.tool_calls),
            reply("It touches V1."),
        ]
    )

    response = api.chat(USER, THREAD, "why?")

    assert response["reply"] == "It touches V1."
    assert len(model.calls) == 2


# --- record_feedback ---------------------------------------------------------------------


def test_record_feedback_stores_the_reaction_with_chat_as_its_source(
    start, datasource, with_values
):
    args = {"reel_id": "rl_014", "label": "disagree", "comment": "this one is fine"}
    model = start([tool_call("record_feedback", args), reply("Noted.")])

    api.chat(USER, THREAD, "this one is fine")

    recorded = [
        f for f in datasource.get_feedback(USER, EPOCH) if f["source"] == "chat"
    ]
    assert [(f["reel_id"], f["label"], f["comment"]) for f in recorded] == [
        ("rl_014", "disagree", "this one is fine")
    ]
    assert seen(model, 1) == [f"Recorded as {recorded[0]['id']}."]


def test_record_feedback_for_an_unknown_reel_reports_the_problem(
    start, datasource, with_values
):
    model = start(
        [
            tool_call("record_feedback", {"reel_id": "rl_missing", "label": "agree"}),
            reply("Hm."),
        ]
    )

    api.chat(USER, THREAD, "yes that was bad")

    assert seen(model, 1)[0].startswith("Could not record")
    assert [
        f for f in datasource.get_feedback(USER, EPOCH) if f["source"] == "chat"
    ] == []


def test_a_comment_written_by_the_agent_cannot_later_be_quoted_as_the_user(
    start, store, datasource, with_values
):
    # Found in a live run: the model paraphrases the user's reason in `comment`.
    invented = "transformation videos motivate me"
    args = {"reel_id": "rl_014", "label": "disagree", "comment": invented}
    start([tool_call("record_feedback", args), reply("Noted.")])

    api.chat(USER, THREAD, "that one was fine")

    statements = checks.user_statements(store, datasource, USER)
    assert invented not in statements
    assert "that one was fine" in statements
    assert not checks.quote_found(invented, statements)


# --- save_note ---------------------------------------------------------------------------


def test_save_note_stores_what_the_user_said_under_a_stable_id(start):
    args = {"text": "Scrolls when stressed before exams", "evidence": STRESS}
    model = start([tool_call("save_note", args), reply("Got it.")])

    api.chat(USER, THREAD, f"honestly, {STRESS}")

    assert seen(model, 1) == ["Saved as N1."]
    assert (
        api.get_memory(USER)["notes.md"]
        == f'- N1: Scrolls when stressed before exams (said: "{STRESS}")'
    )
    assert api.get_memory(OTHER_USER)["notes.md"] == ""


def test_save_note_refuses_evidence_the_user_did_not_say(start):
    args = {"text": "Hates their job", "evidence": "I hate my job"}
    model = start([tool_call("save_note", args), reply("Okay.")])

    api.chat(USER, THREAD, "long day today")

    assert seen(model, 1)[0].startswith("Refused")
    assert api.get_memory(USER)["notes.md"] == ""


def test_note_ids_increase_and_a_repeated_note_is_not_stored_twice(store, datasource):
    records.append_chat_turn(
        store, USER, records.ROLE_USER, f"{STRESS}. I gym three times a week.", NOW
    )

    first = notes.save_note(
        store, datasource, USER, "Scrolls when stressed", STRESS, NOW
    )
    second = notes.save_note(
        store,
        datasource,
        USER,
        "Gyms three times a week",
        "I gym three times a week",
        NOW,
    )
    repeat = notes.save_note(
        store, datasource, USER, "scrolls  when STRESSED", STRESS, NOW
    )

    assert (first.note_id, second.note_id, repeat.note_id) == ("N1", "N2", "N1")
    assert len(notes.list_notes(store, USER)) == 2


@pytest.mark.parametrize(
    ("text", "evidence"), [("", STRESS), ("A fact", ""), ("x" * 301, STRESS)]
)
def test_save_note_validates_its_input(store, datasource, text, evidence):
    records.append_chat_turn(store, USER, records.ROLE_USER, STRESS, NOW)

    result = notes.save_note(store, datasource, USER, text, evidence, NOW)

    assert result.note_id is None and result.error
    assert notes.list_notes(store, USER) == []


def test_notes_are_capped_and_ids_are_not_reused(store, datasource):
    records.append_chat_turn(store, USER, records.ROLE_USER, STRESS, NOW)
    for number in range(notes.MAX_NOTES):
        notes.save_note(store, datasource, USER, f"Fact {number}", STRESS, NOW)

    over = notes.save_note(store, datasource, USER, "One more fact", STRESS, NOW)
    store.delete(keys.namespace(USER, keys.NOTES), "N40")
    after_removal = notes.save_note(
        store, datasource, USER, "One more fact", STRESS, NOW
    )

    assert notes.MAX_NOTES == 40
    assert over.note_id is None and "40" in over.error
    assert after_removal.note_id == "N41"


# --- The other read tools -----------------------------------------------------------------


def test_feedback_search_and_reel_search_return_labelled_results(start, with_values):
    model = start(
        [
            tool_call(
                "find_similar_feedback", {"text": "knee surgery rehab progress"}, "c1"
            ),
            tool_call("search_reels", {"query": "recipe dinner"}, "c2"),
            reply("ok"),
        ]
    )

    api.chat(USER, THREAD, "have I seen this before?")

    similar, searched = seen(model, 2)
    assert is_labelled(similar) and is_labelled(searched)
    assert (
        "rl_001" in similar
        and "disagree" in similar
        and "rehab not comparison" in similar
    )
    assert "rl_013" in searched and "rl_037" not in searched


def test_feed_profile_covers_the_latest_session_or_the_past_week(start, with_values):
    model = start(
        [
            tool_call("feed_profile", {"window": "session"}, "c1"),
            tool_call("feed_profile", {"window": "week"}, "c2"),
            reply("ok"),
        ]
    )

    api.chat(USER, THREAD, "what have I been watching?")

    session, week = seen(model, 2)
    assert is_labelled(session) and is_labelled(week)
    assert "Reels: 8" in session and "Conflicts: 4" in session and "fitness" in session
    assert "Reels: 20" in week and "Conflicts: 9" in week


def test_read_memory_returns_notes_and_the_latest_session_summary(
    start, store, datasource, with_values
):
    records.append_chat_turn(store, USER, records.ROLE_USER, STRESS, NOW)
    notes.save_note(store, datasource, USER, "Scrolls when stressed", STRESS, NOW)
    files = StoreBackend(
        namespace=lambda _rt: keys.reflector_files_namespace(USER), store=store
    )
    files.write("/sessions/s_001.md", "Older session summary.")
    files.write("/sessions/s_002.md", "Mostly cooking, then four conflicts.")
    model = start(
        [
            tool_call("read_memory", {"name": "notes"}, "c1"),
            tool_call("read_memory", {"name": "last_session"}, "c2"),
            reply("ok"),
        ]
    )

    api.chat(USER, THREAD, "remind me")

    saved_notes, summary = seen(model, 2)
    assert is_labelled(saved_notes) and "N1: Scrolls when stressed" in saved_notes
    assert is_labelled(summary) and "Mostly cooking, then four conflicts." in summary
    assert "Older session summary" not in summary


def test_read_memory_says_so_when_nothing_is_stored(start):
    model = start(
        [
            tool_call("read_memory", {"name": "notes"}, "c1"),
            tool_call("read_memory", {"name": "last_session"}, "c2"),
            reply("Nothing saved yet."),
        ]
    )

    api.chat(USER, THREAD, "remind me")

    saved_notes, summary = seen(model, 2)
    assert "No notes saved yet." in saved_notes
    # The last session has no summary yet, so the Judge's records are given instead.
    assert "has not been reviewed yet" in summary and "8 judged reels" in summary


def test_chat_agent_offers_exactly_the_eight_tools_in_the_spec(start):
    model = start([reply("hello")])

    api.chat(USER, THREAD, "hi")

    assert set(model.bound_tool_names) == {
        "get_reels",
        "find_similar_feedback",
        "search_reels",
        "feed_profile",
        "read_memory",
        "record_feedback",
        "save_note",
        "propose_value_change",
    }


def test_only_tools_whose_results_are_written_by_code_are_trusted():
    assert tools_chat.TRUSTED_TOOLS == frozenset(
        {"propose_value_change", "record_feedback", "save_note"}
    )
