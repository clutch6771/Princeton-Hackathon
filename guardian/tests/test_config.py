"""Tests for guardian.config: loading the root .env without exposing secrets."""

import pytest

from guardian import config

FAKE_KEY = "test-key-not-real-0123456789"


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("GOOGLE_API_KEY", "GUARDIAN_CHAT_MODEL", "GUARDIAN_REFLECTOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_load_env_reads_api_key_from_env_file(tmp_path, clean_env):
    env_file = tmp_path / ".env"
    env_file.write_text(f"GOOGLE_API_KEY={FAKE_KEY}\n")

    found = config.load_env(env_file)

    assert found is True
    assert config.has_api_key() is True


def test_load_env_does_not_override_existing_environment(tmp_path, clean_env):
    clean_env.setenv("GOOGLE_API_KEY", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text(f"GOOGLE_API_KEY={FAKE_KEY}\n")

    config.load_env(env_file)

    import os
    assert os.environ["GOOGLE_API_KEY"] == "already-set"


def test_load_env_returns_false_when_file_is_missing(tmp_path, clean_env):
    found = config.load_env(tmp_path / "missing.env")

    assert found is False
    assert config.has_api_key() is False


def test_default_env_path_is_the_repository_root():
    assert config.DEFAULT_ENV_PATH == config.PROJECT_ROOT / ".env"
    assert (config.PROJECT_ROOT / "guardian" / "config.py").exists()


def test_require_api_key_raises_clear_error_when_missing(clean_env):
    with pytest.raises(config.ConfigError) as excinfo:
        config.require_api_key()

    assert "GOOGLE_API_KEY" in str(excinfo.value)


def test_require_api_key_passes_when_set(clean_env):
    clean_env.setenv("GOOGLE_API_KEY", FAKE_KEY)

    config.require_api_key()


def test_blank_api_key_counts_as_missing(clean_env):
    clean_env.setenv("GOOGLE_API_KEY", "   ")

    assert config.has_api_key() is False


def test_model_names_fall_back_to_defaults(clean_env):
    assert config.chat_model_name() == config.DEFAULT_CHAT_MODEL
    assert config.reflector_model_name() == config.DEFAULT_REFLECTOR_MODEL


def test_model_names_come_from_environment_when_set(clean_env):
    clean_env.setenv("GUARDIAN_CHAT_MODEL", "google_genai:chat-x")
    clean_env.setenv("GUARDIAN_REFLECTOR_MODEL", "google_genai:reflect-y")

    assert config.chat_model_name() == "google_genai:chat-x"
    assert config.reflector_model_name() == "google_genai:reflect-y"


def test_redact_secrets_removes_the_key_from_text(clean_env):
    clean_env.setenv("GOOGLE_API_KEY", FAKE_KEY)

    redacted = config.redact_secrets(f"request failed: key={FAKE_KEY} status=429")

    assert FAKE_KEY not in redacted
    assert "status=429" in redacted


def test_redact_secrets_leaves_text_unchanged_without_a_key(clean_env):
    assert config.redact_secrets("nothing secret here") == "nothing secret here"
