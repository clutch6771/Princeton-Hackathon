"""One live run of the demo loop through HTTP, on seed data, against the real model.

    python -m guardian_router.live_run

Starts the standalone server on a local port and plays the app's part: onboarding
from the user's stated goal, approving the value card by text, a digest explanation,
a disagreement, a session review, an inbox card through chat, approving it, and a
check that the stored values changed. Uses API quota and takes several minutes.
Nothing is written outside this process.
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from typing import Any

import httpx
import uvicorn

from guardian_router.chat_adapter import CARD_ENDING
from guardian_router.standalone import create_app

PORT = 8765
BASE_URL = f"http://127.0.0.1:{PORT}"
HEADERS = {"X-Demo-User-Id": "demo-user"}
GOAL = "I like gym content but I want less body comparison stuff, it makes me feel bad"
ONBOARDING_ANSWERS = (
    "hi",
    "Before and after transformation videos and people rating physiques. "
    "Workout tutorials are fine.",
    "No exceptions I can think of. Please propose it.",
    "Yes, please write that up as a value.",
)
REQUEST_TIMEOUT_SECONDS = 90.0
REVIEW_TIMEOUT_SECONDS = 900.0
POLL_SECONDS = 10.0
MAX_INBOX_TURNS = 8


def digest(*reel_ids: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()).upper(),
        "total_reels_watched": 8,
        "flagged_reels": [
            {
                "id": reel_id,
                "timestamp": "2026-11-14T20:55:00Z",
                "topic": "flagged reel",
                "emotion_tag": "value conflict",
                "transcript_text": None,
                "dwell_seconds": 20,
            }
            for reel_id in reel_ids
        ],
        "user_goal_conflict": None,
        "coach_hook_message": f"{len(reel_ids)} of your last 8 judged reels conflicted with your approved values.",
        "suggested_action": "Review the reasons and tell your coach whether you agree.",
    }


class App:
    """The iOS app's side of the conversation."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self.context: dict[str, Any] | None = None  # the app's active digest

    def say(self, message: str) -> str:
        body = {
            "message": message,
            "profile": {
                "stated_goals": GOAL,
                "sensitivity": "medium",
                "checkpoint_threshold": 15,
                "session_id": "live-run",
            },
            "context": self.context,
        }
        started = time.monotonic()
        response = self._client.post("/agent2/chat", json=body)
        response.raise_for_status()
        message_back = response.json()
        took = time.monotonic() - started
        print(f"\nUSER: {message}\nGUARDIAN ({took:.1f}s): {message_back['text']}")
        if message_back["sender"] != "coachAgent" or not uuid.UUID(message_back["id"]):
            raise AssertionError(f"not a ChatMessage: {message_back}")
        return str(message_back["text"])

    def get(self, path: str) -> Any:
        response = self._client.get(path)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, body: dict[str, Any]) -> Any:
        response = self._client.post(path, json=body)
        response.raise_for_status()
        return response.json()


def check(results: list[tuple[str, bool]], name: str, passed: bool) -> None:
    results.append((name, passed))
    print(f"--- {'PASS' if passed else 'FAIL'}: {name}")


def run(app: App) -> list[tuple[str, bool]]:
    results: list[tuple[str, bool]] = []
    check(
        results,
        "server is up with no values",
        app.get("/status") == {"has_values": False},
    )

    # 1. Onboarding from the stated goal, then approve by text.
    text = ""
    for answer in ONBOARDING_ANSWERS:
        text = app.say(answer)
        if text.endswith(CARD_ENDING):
            break
    check(
        results, "onboarding produced a value card as text", text.endswith(CARD_ENDING)
    )
    app.say("Approve")
    first_values = app.get("/memory")["values.md"]
    print(f"\nVALUES AFTER ONBOARDING:\n{first_values}")
    check(results, "approve by text stored V1", first_values.startswith("## V1:"))
    check(results, "status is now true", app.get("/status") == {"has_values": True})

    # 2. A digest explanation, then a disagreement.
    app.context = digest("rl_014", "rl_015")
    explanation = app.say("Why were these flagged?")
    check(results, "digest explanation cites V1", "V1" in explanation)
    app.say("I disagree about the locker room one. That one was fine for me.")

    # 3. The session ends; wait for the review; inbox cards arrive through chat.
    print("\nPOST /session/end:", app.post("/session/end", {"session_id": "live-run"}))
    waited = 0.0
    while not app.get("/questions") and waited < REVIEW_TIMEOUT_SECONDS:
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    questions = app.get("/questions")
    print(f"\nINBOX after {waited:.0f}s: {[(q['id'], q['kind']) for q in questions]}")
    check(results, "the review put cards in the inbox", bool(questions))
    had_value_card = any(q["kind"] == "value_change" for q in questions)

    message = "hey, I'm back"
    for _ in range(MAX_INBOX_TURNS):
        text = app.say(message)
        message = "approve" if text.endswith(CARD_ENDING) else "ok, thanks"
        if not app.get("/questions") and not text.endswith(CARD_ENDING):
            break
    final_values = app.get("/memory")["values.md"]
    print(f"\nVALUES AT THE END:\n{final_values}")
    check(results, "the inbox is empty after the chat", app.get("/questions") == [])
    if had_value_card:
        check(
            results,
            "approving the inbox card changed the values",
            final_values != first_values,
        )
    else:
        print(
            "--- NOTE: the review queued no value card, so the values could not change"
        )
    return results


def main() -> int:
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(seeded=False), host="127.0.0.1", port=PORT, log_level="warning"
        )
    )
    threading.Thread(target=server.run, daemon=True).start()
    with httpx.Client(
        base_url=BASE_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS
    ) as client:
        for _ in range(50):
            try:
                client.get("/health").raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        results = run(App(client))
    server.should_exit = True
    failed = [name for name, passed in results if not passed]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
