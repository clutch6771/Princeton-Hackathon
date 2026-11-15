"""Session reviews in the background.

A review (the Reflector) takes one to three minutes per session, so it never runs
inside a request. `review_sessions` is the function for the server's session-end
route to run as a background task; `start_review` is what the router itself uses on
chat and inbox requests, so reviews also happen without that hook.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from guardian import api, config

logger = logging.getLogger("guardian_router")

# When the app ends a session the Judge may still be judging its last reels, and the
# session only counts as finished once they are done. So the session-end review tries
# a few times before giving up; the next chat message tries again anyway.
SESSION_END_ATTEMPTS = 4
RETRY_DELAY_SECONDS = 15.0

Launcher = Callable[[Callable[[], Any]], Any]

_sleep = time.sleep
_in_flight: set[str] = set()
_guard = threading.Lock()


def reset() -> None:
    """Forget which reviews are running. For tests."""
    with _guard:
        _in_flight.clear()


def _review(user_id: str, attempts: int) -> int:
    """Run reviews for one user unless one is already running in this process."""
    with _guard:
        if user_id in _in_flight:
            return 0
        _in_flight.add(user_id)
    try:
        for attempt in range(attempts):
            reviewed = api.reflect_pending(user_id)
            if reviewed or attempt == attempts - 1:
                return reviewed
            _sleep(RETRY_DELAY_SECONDS)
        return 0
    except Exception as error:  # noqa: BLE001 - a background task must not raise
        detail = config.redact_secrets(f"{type(error).__name__}: {error}")
        logger.error("session review failed for %s: %s", user_id, detail)
        return 0
    finally:
        with _guard:
            _in_flight.discard(user_id)


def review_sessions(user_id: str) -> int:
    """Review this user's finished sessions; returns how many were reviewed.

    Run it as a background task when a session ends, from a worker thread (a plain
    `def` passed to FastAPI's BackgroundTasks runs in one). It blocks for minutes,
    never raises, and is safe to call at any time: extra calls return 0.
    """
    return _review(user_id, SESSION_END_ATTEMPTS)


def thread_launcher(task: Callable[[], Any]) -> None:
    threading.Thread(target=task, name="guardian-review", daemon=True).start()


def start_review(user_id: str, launch: Launcher = thread_launcher) -> None:
    """Start one review attempt in the background, if none is running for this user."""
    with _guard:
        if user_id in _in_flight:
            return
    launch(lambda: _review(user_id, 1))


def start_session_end_review(user_id: str, launch: Launcher = thread_launcher) -> None:
    """Start `review_sessions` in the background."""
    launch(lambda: review_sessions(user_id))
