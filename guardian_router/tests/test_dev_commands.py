"""Tests for the developer commands typed into the chat as "/dev <command>"."""

from __future__ import annotations

from datetime import timedelta

import pytest

from guardian import api
from guardian.memory import proposals, records
from guardian.seed.install import install_initial_values
from guardian.tests.fakes import reply, tool_call
from guardian_router import background, dev_commands
from guardian_router.tests.conftest import HEADERS, NOW, USER

OTHER = "second-user"
OTHER_HEADERS = {"X-Demo-User-Id": OTHER}


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setenv(dev_commands.ENABLE_VAR, "1")


def say_as(server, user: str, message: str) -> str:
    body = {"message": message, "profile": {"stated_goals": ""}}
    headers = {"X-Demo-User-Id": user}
    return server.client.post("/agent2/chat", json=body, headers=headers).json()["text"]


def two_profiles(serve, script=None):
    """A server on which two users each have the three seed values."""
    server = serve(script or [], seeded=True)
    install_initial_values(server.store, server.datasource, OTHER, NOW)
    return server


def test_the_commands_are_off_unless_the_server_allows_them(serve, monkeypatch):
    monkeypatch.delenv(dev_commands.ENABLE_VAR, raising=False)
    server = serve([], seeded=True)

    for command in ("/dev help", "/dev wipe-values all", "/dev reset", "/DEV values"):
        assert server.say(command) == dev_commands.DISABLED, command

    assert "## V1:" in api.get_values(USER)
    assert server.model.calls == []


def test_help_lists_every_command(serve, allowed):
    server = serve([])

    for text in (server.say("/dev"), server.say("/dev help"), server.say("/Dev HELP")):
        assert text == dev_commands.HELP
    for name in ("whoami", "users", "values", "memory", "wipe-values", "reset"):
        assert f"/dev {name}" in dev_commands.HELP
    assert "/dev wipe-values all" in dev_commands.HELP


def test_an_unknown_command_says_so_and_shows_the_list(serve, allowed):
    text = serve([]).say("/dev frobnicate")

    assert text.startswith("Unknown developer command 'frobnicate'.")
    assert text.endswith(dev_commands.HELP)


def test_a_command_never_reaches_the_model_or_the_chat_log(serve, allowed):
    server = serve([reply("Hello.")], seeded=True)

    server.say("/dev values")
    server.say("/dev whoami")

    assert server.model.calls == []
    assert server.say("hi") == "Hello."
    assert "/dev" not in str(server.model.calls[0])


def test_values_and_memory_show_what_is_stored(serve, allowed):
    server = serve([], seeded=True)

    assert server.say("/dev values") == api.get_values(USER)
    memory = server.say("/dev memory")
    assert "== values.md ==\n## V1:" in memory
    assert f"== notes.md ==\n{dev_commands.EMPTY}" in memory
    assert serve([]).say("/dev values") == dev_commands.EMPTY


def test_whoami_names_the_user_the_thread_and_the_values(serve, allowed):
    text = serve([], seeded=True).say("/dev whoami")

    assert f"User: {USER}" in text
    assert f"Thread: {USER}:2026-11-14" in text
    assert "Storage: in memory" in text
    assert "Approved values: V1, V2, V3" in text
    assert "Card waiting in chat: no" in text


def test_users_lists_every_profile_with_its_values(serve, allowed):
    server = two_profiles(serve)
    api.force_delete_values(OTHER)

    text = server.say("/dev users")

    assert text.splitlines() == [
        "2 user profile(s):",
        f"{USER}: V1, V2, V3",
        f"{OTHER}: no values",
    ]


def test_wipe_values_deletes_only_the_callers_values(serve, allowed):
    server = two_profiles(serve)

    text = server.say("/dev wipe-values")

    assert text == (
        f"Force-deleted 3 value(s) from 1 user profile(s):\n{USER}: V1, V2, V3"
    )
    assert api.get_values(USER) == ""
    assert "## V1:" in api.get_values(OTHER)
    assert server.client.get("/status", headers=HEADERS).json() == {"has_values": False}


def test_wipe_values_all_deletes_the_values_of_every_profile(serve, allowed):
    server = two_profiles(serve)

    text = say_as(server, OTHER, "/dev wipe-values ALL")

    assert text.splitlines() == [
        "Force-deleted 6 value(s) from 2 user profile(s):",
        f"{USER}: V1, V2, V3",
        f"{OTHER}: V1, V2, V3",
    ]
    assert api.get_values(USER) == "" and api.get_values(OTHER) == ""
    assert server.say("/dev wipe-values all") == dev_commands.NOTHING_TO_WIPE
    assert server.model.calls == []


def test_wipe_values_refuses_an_argument_it_does_not_know(serve, allowed):
    server = serve([], seeded=True)

    assert server.say("/dev wipe-values everyone") == dev_commands.WIPE_USAGE
    assert "## V1:" in api.get_values(USER)


def test_a_wiped_values_id_is_never_given_to_a_new_value(serve, allowed):
    server = serve([], seeded=True)
    server.say("/dev wipe-values")
    card_id = server.add_value_card()

    api.answer_question(USER, card_id, {"action": "approve"})

    assert api.get_values(USER).startswith("## V4:")


def test_a_card_about_a_wiped_value_changes_nothing_when_approved(serve, allowed):
    removal = tool_call(
        "propose_value_removal", {"value_id": "V2", "evidence": "asked in chat"}
    )
    server = serve([removal, reply("Okay.")], seeded=True)
    card = server.say("please drop the news one")
    server.say("/dev wipe-values")

    text = server.say("approve")

    assert card.startswith("Remove V2")
    assert "changed since the card was made" in text
    assert api.get_values(USER) == ""
    assert api.pending_card(USER, f"{USER}:2026-11-14") is None


def test_wiping_keeps_everything_else_about_the_user(serve, allowed):
    server = serve([reply("Hello.")], seeded=True)
    server.say("hi")
    card_id = server.add_value_card()
    before = proposals.list_proposals(server.store, USER)

    server.say("/dev wipe-values")

    assert api.get_values(USER) == ""
    assert [question["id"] for question in api.get_questions(USER)] == [card_id]
    assert proposals.list_proposals(server.store, USER) == before
    assert records.user_chat_messages(server.store, USER)[-1] == "hi"


def test_reset_erases_the_caller_and_nobody_else(serve, allowed):
    server = two_profiles(serve, [reply("Hello.")])
    server.say("hi")

    text = server.say("/dev reset")

    assert text.startswith(f"Erased everything stored about {USER} (store: ")
    assert api.get_values(USER) == ""
    assert api.list_users() == [OTHER]
    assert "## V1:" in api.get_values(OTHER)


def test_reset_is_refused_while_a_recording_or_a_review_is_open(
    serve, allowed, monkeypatch
):
    server = serve([], seeded=True)
    recording = {
        "id": "s_live",
        "start": NOW,
        "last_reel_at": NOW - timedelta(minutes=1),
        "stopped": False,
    }
    monkeypatch.setattr(server.datasource, "list_sessions", lambda user_id: [recording])
    assert server.say("/dev reset") == dev_commands.RESET_WHILE_RECORDING

    monkeypatch.setattr(server.datasource, "list_sessions", lambda user_id: [])
    monkeypatch.setattr(background, "is_reviewing", lambda user_id: True)
    assert server.say("/dev reset") == dev_commands.RESET_WHILE_REVIEWING
    assert "## V1:" in api.get_values(USER)
