"""Create the runtime with real adapters, selected by configuration.

Production: PostgreSQL (Tiger Data) storage, the SELECT-only values reader
and the selected Grok/Gemini adapters. Fakes and in-memory storage are wired only when
configuration asks for them in development; ``validate_for_startup`` refuses
them in production, so missing configuration fails clearly instead of
producing mock judgments.
"""

from __future__ import annotations

import logging

import httpx

from judge.bootstrap import JudgeRuntime, build_runtime
from judge.config import ProviderMode, Settings, StorageMode
from judge.contracts.examples import example_values_store_item
from judge.contracts.storage import EmbeddingProfile
from judge.devfakes.memory_storage import MemoryStorage
from judge.devfakes.providers import FakeEmbeddingProvider, FakeUnderstandingProvider
from judge.errors import ConfigurationError
from judge.persistence.interfaces import Storage
from judge.persistence.postgres import PostgresStorage
from judge.runtime.clock import SystemClock
from judge.understanding.gemini import (
    GeminiEmbeddingProvider,
    GeminiUnderstandingProvider,
)
from judge.understanding.grok import GrokUnderstandingProvider
from judge.understanding.local_openai import (
    LocalEmbeddingProvider,
    LocalUnderstandingProvider,
)
from judge.understanding.provider import EmbeddingProvider, UnderstandingProvider
from judge.values.postgres_source import PostgresMemoryItemSource
from judge.values.reader import MemoryItemSource, StoredMemoryItem, StoreValuesReader

logger = logging.getLogger(__name__)


class SyntheticValuesSource:
    """Development only: serves the synthetic sample values for any owner."""

    async def fetch(self, owner_id: str) -> StoredMemoryItem | None:
        return StoredMemoryItem(
            value=example_values_store_item().model_dump(mode="json")
        )


def _providers(
    settings: Settings, profile: EmbeddingProfile
) -> tuple[UnderstandingProvider, EmbeddingProvider | None]:
    if settings.provider_mode is ProviderMode.GROK:
        if (
            settings.xai_api_key is None
            or not settings.xai_api_key.get_secret_value().strip()
        ):
            raise ConfigurationError("XAI_API_KEY is required when PROVIDER_MODE=grok")
        if settings.embedding_indexing_enabled:
            raise ConfigurationError("Set EMBEDDING_INDEXING_ENABLED=false for Grok")
        return (
            GrokUnderstandingProvider(
                api_key=settings.xai_api_key.get_secret_value(),
                model_id=settings.grok_model_id,
                transcription_model=settings.grok_transcription_model,
                timeout_seconds=settings.provider_timeout_seconds,
                transcript_max_chars=settings.transcript_max_chars,
            ),
            None,
        )
    if settings.provider_mode is ProviderMode.FAKE:
        logger.warning("PROVIDER_MODE=fake: judgments are synthetic")
        return (
            FakeUnderstandingProvider(verdict_pattern=settings.fake_verdict_pattern),
            FakeEmbeddingProvider(profile),
        )
    if settings.provider_mode is ProviderMode.LOCAL:
        client = httpx.AsyncClient(
            base_url=settings.local_provider_base_url or "",
            timeout=settings.provider_timeout_seconds,
        )
        local_profile = profile.model_copy(
            update={"model_id": settings.local_embedding_model or profile.model_id}
        )
        return (
            LocalUnderstandingProvider(
                client=client, model=settings.local_understanding_model or ""
            ),
            LocalEmbeddingProvider(client=client, profile=local_profile),
        )
    if settings.gemini_api_key is None:
        msg = "GEMINI_API_KEY is required when PROVIDER_MODE=gemini"
        raise ConfigurationError(msg)
    api_key = settings.gemini_api_key.get_secret_value()
    return (
        GeminiUnderstandingProvider(
            model_id=settings.understanding_model_id,
            api_key=api_key,
            timeout_seconds=settings.provider_timeout_seconds,
            max_output_tokens=settings.understanding_max_output_tokens,
        ),
        GeminiEmbeddingProvider(profile=profile, api_key=api_key),
    )


async def create_runtime(settings: Settings) -> JudgeRuntime:
    """Build the runtime described by ``settings`` (not yet started)."""
    profile = EmbeddingProfile.from_settings(settings)
    understanding, embedding = _providers(settings, profile)
    storage: Storage
    source: MemoryItemSource
    indexing = settings.embedding_indexing_enabled
    if settings.storage_mode is StorageMode.MEMORY:
        logger.warning(
            "STORAGE_MODE=memory: nothing is persisted; values are synthetic"
        )
        storage, source = MemoryStorage(), SyntheticValuesSource()
    else:
        postgres = await PostgresStorage.from_settings(settings)
        if indexing and not postgres.vector_enabled:
            logger.warning("pgvector table is missing; embedding indexing is disabled")
            indexing = False
        storage = postgres
        source = PostgresMemoryItemSource(
            postgres.pool,
            schema=settings.values_store_schema,
            layout=settings.values_store_layout,
        )
    clock = SystemClock()
    return build_runtime(
        settings,
        clock=clock,
        storage=storage,
        understanding=understanding,
        embedding=embedding if indexing else None,
        values=StoreValuesReader(
            source,
            max_bytes=settings.values_max_bytes,
            require_approval=settings.values_require_approval_metadata,
            clock=clock,
        ),
    )
