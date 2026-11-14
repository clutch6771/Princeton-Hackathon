"""Builds the chat models from the environment, behind one shared rate limiter."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter

from guardian import config

REQUESTS_PER_MINUTE_VAR = "GUARDIAN_REQUESTS_PER_MINUTE"
DEFAULT_REQUESTS_PER_MINUTE = (
    12.0  # kept below the free-tier limit; both agents share it
)
REQUEST_TIMEOUT_SECONDS = 60
PROVIDER_MAX_RETRIES = 2
SECONDS_PER_MINUTE = 60.0


def requests_per_minute() -> float:
    raw = os.environ.get(REQUESTS_PER_MINUTE_VAR, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_REQUESTS_PER_MINUTE
    except ValueError as error:
        raise config.ConfigError(
            f"{REQUESTS_PER_MINUTE_VAR} must be a number, got {raw!r}"
        ) from error
    if value <= 0:
        raise config.ConfigError(f"{REQUESTS_PER_MINUTE_VAR} must be greater than zero")
    return value


@lru_cache(maxsize=1)
def shared_rate_limiter() -> InMemoryRateLimiter:
    """One limiter for every model call the package makes with this key."""
    per_minute = requests_per_minute()
    return InMemoryRateLimiter(
        requests_per_second=per_minute / SECONDS_PER_MINUTE,
        check_every_n_seconds=0.1,
        max_bucket_size=max(1.0, per_minute / 2),
    )


def build_model(name: str) -> Any:
    """Create a chat model such as "google_genai:gemini-3.5-flash-lite"."""
    config.load_env()
    config.require_api_key()
    return init_chat_model(
        name,
        temperature=0,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=PROVIDER_MAX_RETRIES,
        rate_limiter=shared_rate_limiter(),
    )
