"""Tests for the temporary dev server. A scripted model stands in for Gemini."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from dev_server.app import DEMO_USER, create_app
from guardian.tests.fakes import ScriptedChatModel, reply, tool_call

NOW = datetime(2026, 11, 14, 21, 0, tzinfo=UTC)
QUOTE = "gym stuff is great, comparing bodies is not"
CARD = f"""## New: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "{QUOTE}"
- Evidence: said in chat"""


def client_with(script: list[AIMessage], seeded: bool = False):
    model = ScriptedChatModel(script=script)
    app = create_app(chat_model=model, clock=lambda: NOW, seeded=seeded)
    return TestClient(app), model


def propose_fitness() -> AIMessage:
    args = {"value_id": "new", "new_text": CARD, "evidence": "said in chat"}
    return tool_call("propose_value_change", args)


def test_chat_returns_the_guardian_response_unchanged():
    client, _ = client_with([reply("What would you like to see less of?")])

    response = client.post("/chat", json={"message": "hi", "context": None})

    assert response.status_code == 200
    assert response.json() == {
        "reply": "What would you like to see less of?",
        "pending": None,
        "error": None,
    }


def test_card_then_approve_then_status_and_memory():
    client, _ = client_with([propose_fitness(), reply("Saved as V1.")])
    assert client.get("/status").json() == {"has_values": False}

    card = client.post("/chat", json={"message": f"Honestly, {QUOTE}."}).json()
    resumed = client.post(
        "/chat/resume", json={"action": "approve", "edited_text": None}
    ).json()

    assert card["pending"]["value_change"]["new_text"] == CARD
    assert resumed == {"reply": "Saved as V1.", "pending": None, "error": None}
    assert client.get("/status").json() == {"has_values": True}
    memory = client.get("/memory").json()
    assert set(memory) == {"values.md", "brief.md", "hypotheses.md", "notes.md"}
    assert memory["values.md"].startswith("## V1: Fitness without body comparison")


def test_an_error_comes_back_in_the_body_with_status_200():
    client, _ = client_with([reply("unused")])

    response = client.post("/chat/resume", json={"action": "approve"})

    assert response.status_code == 200
    assert response.json()["error"] == "No value card is waiting in this conversation."


def test_context_is_passed_through_to_the_guardian():
    client, model = client_with([reply("Let me look.")], seeded=True)
    context = {"entry": "drift_notification", "reel_ids": ["rl_014", "rl_015"]}

    client.post("/chat", json={"message": "why?", "context": context})

    sent = [str(m.content) for m in model.calls[0] if isinstance(m, HumanMessage)][-1]
    assert "entry: drift_notification" in sent
    assert "reel_ids: rl_014, rl_015" in sent


def test_seeded_server_starts_with_the_personas_three_values():
    client, _ = client_with([reply("unused")], seeded=True)

    assert client.get("/status").json() == {"has_values": True}
    assert client.get("/memory").json()["values.md"].count("## V") == 3


@pytest.mark.parametrize(
    ("path", "body"),
    [("/chat", {}), ("/chat", {"message": 5}), ("/chat/resume", {})],
)
def test_a_malformed_request_body_is_rejected(path: str, body: dict):
    client, model = client_with([reply("unused")])

    response = client.post(path, json=body)

    assert response.status_code == 422
    assert model.calls == []


def test_the_server_serves_one_fixed_demo_user():
    client, _ = client_with([reply("hello")])

    client.post("/chat", json={"message": "hi"}, headers={"X-User-Id": "someone"})

    assert DEMO_USER == "demo-user"
    assert client.get("/memory").status_code == 200
