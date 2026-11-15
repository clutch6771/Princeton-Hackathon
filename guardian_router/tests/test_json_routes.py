"""Tests for the JSON routes: each is one Guardian function, its result unchanged."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from guardian import api, config, storage
from guardian.datasource import FakeDataSource
from guardian.tests.fakes import reply, tool_call
from guardian_router import router
from guardian_router.tests.conftest import HEADERS, HUSTLE, NOW, USER

QUOTE = "gym stuff is great, comparing bodies is not"
CARD = f"""## New: Fitness without body comparison (strength: high)
- Want more: none
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "{QUOTE}"
- Evidence: said in chat"""


def propose_fitness():
    args = {"value_id": "new", "new_text": CARD, "evidence": "said in chat"}
    return tool_call("propose_value_change", args)


def test_chat_returns_the_guardian_response_unchanged(serve):
    server = serve([reply("What would you like to see less of?")])

    response = server.client.post("/chat", json={"message": "hi", "context": None})

    assert response.status_code == 200
    assert response.json() == {
        "reply": "What would you like to see less of?",
        "pending": None,
        "error": None,
    }


def test_card_then_resume_then_status_and_memory(serve):
    server = serve([propose_fitness(), reply("Saved as V1.")])
    client = server.client
    assert client.get("/status").json() == {"has_values": False}

    card = client.post("/chat", json={"message": f"Honestly, {QUOTE}."}).json()
    resumed = client.post("/chat/resume", json={"action": "approve"}).json()

    assert card["pending"]["value_change"]["new_text"] == CARD
    assert resumed == {"reply": "Saved as V1.", "pending": None, "error": None}
    assert client.get("/status").json() == {"has_values": True}
    memory = client.get("/memory").json()
    assert set(memory) == {"values.md", "brief.md", "hypotheses.md", "notes.md"}
    assert memory["values.md"].startswith("## V1: Fitness without body comparison")


def test_an_error_comes_back_in_the_body_with_status_200(serve):
    server = serve()

    response = server.client.post("/chat/resume", json={"action": "approve"})

    assert response.status_code == 200
    assert response.json()["error"] == "No value card is waiting in this conversation."


def test_chat_context_is_passed_through(serve):
    server = serve([reply("Let me look.")], seeded=True)
    context = {"entry": "drift_notification", "reel_ids": ["rl_014", "rl_015"]}

    server.client.post("/chat", json={"message": "why?", "context": context})

    sent = [
        str(m.content) for m in server.model.calls[0] if isinstance(m, HumanMessage)
    ][-1]
    assert "entry: drift_notification" in sent and "rl_014, rl_015" in sent


def test_a_malformed_body_is_rejected(serve):
    server = serve()

    assert server.client.post("/chat", json={"message": 5}).status_code == 422
    assert server.client.post("/chat/resume", json={}).status_code == 422
    assert server.client.post("/agent2/chat", json={"message": []}).status_code == 422


def test_the_user_comes_from_the_header_and_defaults_to_the_demo_user(serve):
    server = serve([propose_fitness(), reply("Saved as V1.")])
    client = server.client
    client.post("/chat", json={"message": f"Honestly, {QUOTE}."})  # no header
    client.post("/chat/resume", json={"action": "approve"}, headers=HEADERS)

    other = {"X-Demo-User-Id": "someone-else"}
    assert client.get("/status", headers=HEADERS).json() == {"has_values": True}
    assert client.get("/status").json() == {"has_values": True}
    assert client.get("/status", headers=other).json() == {"has_values": False}
    assert client.get("/memory", headers=other).json()["values.md"] == ""


def test_an_invalid_user_id_is_an_error_not_a_crash(serve):
    server = serve([reply("unused")])
    bad = {"X-Demo-User-Id": "not a valid id!"}

    response = server.client.post("/chat", json={"message": "hi"}, headers=bad)

    assert response.status_code == 200
    assert response.json()["error"] == "The user ID is missing or not valid."
    assert server.model.calls == []


def test_questions_lists_the_inbox_oldest_first_with_iso_times(serve):
    server = serve(seeded=True)
    value_card = server.add_value_card()
    learned_card = server.add_learned_card(NOW.replace(hour=22))

    questions = server.client.get("/questions", headers=HEADERS).json()

    assert [q["id"] for q in questions] == [value_card, learned_card]
    assert questions[0]["kind"] == "value_change"
    assert questions[0]["value_change"]["new_text"] == HUSTLE
    assert questions[0]["created"] == "2026-11-14T21:00:00Z"
    assert questions[1]["kind"] == "learned" and questions[1]["value_change"] is None


def test_an_empty_inbox_is_an_empty_list(serve):
    assert serve().client.get("/questions").json() == []


def test_answering_a_value_card_changes_the_values(serve):
    server = serve(seeded=True)
    card = server.add_value_card()

    answered = server.client.post(
        f"/questions/{card}/answer", json={"action": "approve"}
    )

    assert answered.json() == {"ok": True, "error": None}
    assert "Less get-rich-quick content" in api.get_values(USER)
    assert server.client.get("/questions").json() == []


def test_an_edit_is_passed_to_the_guardian_and_an_invalid_one_keeps_the_card(serve):
    server = serve(seeded=True)
    card = server.add_value_card()

    answered = server.client.post(
        f"/questions/{card}/answer", json={"action": "edit", "edited_text": "nonsense"}
    ).json()

    assert answered["ok"] is False and "6 lines" in answered["error"]
    assert [q["id"] for q in server.client.get("/questions").json()] == [card]


def test_a_learned_card_is_dismissed_and_an_unknown_card_is_refused(serve):
    server = serve(seeded=True)
    card = server.add_learned_card()
    client = server.client

    wrong = client.post(f"/questions/{card}/answer", json={"action": "approve"}).json()
    dismissed = client.post(f"/questions/{card}/answer", json={"action": "dismiss"})
    unknown = client.post("/questions/q_nope/answer", json={"action": "dismiss"})

    assert wrong["ok"] is False
    assert dismissed.json() == {"ok": True, "error": None}
    assert unknown.json() == {"ok": False, "error": "This card does not exist."}


def test_health_answers_for_the_standalone_app(serve):
    assert serve().client.get("/health").json() == {"status": "ok"}


# --- Included in another server ---------------------------------------------------------


def test_the_router_included_in_another_app_uses_that_apps_datasource(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    for name in (storage.DATABASE_URL_VAR, storage.GUARDIAN_DATABASE_URL_VAR):
        monkeypatch.delenv(name, raising=False)
    host = FastAPI()
    host.include_router(router)  # the one line
    host.state.guardian_datasource = FakeDataSource.from_seed(USER, clock=lambda: NOW)
    client = TestClient(host)

    assert client.get("/status", headers=HEADERS).json() == {"has_values": False}
    assert client.get("/questions", headers=HEADERS).json() == []
    assert set(client.get("/memory", headers=HEADERS).json()) == {
        "values.md",
        "brief.md",
        "hypotheses.md",
        "notes.md",
    }


def test_the_router_reports_a_missing_datasource_instead_of_guessing():
    host = FastAPI()
    host.include_router(router)

    response = TestClient(host).get("/status")

    assert response.status_code == 503
    assert "guardian_datasource" in response.json()["detail"]
