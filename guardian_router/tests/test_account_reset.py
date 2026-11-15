"""Tests for POST /account/reset: erase everything stored about the header's user."""

from __future__ import annotations

from datetime import timedelta

import pytest

from guardian import api
from guardian.memory import notes, records
from guardian.seed.install import install_initial_values
from guardian.tests.fakes import reply
from guardian_router import background, reset_demo, routes
from guardian_router.tests.conftest import HEADERS, NOW, USER

DELETE = {"confirm": "DELETE"}
OTHER = {"X-Demo-User-Id": "someone-else"}


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setenv(routes.ALLOW_RESET_VAR, "1")


def reset(server, body=None, headers=HEADERS):
    return server.client.post(
        "/account/reset", json=DELETE if body is None else body, headers=headers
    )


def test_reset_is_disabled_unless_the_server_allows_it(serve, monkeypatch):
    monkeypatch.delenv(routes.ALLOW_RESET_VAR, raising=False)
    server = serve(seeded=True)

    response = reset(server)

    assert response.status_code == 403
    assert routes.ALLOW_RESET_VAR in response.json()["detail"]
    assert api.has_values(USER)


@pytest.mark.parametrize(
    "body", [{}, {"confirm": "delete"}, {"confirm": "yes"}, {"confirm": ""}]
)
def test_reset_needs_the_exact_confirmation(serve, allowed, body):
    server = serve(seeded=True)

    response = reset(server, body)

    assert response.status_code in (400, 422)
    assert api.has_values(USER)


def test_reset_needs_the_user_header(serve, allowed):
    server = serve(seeded=True)

    response = server.client.post("/account/reset", json=DELETE)

    assert response.status_code == 400 and "X-Demo-User-Id" in response.json()["detail"]
    assert api.has_values(USER)


def test_reset_erases_that_users_records_and_returns_counts(serve, allowed):
    server = serve([reply("Hello.")], seeded=True)
    server.say("I scroll when I'm stressed before exams")
    notes.save_note(
        server.store,
        server.datasource,
        USER,
        "Scrolls when stressed",
        "I scroll when I'm stressed before exams",
        NOW,
    )
    server.add_learned_card()

    response = reset(server)

    body = response.json()
    assert response.status_code == 200 and body["user"] == USER
    assert body["deleted"]["store"] > 0 and body["deleted"]["threads"] >= 1
    assert server.client.get("/status", headers=HEADERS).json() == {"has_values": False}
    memory = server.client.get("/memory", headers=HEADERS).json()
    assert set(memory.values()) == {""}
    assert server.client.get("/questions", headers=HEADERS).json() == []
    assert records.user_chat_messages(server.store, USER) == []


def test_reset_leaves_other_users_alone(serve, allowed):
    server = serve(seeded=True)
    install_initial_values(server.store, server.datasource, "someone-else", NOW)

    assert reset(server).status_code == 200

    assert server.client.get("/status", headers=OTHER).json() == {"has_values": True}


def test_reset_clears_this_processes_chat_state_for_the_user(serve, allowed):
    server = serve([reply("Hello.")], seeded=True)
    server.add_value_card()
    card = server.say("hi")  # an inbox card is now on screen, waiting for a decision

    reset(server)

    assert card.endswith("Reply approve or reject.")
    # No values after the reset, so "approve" is an ordinary message, not a decision.
    assert server.say("approve") == "Hello."


def test_reset_is_refused_while_a_recording_session_is_open(
    serve, allowed, monkeypatch
):
    server = serve(seeded=True)
    recording = {
        "id": "s_live",
        "start": NOW,
        "last_reel_at": NOW - timedelta(minutes=1),
        "stopped": False,
    }
    monkeypatch.setattr(server.datasource, "list_sessions", lambda user_id: [recording])

    response = reset(server)

    assert response.status_code == 409 and "recording" in response.json()["detail"]
    assert api.has_values(USER)


def test_reset_is_refused_while_a_review_is_running(serve, allowed, monkeypatch):
    server = serve(seeded=True)
    monkeypatch.setattr(background, "is_reviewing", lambda user_id: True)

    response = reset(server)

    assert response.status_code == 409 and "review" in response.json()["detail"]
    assert api.has_values(USER)


def test_on_a_database_the_route_uses_the_reset_command_for_that_user(
    serve, allowed, monkeypatch
):
    server = serve(seeded=True)
    runtime = server.client.app.state.guardian_runtime
    server.client.app.state.guardian_runtime = routes.Runtime(
        clock=runtime.clock,
        launch=runtime.launch,
        adapter=runtime.adapter,
        database_url="postgresql://not-a-real-url",
    )
    calls = []

    def fake_reset(conn_string, user_id, confirmed):
        calls.append((conn_string, user_id, confirmed))
        return {"store": 12, "checkpoints": 3, "judge.sessions": 1}

    monkeypatch.setattr(reset_demo, "reset", fake_reset)

    response = reset(server)

    assert calls == [("postgresql://not-a-real-url", USER, True)]
    assert response.json() == {
        "user": USER,
        "deleted": {"store": 12, "checkpoints": 3, "judge.sessions": 1},
    }
    assert "not-a-real-url" not in response.text


def test_an_invalid_user_id_is_refused(serve, allowed):
    server = serve(seeded=True)

    response = reset(server, headers={"X-Demo-User-Id": "not valid!"})

    assert response.status_code == 400 and api.has_values(USER)
