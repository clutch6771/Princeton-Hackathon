"""The single typed configuration source for the Judge backend.

Settings are loaded once from the process environment (and an optional ``.env``
file) into an immutable object. Nothing the model reads or returns can alter
them. Secrets are ``SecretStr`` and are never logged.

PRD-fixed invariants are module constants, not settings: configuration cannot
switch them off. Every name here is documented in
``watcher_Agent/docs/MEMORY_PARAMETERS.txt``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from judge.errors import ConfigurationError

# --- PRD-fixed invariants (not configurable) --------------------------------

PERSIST_RAW_AUDIO: Final = False
PERSIST_RAW_VIDEO: Final = False
PERSIST_EXTRA_FRAMES: Final = False
PERSISTED_THUMBNAILS_PER_ACCEPTED_REEL: Final = 1
VALUES_ACCESS_MODE: Final = "read_only"
NORMAL_UNDERSTANDING_CALLS_PER_REEL: Final = 1
NORMAL_NEW_REEL_EMBEDDINGS_PER_REEL: Final = 1
FRAME_MIME_TYPE: Final = "image/jpeg"
AUDIO_CODEC: Final = "aac"

# Native adapter requirements. Documented here; enforced on the device.
NATIVE_MEMORY_CAP_MB: Final = 50
NATIVE_MEMORY_SPIKE_MINUTES: Final = 10
MAX_UPLOADS_IN_FLIGHT: Final = 1

# The vector column created by migration 0004 is ``vector(768)``.
EMBEDDING_COLUMN_DIMENSION: Final = 768

# SDK tracing would export prompts (frames, audio, values) to a third party.
TRACING_ENV_FLAGS: Final = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_TRACING",
)
_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})


class AppEnv(StrEnum):
    """Deployment environment. Production is the fail-safe default."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class PrivacyMode(StrEnum):
    """How the backend eligibility gate treats privacy provenance."""

    ENFORCE = "enforce"
    DEV_PERMISSIVE = "dev_permissive"


class ProviderMode(StrEnum):
    """Which understanding and embedding adapters are wired."""

    GEMINI = "gemini"
    LOCAL = "local"
    FAKE = "fake"


class StorageMode(StrEnum):
    """Which storage adapter is wired."""

    POSTGRES = "postgres"
    MEMORY = "memory"


class Settings(BaseSettings):
    """Immutable runtime configuration. Field names are the env var names."""

    model_config = SettingsConfigDict(
        env_file=(".env", "watcher_Agent/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
    )

    # --- Modes and identity -------------------------------------------------
    app_env: AppEnv = AppEnv.PRODUCTION
    privacy_mode: PrivacyMode = PrivacyMode.ENFORCE
    provider_mode: ProviderMode = ProviderMode.GEMINI
    storage_mode: StorageMode = StorageMode.POSTGRES
    demo_user_id: str | None = None
    demo_user_header: str = "X-Demo-User-Id"

    # --- Secrets (environment or secret store only) -------------------------
    database_url: SecretStr | None = None
    gemini_api_key: SecretStr | None = None

    # --- Shared memory Store (read-only for the Judge) ----------------------
    values_store_schema: str = Field(default="public", pattern=r"^[a-z_][a-z0-9_]*$")
    values_require_approval_metadata: bool = True

    # --- Models -------------------------------------------------------------
    understanding_model_id: str = "gemini-3.5-flash-lite"
    understanding_prompt_version: str = "judge-v1"
    embedding_model_id: str = "gemini-embedding-001"
    embedding_dimension: int = Field(default=EMBEDDING_COLUMN_DIMENSION, gt=0)
    embedding_task_type: str = "SEMANTIC_SIMILARITY"
    embedding_profile_version: str = "v1"
    embedding_indexing_enabled: bool = True
    local_provider_base_url: str | None = None
    local_understanding_model: str | None = None
    local_embedding_model: str | None = None
    fake_verdict_pattern: str = "neutral"

    # --- PRD defaults: capture ----------------------------------------------
    frame_interval_seconds: float = Field(default=2.0, gt=0)
    frame_max_long_edge_px: int = Field(default=512, gt=0)
    judge_selected_frames: int = Field(default=3, ge=1, le=3)
    audio_sample_rate_hz: Literal[16000] = 16000
    audio_channels: Literal[1] = 1
    audio_chunk_seconds: float = Field(default=5.0, gt=0)

    # --- PRD defaults: judgment and policy ----------------------------------
    max_feedback_examples: int = Field(default=3, ge=0, le=3)
    reel_end_to_verdict_target_seconds: float = Field(default=10.0, gt=0)
    notification_batch_size: int = Field(default=15, ge=1, le=100)
    notification_min_conflicts: int = Field(default=3, ge=1)
    notification_cooldown_seconds: int = Field(default=600, ge=0)
    session_idle_timeout_seconds: int = Field(default=600, gt=0)

    # --- Engineering: payload limits ----------------------------------------
    frame_max_bytes: int = Field(default=300_000, gt=0)
    audio_chunk_max_bytes: int = Field(default=100_000, gt=0)
    audio_chunk_max_seconds: float = Field(default=6.0, gt=0)
    ingest_max_body_bytes: int = Field(default=400_000, gt=0, lt=1_000_000)
    thumbnail_max_long_edge_px: int = Field(default=320, gt=0)
    thumbnail_jpeg_quality: int = Field(default=70, ge=10, le=95)
    thumbnail_max_bytes: int = Field(default=48_000, gt=0)

    # --- Engineering: context and token budgets -----------------------------
    values_max_bytes: int = Field(default=16_384, gt=0)
    feedback_example_max_chars: int = Field(default=600, gt=0)
    understanding_max_output_tokens: int = Field(default=1024, gt=0)
    understanding_max_input_tokens: int = Field(default=12_000, gt=0)
    transcript_max_chars: int = Field(default=4000, gt=0)

    # --- Engineering: volatile memory and queues ----------------------------
    media_budget_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    session_media_budget_bytes: int = Field(default=24 * 1024 * 1024, gt=0)
    max_candidate_seconds: float = Field(default=90.0, gt=0)
    min_judgeable_frames: int = Field(default=1, ge=1)
    worker_queue_capacity: int = Field(default=32, gt=0)
    worker_concurrency: int = Field(default=2, gt=0)
    max_active_sessions: int = Field(default=4, gt=0)

    # --- Engineering: segmentation, clocks, deadlines -----------------------
    segment_frame_hash_threshold: int = Field(default=20, ge=1, le=64)
    segment_overlay_hash_threshold: int = Field(default=12, ge=1, le=64)
    segment_gap_seconds: float = Field(default=8.0, gt=0)
    transition_hint_uncertainty_ms: int = Field(default=250, ge=0)
    clock_skew_tolerance_seconds: float = Field(default=5.0, ge=0)
    late_event_grace_seconds: float = Field(default=3.0, ge=0)
    final_audio_wait_seconds: float = Field(default=6.0, ge=0)
    audio_complete_ratio: float = Field(default=0.8, gt=0, le=1)
    min_audio_ms_for_model: int = Field(default=500, ge=0)

    # --- Engineering: providers and database --------------------------------
    provider_timeout_seconds: float = Field(default=8.0, gt=0)
    provider_max_attempts: int = Field(default=2, ge=1, le=3)
    provider_requests_per_minute: int = Field(default=10, gt=0)
    provider_requests_per_day: int | None = Field(default=None, gt=0)
    job_deadline_seconds: float = Field(default=30.0, gt=0)
    job_sweep_grace_seconds: float = Field(default=5.0, ge=0)
    db_statement_timeout_ms: int = Field(default=5000, gt=0)
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=8, ge=1)
    embedding_requests_per_minute: int = Field(default=60, gt=0)
    embedding_max_attempts: int = Field(default=5, ge=1)
    embedding_retry_base_seconds: float = Field(default=2.0, gt=0)

    # --- Engineering: notifications, values wait, retention -----------------
    notification_ttl_seconds: int = Field(default=120, gt=0)
    notification_lease_seconds: int = Field(default=30, gt=0)
    notification_max_dispatch_attempts: int = Field(default=3, ge=1)
    values_wait_seconds: float = Field(default=20.0, ge=0)
    values_poll_seconds: float = Field(default=5.0, gt=0)
    values_cache_ttl_seconds: float = Field(default=5.0, ge=0)
    receipt_retention_seconds: int = Field(default=86_400, gt=0)
    tick_interval_seconds: float = Field(default=0.5, gt=0)
    ingest_requests_per_minute: int = Field(default=240, gt=0)
    api_requests_per_minute: int = Field(default=120, gt=0)

    @model_validator(mode="after")
    def _check_policy_bounds(self) -> Self:
        if self.notification_min_conflicts > self.notification_batch_size:
            msg = "NOTIFICATION_MIN_CONFLICTS must be <= NOTIFICATION_BATCH_SIZE"
            raise ValueError(msg)
        if self.final_audio_wait_seconds >= self.job_deadline_seconds:
            msg = "FINAL_AUDIO_WAIT_SECONDS must be < JOB_DEADLINE_SECONDS"
            raise ValueError(msg)
        if self.db_pool_min_size > self.db_pool_max_size:
            msg = "DB_POOL_MIN_SIZE must be <= DB_POOL_MAX_SIZE"
            raise ValueError(msg)
        return self

    @property
    def embedding_profile_id(self) -> str:
        """Identity of the vector space: model, dimension, task and version."""
        return ":".join(
            (
                self.embedding_model_id,
                str(self.embedding_dimension),
                self.embedding_task_type,
                self.embedding_profile_version,
            )
        )

    @property
    def is_production(self) -> bool:
        """True unless APP_ENV is explicitly ``development``."""
        return self.app_env is AppEnv.PRODUCTION


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _production_problems(settings: Settings) -> list[str]:
    problems: list[str] = []
    if settings.privacy_mode is not PrivacyMode.ENFORCE:
        problems.append(
            "PRIVACY_MODE must be 'enforce' unless APP_ENV=development "
            "(the permissive eligibility gate is development-only)"
        )
    if settings.provider_mode is ProviderMode.FAKE:
        problems.append(
            "PROVIDER_MODE=fake is development-only; "
            "production never produces mock judgments"
        )
    if settings.storage_mode is StorageMode.MEMORY:
        problems.append("STORAGE_MODE=memory is development-only")
    return problems


def _provider_problems(settings: Settings) -> list[str]:
    problems: list[str] = []
    if settings.provider_mode is ProviderMode.GEMINI and (
        settings.gemini_api_key is None
        or _is_blank(settings.gemini_api_key.get_secret_value())
    ):
        problems.append("GEMINI_API_KEY is required when PROVIDER_MODE=gemini")
    if settings.provider_mode is ProviderMode.LOCAL:
        if _is_blank(settings.local_provider_base_url):
            problems.append("LOCAL_PROVIDER_BASE_URL is required for local mode")
        if _is_blank(settings.local_understanding_model):
            problems.append("LOCAL_UNDERSTANDING_MODEL is required for local mode")
    if settings.embedding_dimension != EMBEDDING_COLUMN_DIMENSION:
        problems.append(
            f"EMBEDDING_DIMENSION must equal the vector column dimension "
            f"({EMBEDDING_COLUMN_DIMENSION}); changing it needs a new, explicitly "
            f"versioned embedding migration"
        )
    return problems


def _tracing_problems(environ: Mapping[str, str]) -> list[str]:
    return [
        f"{flag} must be disabled: SDK tracing would export frames, audio and "
        f"values to a third party"
        for flag in TRACING_ENV_FLAGS
        if environ.get(flag, "").strip().lower() in _TRUTHY
    ]


def validate_for_startup(
    settings: Settings, *, environ: Mapping[str, str] | None = None
) -> None:
    """Fail fast, listing every configuration problem by variable name."""
    env = os.environ if environ is None else environ
    problems: list[str] = []
    if _is_blank(settings.demo_user_id):
        problems.append("DEMO_USER_ID is required (the single demo identity)")
    if settings.is_production:
        problems.extend(_production_problems(settings))
    problems.extend(_provider_problems(settings))
    if settings.storage_mode is StorageMode.POSTGRES and (
        settings.database_url is None
        or _is_blank(settings.database_url.get_secret_value())
    ):
        problems.append("DATABASE_URL is required when STORAGE_MODE=postgres")
    problems.extend(_tracing_problems(env))
    if problems:
        raise ConfigurationError("Invalid configuration: " + "; ".join(problems))


def load_settings() -> Settings:
    """Load settings from the environment and optional .env files."""
    return Settings()
