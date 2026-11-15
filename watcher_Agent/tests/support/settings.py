"""Hermetic settings for tests: nothing is read from the environment or .env."""

from __future__ import annotations

from typing import Any

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from judge.config import AppEnv, PrivacyMode, ProviderMode, Settings, StorageMode

DEMO_USER_ID = "demo-user-synthetic"


class HermeticSettings(Settings):
    """Settings built only from explicit keyword arguments."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def make_settings(**overrides: Any) -> Settings:
    """Development settings wired for fakes; override any field by name."""
    base: dict[str, Any] = {
        "app_env": AppEnv.DEVELOPMENT,
        "privacy_mode": PrivacyMode.DEV_PERMISSIVE,
        "provider_mode": ProviderMode.FAKE,
        "storage_mode": StorageMode.MEMORY,
        "demo_user_id": DEMO_USER_ID,
    }
    return HermeticSettings(**{**base, **overrides})


def make_production_settings(**overrides: Any) -> Settings:
    """Production-shaped settings with obviously fake credentials."""
    base: dict[str, Any] = {
        "app_env": AppEnv.PRODUCTION,
        "privacy_mode": PrivacyMode.ENFORCE,
        "provider_mode": ProviderMode.GEMINI,
        "storage_mode": StorageMode.POSTGRES,
        "demo_user_id": DEMO_USER_ID,
        "database_url": "postgresql://judge:fake@db.example.test/judge?sslmode=require",
        "gemini_api_key": "fake-gemini-key-for-tests",
    }
    return HermeticSettings(**{**base, **overrides})
