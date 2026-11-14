"""Configuration: one typed source, PRD defaults, fail-fast startup checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from judge import config
from judge.config import (
    AppEnv,
    PrivacyMode,
    ProviderMode,
    Settings,
    StorageMode,
    validate_for_startup,
)
from judge.errors import ConfigurationError
from tests.support.settings import make_production_settings, make_settings

pytestmark = pytest.mark.unit


def test_prd_defaults_match_the_documented_values() -> None:
    settings = make_settings()

    assert settings.frame_interval_seconds == 2
    assert settings.frame_max_long_edge_px == 512
    assert settings.judge_selected_frames == 3
    assert settings.audio_sample_rate_hz == 16000
    assert settings.audio_channels == 1
    assert settings.audio_chunk_seconds == 5
    assert settings.max_feedback_examples == 3
    assert settings.reel_end_to_verdict_target_seconds == 10
    assert settings.notification_batch_size == 15
    assert settings.notification_min_conflicts == 3
    assert settings.notification_cooldown_seconds == 600
    assert settings.session_idle_timeout_seconds == 600


def test_fixed_prd_invariants_are_constants_not_settings() -> None:
    assert config.PERSIST_RAW_AUDIO is False
    assert config.PERSIST_RAW_VIDEO is False
    assert config.PERSIST_EXTRA_FRAMES is False
    assert config.PERSISTED_THUMBNAILS_PER_ACCEPTED_REEL == 1
    assert config.VALUES_ACCESS_MODE == "read_only"
    assert config.NORMAL_UNDERSTANDING_CALLS_PER_REEL == 1
    assert config.NORMAL_NEW_REEL_EMBEDDINGS_PER_REEL == 1
    assert config.NATIVE_MEMORY_CAP_MB == 50
    assert config.NATIVE_MEMORY_SPIKE_MINUTES == 10
    assert config.MAX_UPLOADS_IN_FLIGHT == 1
    assert not hasattr(make_settings(), "persist_raw_audio")


def test_defaults_are_fail_safe_production_values() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_env is AppEnv.PRODUCTION
    assert settings.privacy_mode is PrivacyMode.ENFORCE
    assert settings.provider_mode is ProviderMode.GEMINI
    assert settings.storage_mode is StorageMode.POSTGRES


def test_settings_are_immutable() -> None:
    settings = make_settings()

    with pytest.raises(ValidationError):
        settings.notification_batch_size = 99


@pytest.mark.parametrize(
    ("batch_size", "min_conflicts"),
    [(15, 16), (5, 0), (0, 0), (3, -1)],
)
def test_rejects_policy_outside_one_to_batch_size(
    batch_size: int, min_conflicts: int
) -> None:
    with pytest.raises(ValidationError):
        make_settings(
            notification_batch_size=batch_size,
            notification_min_conflicts=min_conflicts,
        )


def test_accepts_min_conflicts_equal_to_batch_size() -> None:
    settings = make_settings(notification_batch_size=4, notification_min_conflicts=4)

    assert settings.notification_min_conflicts == 4


def test_more_than_three_feedback_examples_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(max_feedback_examples=4)


def test_secrets_never_appear_in_repr() -> None:
    settings = make_production_settings()

    rendered = repr(settings) + str(settings)

    assert "fake-gemini-key-for-tests" not in rendered
    assert "judge:fake@" not in rendered


def test_environment_variables_use_the_documented_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFICATION_BATCH_SIZE", "20")
    monkeypatch.setenv("NOTIFICATION_MIN_CONFLICTS", "4")
    monkeypatch.setenv("DEMO_USER_ID", "env-user")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.notification_batch_size == 20
    assert settings.notification_min_conflicts == 4
    assert settings.demo_user_id == "env-user"


def test_development_with_fakes_passes_startup_validation() -> None:
    validate_for_startup(make_settings(), environ={})


def test_production_with_real_configuration_passes_startup_validation() -> None:
    validate_for_startup(make_production_settings(), environ={})


@pytest.mark.parametrize(
    ("overrides", "expected_fragment"),
    [
        ({"provider_mode": ProviderMode.FAKE}, "PROVIDER_MODE"),
        ({"storage_mode": StorageMode.MEMORY}, "STORAGE_MODE"),
        ({"privacy_mode": PrivacyMode.DEV_PERMISSIVE}, "PRIVACY_MODE"),
        ({"gemini_api_key": None}, "GEMINI_API_KEY"),
        ({"database_url": None}, "DATABASE_URL"),
        ({"demo_user_id": None}, "DEMO_USER_ID"),
        ({"demo_user_id": "  "}, "DEMO_USER_ID"),
        ({"embedding_dimension": 384}, "EMBEDDING_DIMENSION"),
    ],
)
def test_production_misconfiguration_fails_clearly(
    overrides: dict[str, object], expected_fragment: str
) -> None:
    settings = make_production_settings(**overrides)

    with pytest.raises(ConfigurationError) as excinfo:
        validate_for_startup(settings, environ={})

    assert expected_fragment in str(excinfo.value)
    assert "fake-gemini-key-for-tests" not in str(excinfo.value)


def test_local_provider_requires_its_endpoint_and_model() -> None:
    settings = make_settings(provider_mode=ProviderMode.LOCAL)

    with pytest.raises(ConfigurationError) as excinfo:
        validate_for_startup(settings, environ={})

    assert "LOCAL_PROVIDER_BASE_URL" in str(excinfo.value)
    assert "LOCAL_UNDERSTANDING_MODEL" in str(excinfo.value)


@pytest.mark.parametrize("flag", ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"])
@pytest.mark.parametrize("value", ["true", "1", "YES"])
def test_sdk_tracing_blocks_startup_because_it_would_export_media(
    flag: str, value: str
) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        validate_for_startup(make_production_settings(), environ={flag: value})

    assert flag in str(excinfo.value)


def test_disabled_tracing_flag_is_accepted() -> None:
    validate_for_startup(
        make_production_settings(), environ={"LANGSMITH_TRACING": "false"}
    )


def test_all_problems_are_reported_together() -> None:
    settings = make_production_settings(gemini_api_key=None, database_url=None)

    with pytest.raises(ConfigurationError) as excinfo:
        validate_for_startup(settings, environ={})

    assert "GEMINI_API_KEY" in str(excinfo.value)
    assert "DATABASE_URL" in str(excinfo.value)


def test_embedding_profile_identity_pins_model_dimension_task_and_version() -> None:
    settings = make_settings()

    assert settings.embedding_profile_id == (
        "gemini-embedding-001:768:SEMANTIC_SIMILARITY:v1"
    )
