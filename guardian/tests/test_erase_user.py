"""Tests for erasing one user's records from the store and the chat threads."""

from __future__ import annotations

from guardian import api
from guardian.memory import inbox, notes, records, values
from guardian.seed.install import install_initial_values
from guardian.tests.conftest import NOW, OTHER_USER, USER
from guardian.tests.fakes import reply, tool_call

THREAD = f"{USER}:2026-11-14"
QUOTE = "gym stuff is great, comparing bodies is not"
FITNESS = f"""## New: Body comparison (strength: high)
- Want more: none
- Want less: physique rating
- Unless: none
- In my words: "{QUOTE}"
- Evidence: said in chat"""


def test_erase_user_removes_everything_stored_about_that_user(
    start, store, datasource, with_values
):
    args = {"value_id": "new", "new_text": FITNESS, "evidence": "said in chat"}
    start([reply("Hello."), tool_call("propose_value_change", args)])
    api.chat(USER, THREAD, "hi")
    api.chat(USER, THREAD, f"Honestly, {QUOTE}.")  # leaves a card pending on the thread
    notes.save_note(store, datasource, USER, "Trains a lot", "hi", NOW)
    inbox.add_card(store, inbox.new_card(USER, "learned", "- A bullet.", NOW))

    counts = api.erase_user(USER)

    assert counts["store"] > 0 and counts["threads"] == 1
    assert api.get_values(USER) == "" and api.get_questions(USER) == []
    assert api.pending_card(USER, THREAD) is None
    assert set(api.get_memory(USER).values()) == {""}
    assert records.user_chat_messages(store, USER) == []
    assert api.erase_user(USER) == {"store": 0, "threads": 0}  # nothing left


def test_erase_user_leaves_other_users_alone(start, store, datasource, with_values):
    start([reply("Hello.")])
    for other in (OTHER_USER, "a_b", "aXb"):
        install_initial_values(store, datasource, other, NOW)
    api.chat(OTHER_USER, f"{OTHER_USER}:2026-11-14", "hi")

    api.erase_user("a_b")

    assert values.list_values(store, "a_b") == []
    for other in (USER, OTHER_USER, "aXb"):
        assert len(values.list_values(store, other)) == 3
    assert "hi" in records.user_chat_messages(store, OTHER_USER)
    assert records.user_chat_messages(store, "a_b") == []


def test_erase_user_refuses_an_invalid_user_id(start):
    start([])

    assert api.erase_user("not valid!") == {"store": 0, "threads": 0}
