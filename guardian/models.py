"""Builds the chat models from the environment, each behind a rate limiter."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter

from guardian import config

REQUESTS_PER_MINUTE_VAR = "GUARDIAN_REQUESTS_PER_MINUTE"
DEFAULT_REQUESTS_PER_MINUTE = 12.0  # chat; kept below the free-tier limit
# The Reflector runs in the background for minutes at a time. It has its own, slower
# limiter so that a chat reply never waits behind a review (the app gives up on a
# chat request after 30 seconds). Defaults to half the chat rate.
REFLECTOR_REQUESTS_PER_MINUTE_VAR = "GUARDIAN_REFLECTOR_REQUESTS_PER_MINUTE"
REFLECTOR_SHARE = 0.5
REQUEST_TIMEOUT_SECONDS = 60
PROVIDER_MAX_RETRIES = 2
SECONDS_PER_MINUTE = 60.0


def _rate_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError as error:
        raise config.ConfigError(f"{name} must be a number, got {raw!r}") from error
    if value <= 0:
        raise config.ConfigError(f"{name} must be greater than zero")
    return value


def requests_per_minute() -> float:
    """Model calls per minute allowed for chat."""
    return _rate_from_env(REQUESTS_PER_MINUTE_VAR, DEFAULT_REQUESTS_PER_MINUTE)


def background_requests_per_minute() -> float:
    """Model calls per minute allowed for the Reflector."""
    return _rate_from_env(
        REFLECTOR_REQUESTS_PER_MINUTE_VAR, requests_per_minute() * REFLECTOR_SHARE
    )


def _limiter(per_minute: float) -> InMemoryRateLimiter:
    limiter = InMemoryRateLimiter(
        requests_per_second=per_minute / SECONDS_PER_MINUTE,
        check_every_n_seconds=0.1,
        max_bucket_size=max(1.0, per_minute / 2),
    )
    # LangChain starts the bucket empty, so the first call waits a whole interval
    # (5 seconds at 12 a minute). Start it full: a burst is what the bucket allows.
    limiter.available_tokens = limiter.max_bucket_size
    return limiter


@lru_cache(maxsize=1)
def shared_rate_limiter() -> InMemoryRateLimiter:
    """The limiter for chat model calls."""
    return _limiter(requests_per_minute())


@lru_cache(maxsize=1)
def background_rate_limiter() -> InMemoryRateLimiter:
    """The limiter for the Reflector's model calls."""
    return _limiter(background_requests_per_minute())


def build_model(name: str, background: bool = False, api_key: str | None = None) -> Any:
    """Create a chat model such as "google_genai:gemini-3.5-flash-lite".

    `background=True` is for the Reflector: it takes the slower background limiter.
    `api_key` builds the model with that key instead of GOOGLE_API_KEY; it is how
    a fallback key is put to use (see guardian/key_fallback.py).
    """
    config.load_env()
    config.require_api_key()
    limiter = background_rate_limiter() if background else shared_rate_limiter()
    with_key: dict[str, Any] = {"api_key": api_key} if api_key else {}
    return init_chat_model(
        name,
        temperature=0,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=PROVIDER_MAX_RETRIES,
        rate_limiter=limiter,
        **with_key,
    )
