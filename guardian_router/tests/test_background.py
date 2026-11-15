"""Tests for starting session reviews in the background."""

from __future__ import annotations

import pytest

from guardian import api
from guardian_router import background, review_sessions
from guardian_router.tests.conftest import HEADERS, USER


@pytest.fixture
def no_sleep(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(background, "_sleep", waits.append)
    return waits


def test_review_sessions_returns_how_many_sessions_were_reviewed(monkeypatch, no_sleep):
    monkeypatch.setattr(api, "reflect_pending", lambda user_id: 2)

    assert review_sessions(USER) == 2
    assert no_sleep == []


def test_review_sessions_waits_for_a_session_that_is_still_being_judged(
    monkeypatch, no_sleep
):
    results = iter([0, 0, 1])
    monkeypatch.setattr(api, "reflect_pending", lambda user_id: next(results))

    assert review_sessions(USER) == 1
    assert no_sleep == [background.RETRY_DELAY_SECONDS] * 2


def test_review_sessions_gives_up_quietly_when_nothing_is_ready(monkeypatch, no_sleep):
    calls: list[str] = []
    monkeypatch.setattr(
        api, "reflect_pending", lambda user_id: calls.append(user_id) or 0
    )

    assert review_sessions(USER) == 0
    assert len(calls) == background.SESSION_END_ATTEMPTS


def test_review_sessions_never_raises_and_does_not_log_secrets(
    monkeypatch, no_sleep, caplog
):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:hunter2@db.example.com/app")

    def boom(user_id: str) -> int:
        raise RuntimeError("cannot reach postgres://u:hunter2@db.example.com/app")

    monkeypatch.setattr(api, "reflect_pending", boom)

    assert review_sessions(USER) == 0
    assert "hunter2" not in caplog.text and "RuntimeError" in caplog.text


def test_only_one_review_per_user_runs_in_this_process(monkeypatch, no_sleep):
    seen: list[int] = []

    def nested(user_id: str) -> int:
        seen.append(review_sessions(user_id))  # a second call while the first runs
        return 1

    monkeypatch.setattr(api, "reflect_pending", nested)

    assert review_sessions(USER) == 1
    assert seen == [0]


def test_each_app_chat_message_starts_a_review_once_the_user_has_values(
    serve, monkeypatch
):
    reviewed: list[str] = []
    monkeypatch.setattr(
        api, "reflect_pending", lambda user_id: reviewed.append(user_id) or 0
    )
    server = serve(seeded=True)
    server.add_learned_card()

    server.say("hi")

    assert len(server.launched) == 1
    server.launched[0]()
    assert reviewed == [USER]


def test_listing_questions_starts_a_review(serve):
    server = serve(seeded=True)

    server.client.get("/questions", headers=HEADERS)

    assert len(server.launched) == 1


def test_no_review_is_started_before_the_user_has_any_values(serve):
    server = serve()
    server.add_learned_card()

    server.say("hi")
    server.client.get("/questions", headers=HEADERS)

    assert server.launched == []


def test_the_standalone_session_end_route_starts_a_review(serve):
    server = serve(seeded=True)

    response = server.client.post(
        "/session/end", json={"session_id": "abc"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert len(server.launched) == 1
