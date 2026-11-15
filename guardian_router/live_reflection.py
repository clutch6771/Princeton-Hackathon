"""Live runs of the reflection behaviours through POST /agent2/chat, on seed data.

    python -m guardian_router.live_reflection          # all three
    python -m guardian_router.live_reflection A B      # only the ones named
    LIVE_PAUSE_SECONDS=8 python -m guardian_router.live_reflection   # paced like a person

Three short conversations against the real model, each on a fresh standalone server:

  A. Onboarding: the Guardian speaks first, interviews about what the user wants
     less of, proposes a value, and asks whether there is anything else.
  B. With three approved values: opening about a value, a drift explanation, a
     reflection question, a note saved from the answer, a refinement after a
     disagreement, and reopening the chat without a new model call.
  C. A session review: the Reflector's cards arrive through the chat.

It prints every reply. Uses API quota and takes several minutes. Nothing is stored
outside this process.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from typing import Any

import httpx
import uvicorn

from guardian_router.chat_adapter import CARD_ENDING
from guardian_router.standalone import create_app

FIRST_PORT = 8771
HEADERS = {"X-Demo-User-Id": "demo-user"}
APP_DEFAULT_GOAL = "Low-Stress Feed"
REQUEST_TIMEOUT_SECONDS = 120.0
REVIEW_TIMEOUT_SECONDS = 900.0
POLL_SECONDS = 10.0
MAX_ONBOARDING_TURNS = 8
# Seconds to wait before each message, to pace the run like a person typing. The
# chat rate limiter refills over time, so a run with no pauses is the slowest case.
PAUSE_SECONDS = float(os.environ.get("LIVE_PAUSE_SECONDS", "0"))

# The simulated user's answers, in the order the interview is expected to need them.
ONBOARDING_ANSWERS = (
    "Mostly gym videos where people compare bodies. Before and after "
    "transformations, physique ratings, that kind of thing.",
    "Last night there was one where a guy rated five physiques from his comments. "
    "I watched the whole thing.",
    "Form tutorials and training plans are fine, I like those.",
    "I end up comparing myself and feel worse about my own progress. I mostly "
    "scroll late at night when I'm stressed about exams.",
    "That's everything. Please write it up.",
    "Yes, go ahead.",
)


def no_background(task: Any) -> None:
    """Drop background reviews, so a conversation is not interrupted by cards."""


def serve(port: int, **options: Any) -> httpx.Client:
    """Start a fresh standalone server and return a client for it."""
    app = create_app(**options)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    for _ in range(50):
        try:
            client.get("/health").raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    return client


def digest(*reel_ids: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()).upper(),
        "total_reels_watched": 8,
        "flagged_reels": [{"id": reel_id} for reel_id in reel_ids],
        "coach_hook_message": f"{len(reel_ids)} of your last 8 judged reels conflicted with your approved values.",
        "suggested_action": "Review the reasons and tell your coach whether you agree.",
    }


def say(
    client: httpx.Client,
    message: str,
    context: dict[str, Any] | None = None,
    goals: str = APP_DEFAULT_GOAL,
) -> str:
    body = {"message": message, "profile": {"stated_goals": goals}, "context": context}
    time.sleep(PAUSE_SECONDS)
    started = time.monotonic()
    response = client.post("/agent2/chat", json=body)
    response.raise_for_status()
    text = str(response.json()["text"])
    shown = message if message else "(opens the chat, empty message)"
    took = time.monotonic() - started
    print(f"\nUSER: {shown}\nGUARDIAN ({took:.1f}s): {text}")
    return text


def memory(client: httpx.Client, name: str) -> str:
    return str(client.get("/memory").json()[name]) or "(empty)"


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def onboarding(port: int) -> None:
    section("A. Onboarding: the Guardian speaks first and interviews about 'want less'")
    client = serve(port, seeded=False, launch=no_background)
    text = say(client, "")
    answers = iter(ONBOARDING_ANSWERS)
    for _ in range(MAX_ONBOARDING_TURNS):
        if text.endswith(CARD_ENDING):
            break
        text = say(client, next(answers, "Please propose the value now."))
    if text.endswith(CARD_ENDING):
        say(client, "approve")
        say(client, "No, that's all for now. Thanks.")
    print(f"\nvalues.md after onboarding:\n{memory(client, 'values.md')}")
    print(f"\nnotes.md after onboarding:\n{memory(client, 'notes.md')}")


def reflection(port: int) -> None:
    section("B. With approved values: opening, explanation, reflection, refinement")
    client = serve(port, seeded=True, launch=no_background)
    print(f"\nvalues.md at the start:\n{memory(client, 'values.md')}")

    print("\n--- B1. The user opens the chat with nothing flagged")
    opener = say(client, "")
    say(client, "It still fits. Nothing to change there.")

    print("\n--- B2. A drift notification, then the user's reply, then reflection")
    flagged = digest("rl_014", "rl_015")
    say(client, "Why were these flagged?", flagged)
    say(client, "Yes, that matches how I see it.", flagged)
    say(
        client,
        "It was hard to scroll past because I kept wondering how I would be rated.",
        flagged,
    )
    print(f"\nnotes.md after the reflection answer:\n{memory(client, 'notes.md')}")

    print("\n--- B3. A disagreement that leads to a refinement")
    text = say(
        client,
        "One more thing. A knee surgery recovery video, rl_001, was flagged too. "
        "I disagree: that is rehab, not comparison. Rehab progress is fine with me.",
        flagged,
    )
    for follow_up in (
        "Yes, please add that as an exception.",
        "Yes, propose that change.",
    ):
        if text.endswith(CARD_ENDING):
            break
        text = say(client, follow_up, flagged)
    if text.endswith(CARD_ENDING):
        say(client, "approve", flagged)
    print(f"\nvalues.md after the refinement:\n{memory(client, 'values.md')}")

    print("\n--- B4. Reopening the chat returns the last message without a model call")
    again = say(client, "", flagged)
    print(f"\n(first opener in this conversation was: {opener[:70]!r})")
    print(f"(reopened reply came back in well under a second: {again[:70]!r})")


def review(port: int) -> None:
    section("C. A session review: the Reflector's cards arrive through the chat")
    client = serve(port, seeded=True)
    print(f"\nvalues.md before the review:\n{memory(client, 'values.md')}")
    print("\nPOST /session/end:", client.post("/session/end", json={}).json())
    waited = 0.0
    while not client.get("/questions").json() and waited < REVIEW_TIMEOUT_SECONDS:
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    cards = client.get("/questions").json()
    print(f"\ninbox after {waited:.0f}s: {[(c['id'], c['kind']) for c in cards]}")
    message = "hey, I'm back"
    for _ in range(6):
        text = say(client, message)
        if text.endswith(CARD_ENDING):
            message = "approve"
        elif text.rstrip().endswith("?") and "learned" in text:
            message = "The rehab ones feel like progress, not showing off."
        else:
            break
    print(f"\nvalues.md after the review:\n{memory(client, 'values.md')}")


def main() -> int:
    runs = {"A": onboarding, "B": reflection, "C": review}
    ports = {name: FIRST_PORT + offset for offset, name in enumerate(runs)}
    for name in sys.argv[1:] or list(runs):
        runs[name](ports[name])
    return 0


if __name__ == "__main__":
    sys.exit(main())
