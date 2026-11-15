"""Configuration: the repository-root .env file and model names.

Secrets are read into the process environment and nowhere else. Nothing in this
module logs, prints or returns the value of the API key.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

API_KEY_VAR = "GOOGLE_API_KEY"
API_KEY_FALLBACKS_VAR = "GOOGLE_API_KEY_FALLBACKS"  # optional, comma-separated
CHAT_MODEL_VAR = "GUARDIAN_CHAT_MODEL"
REFLECTOR_MODEL_VAR = "GUARDIAN_REFLECTOR_MODEL"

# Chosen in Step 0 (Nov 14): the only Flash-family model that answered every probe on
# the free tier. gemini-3.5-flash works but returned 503 "high demand" about half the
# time; 3.6 to 3.8 were unavailable. Override with the environment variables above.
DEFAULT_CHAT_MODEL = "google_genai:gemini-3.5-flash-lite"
DEFAULT_REFLECTOR_MODEL = "google_genai:gemini-3.5-flash-lite"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

REDACTED = "***"
SECRET_VARS = (
    API_KEY_VAR,
    "DATABASE_URL",
    "GUARDIAN_DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "TEST_DATABASE_URL",
    # The Judge's provider keys: it runs in the same process and shares the .env.
    "XAI_API_KEY",
    "GEMINI_API_KEY",
)


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


def load_env(env_path: Path | None = None) -> bool:
    """Load variables from the .env file into the environment.

    Variables that are already set are left as they are. Returns whether the
    file was found.
    """
    path = env_path or DEFAULT_ENV_PATH
    if not path.is_file():
        return False
    load_dotenv(path, override=False)
    return True


def has_api_key() -> bool:
    return bool(os.environ.get(API_KEY_VAR, "").strip())


def require_api_key() -> None:
    """Raise ConfigError if the model API key is not available."""
    if not has_api_key():
        raise ConfigError(
            f"{API_KEY_VAR} is not set. Add it to {DEFAULT_ENV_PATH.name} in the "
            "repository root or export it in the environment."
        )


def chat_model_name() -> str:
    return os.environ.get(CHAT_MODEL_VAR, "").strip() or DEFAULT_CHAT_MODEL


def reflector_model_name() -> str:
    return os.environ.get(REFLECTOR_MODEL_VAR, "").strip() or DEFAULT_REFLECTOR_MODEL


def fallback_api_keys() -> list[str]:
    """Extra Gemini keys to switch to when the current one runs out of quota."""
    load_env()
    listed = os.environ.get(API_KEY_FALLBACKS_VAR, "")
    return [key.strip() for key in listed.split(",") if key.strip()]


def redact_secrets(text: str) -> str:
    """Remove every API key and any database connection string from text about to be logged."""
    for key in fallback_api_keys():
        text = text.replace(key, REDACTED)
    for name in SECRET_VARS:
        secret = os.environ.get(name, "").strip()
        if not secret:
            continue
        text = text.replace(secret, REDACTED)
        password = urlsplit(secret).password if "://" in secret else None
        if password:
            text = text.replace(password, REDACTED)
    return text
