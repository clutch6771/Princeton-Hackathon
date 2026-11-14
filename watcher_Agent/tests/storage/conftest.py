"""Fixtures that provide each storage backend to the contract tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from judge.devfakes.memory_storage import MemoryStorage
from judge.persistence.interfaces import Storage
from judge.persistence.postgres import PostgresStorage
from tests.support import postgres as pg


@pytest.fixture(scope="session")
def pg_schema() -> tuple[str, bool]:
    """Rebuild the test schema once; returns (url, pgvector available)."""
    url = pg.database_url()
    return url, pg.rebuild_schema(url)


@pytest.fixture(
    params=[
        pytest.param("memory", marks=pytest.mark.unit),
        pytest.param("postgres", marks=pytest.mark.integration),
    ]
)
async def storage(request: pytest.FixtureRequest) -> AsyncIterator[Storage]:
    """A clean storage backend."""
    if request.param == "memory":
        yield MemoryStorage()
        return
    url, has_vector = request.getfixturevalue("pg_schema")
    pg.truncate_all(url)
    backend = await PostgresStorage.connect(url, vector_enabled=has_vector)
    try:
        yield backend
    finally:
        await backend.close()


@pytest.fixture
def vector_storage(storage: Storage, request: pytest.FixtureRequest) -> Storage:
    """A storage backend that can hold vectors, or skip."""
    if isinstance(storage, PostgresStorage) and not storage.vector_enabled:
        pytest.skip(pg.VECTOR_SKIP_REASON)
    return storage
