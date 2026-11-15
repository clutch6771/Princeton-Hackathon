"""Fallback Gemini keys, for when a key runs out of quota in the middle of a demo.

GOOGLE_API_KEY_FALLBACKS in the environment (or the repository's .env) is an optional
comma-separated list of extra keys. When a Guardian or Reflector model call fails with
a quota or rate-limit error that the ordinary retries did not clear, this process
switches to the next key in the list and repeats the call once. It stays on the new
key for every later call. When no key is left the call fails as it always did.

No key is ever logged or printed: the only trace is "switched to fallback key N".
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, wrap_model_call

from guardian import config

logger = logging.getLogger("guardian")

FALLBACKS_VAR = config.API_KEY_FALLBACKS_VAR
fallback_keys = config.fallback_api_keys

# How a provider says a key is out of quota or over its rate limit.
_QUOTA_SIGNS = (
    "429",
    "resource_exhausted",
    "quota",
    "rate limit",
    "ratelimit",
    "too many requests",
    "per day limit",
)

_lock = threading.Lock()
_position = 0  # 0 is the primary key; N is the Nth fallback key


def position() -> int:
    """Which key this process is on: 0 for GOOGLE_API_KEY, N for the Nth fallback."""
    return _position


def reset() -> None:
    """Go back to the primary key. For tests."""
    global _position
    with _lock:
        _position = 0


def is_quota_error(error: BaseException | None) -> bool:
    """Whether an error, or one it was raised from, is a quota or rate-limit failure."""
    seen = 0
    while error is not None and seen < 5:
        text = f"{type(error).__name__} {error}".lower()
        if any(sign in text for sign in _QUOTA_SIGNS):
            return True
        error, seen = error.__cause__ or error.__context__, seen + 1
    return False


def move_past(used: int) -> bool:
    """Leave the key at position `used` for the next one. False when none is left.

    If another call has already moved on, nothing changes and the answer is True:
    two calls that fail together must not skip a key.
    """
    global _position
    with _lock:
        if _position != used:
            return True
        if _position >= len(fallback_keys()):
            return False
        _position += 1
        logger.warning("switched to fallback key %d", _position)
        return True


def middleware(rebuild: Callable[[str], Any]) -> AgentMiddleware:
    """Model-call middleware that applies the fallback keys to one agent.

    `rebuild` makes that agent's model again with a given key. Place this first in
    the agent's middleware list, outside the retry middleware, so that a key is
    left only after the ordinary retries have failed.
    """
    built: dict[int, Any] = {}

    def model_at(place: int) -> Any:
        if place not in built:
            built[place] = rebuild(fallback_keys()[place - 1])
        return built[place]

    @wrap_model_call(name="KeyFallbackMiddleware")
    def with_key_fallback(request: ModelRequest, handler: Callable[..., Any]) -> Any:
        used = position()
        active = request if used == 0 else request.override(model=model_at(used))
        try:
            return handler(active)
        except Exception as error:  # noqa: BLE001 - re-raised unless it is about quota
            if not is_quota_error(error) or not move_past(used):
                raise
            return handler(request.override(model=model_at(position())))

    return with_key_fallback
