"""The router inside the teammate's real Judge server, running on its own fakes.

Nothing in `watcher_Agent/` is changed: the test builds their app with their test
settings (in-memory storage, a fake judging model) and adds the one include line.
Skipped when the Judge server or its requirements are not importable.
"""

from __future__ import annotations

import base64
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from guardian import api, config, storage
from guardian.tests.fakes import ScriptedChatModel, reply, tool_call
from guardian_router import router
from guardian_router.chat_adapter import CARD_ENDING

app_api = pytest.importorskip("judge.api.app_api")
harness = pytest.importorskip("tests.support.harness")
settings = pytest.importorskip("tests.support.settings")

USER = settings.DEMO_USER_ID
HEADERS = {"X-Demo-User-Id": USER}
QUOTE = "gym stuff is great, comparing bodies is not"
FITNESS = f"""## New: Fitness without body comparison (strength: high)
- Want more: none
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "{QUOTE}"
- Evidence: said in chat"""


@pytest.fixture
def judge_server(monkeypatch, tmp_path):
    """Their app, started, with the Guardian router included and no database."""
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    for name in (storage.DATABASE_URL_VAR, storage.GUARDIAN_DATABASE_URL_VAR):
        monkeypatch.delenv(name, raising=False)
    app = app_api.create_app(settings.make_settings())
    app.include_router(router)  # the one line
    with TestClient(app) as client:
        yield app, client


def upload_one_reel(app: Any, client: TestClient) -> dict[str, Any]:
    """Send one reel the way the iOS app does and wait for the Judge to store it."""
    payload = {
        "session_id": str(uuid4()),
        "reel_index": 1,
        "dwell_seconds": 6,
        "started_at": "2026-11-14T11:59:54Z",
        "frame_base64": base64.b64encode(harness.frame_jpeg(1, 1)).decode(),
        "transcript_text": "I want you to compare your body to this photo.",
        "ocr_text": ["Captions"],
        "stated_goals": "Low-Stress Feed",
        "sensitivity": "high",
    }
    uploaded = client.post("/agent1/ingest", json=payload, headers=HEADERS)
    if uploaded.status_code != 200:
        raise AssertionError(uploaded.text)
    client.portal.call(app.state.runtime.queue.join)
    return client.get("/timeline", headers=HEADERS).json()["data"]["items"][0]


def chat(client: TestClient, message: str, context: dict | None = None) -> str:
    body = {"message": message, "profile": {"stated_goals": ""}, "context": context}
    response = client.post("/agent2/chat", json=body, headers=HEADERS)
    if response.status_code != 200:
        raise AssertionError(response.text)
    return response.json()["text"]


def test_the_included_router_configures_itself_from_the_judge_servers_datasource(
    judge_server,
):
    _, client = judge_server

    assert client.get("/status", headers=HEADERS).json() == {"has_values": False}
    assert client.get("/questions", headers=HEADERS).json() == []
    assert client.get("/health").status_code == 200  # their routes still answer


def test_a_digest_from_the_timeline_reaches_the_judges_reel_through_the_chat(
    judge_server,
):
    app, client = judge_server
    reel = upload_one_reel(app, client)
    assert client.get("/status", headers=HEADERS).status_code == 200  # configures
    model = ScriptedChatModel(
        script=[
            tool_call(
                "propose_value_change",
                {"value_id": "new", "new_text": FITNESS, "evidence": "said in chat"},
            ),
            reply("Saved as V1."),
            tool_call("get_reels", {"reel_ids": [reel["id"]]}, "call_2"),
            reply("That reel touched V1."),
        ]
    )
    api.configure(
        InMemoryStore(),
        InMemorySaver(),
        app.state.guardian_datasource,
        chat_model=model,
    )
    digest = {
        "id": str(uuid4()).upper(),
        "total_reels_watched": 1,
        # The app sends the timeline's reel ID as an upper-case UUID.
        "flagged_reels": [{"id": reel["id"].upper(), "timestamp": reel["end"]}],
        "coach_hook_message": "1 of your last 1 judged reels conflicted.",
        "suggested_action": "Review.",
    }

    card = chat(client, f"Honestly, {QUOTE}.")
    saved = chat(client, "approve")
    explained = chat(client, "why was this flagged?", digest)

    assert card.endswith(CARD_ENDING)
    assert saved == "Saved as V1."
    assert explained == "That reel touched V1."
    asked = [m for m in model.calls[2] if isinstance(m, HumanMessage)][-1]
    assert f"reel_ids: {reel['id']}" in str(asked.content)  # lower case again
    looked_up = [m for m in model.calls[-1] if isinstance(m, ToolMessage)][-1]
    assert reel["id"] in str(looked_up.content)
    assert reel["description"] in str(looked_up.content)


def test_reflect_pending_runs_against_the_judges_sessions(judge_server):
    app, client = judge_server
    upload_one_reel(app, client)
    assert client.get("/status", headers=HEADERS).status_code == 200  # configures

    # The session is still open, so there is nothing to review yet, and no model runs.
    assert api.reflect_pending(USER) == 0


def test_the_router_is_already_included_by_the_judge_app_factory(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    for name in (storage.DATABASE_URL_VAR, storage.GUARDIAN_DATABASE_URL_VAR):
        monkeypatch.delenv(name, raising=False)

    with TestClient(app_api.create_app(settings.make_settings())) as client:
        assert client.get("/status", headers=HEADERS).json() == {"has_values": False}


def test_ending_a_session_starts_a_review_once_the_guardian_is_configured(
    judge_server, monkeypatch
):
    app, client = judge_server
    reel = upload_one_reel(app, client)
    reviewed: list[str] = []
    monkeypatch.setattr(api, "reflect_pending", lambda user: reviewed.append(user) or 1)
    end = {"session_id": reel["session_id"]}

    before = client.post("/session/end", json=end, headers=HEADERS)
    assert client.get("/status", headers=HEADERS).status_code == 200  # configures
    after = client.post("/session/end", json=end, headers=HEADERS)

    assert before.status_code == after.status_code == 200
    assert reviewed == [USER]  # only the call after the Guardian was configured
