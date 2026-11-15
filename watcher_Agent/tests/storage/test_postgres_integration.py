"""PostgreSQL integration: migrations, the read-only values role, end to end.

Skipped with an explicit reason unless ``TEST_DATABASE_URL`` is set.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from langgraph.store.postgres import AsyncPostgresStore, PostgresStore
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from judge.contracts.examples import example_values_store_item
from judge.contracts.record import EvaluationStatus
from judge.contracts.values import ApprovedValuesSnapshot
from judge.devfakes.clock import FakeClock
from judge.persistence import migrator
from judge.persistence.postgres import PostgresStorage
from judge.values.postgres_source import PostgresMemoryItemSource
from judge.values.reader import StoreValuesReader
from tests.support import postgres as pg
from tests.support.harness import Harness
from tests.support.records import make_session

pytestmark = pytest.mark.integration

RUNTIME_ROLE = "judge_runtime_test"
OWNER = "demo-user-synthetic"


def _as_role(url: str, role: str) -> str:
    return make_conninfo(url, user=role)


@pytest.fixture(scope="module")
def runtime_url(pg_schema: tuple[str, bool]) -> str:
    """A login role with the limited runtime grants, plus a seeded Store."""
    url, _ = pg_schema
    with psycopg.connect(url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (RUNTIME_ROLE,)
        ).fetchone()
        if exists is None:
            connection.execute(f'CREATE ROLE "{RUNTIME_ROLE}" LOGIN')
    migrator.setup_memory_store(url)
    migrator.grant_runtime_role(url, RUNTIME_ROLE)
    with PostgresStore.from_conn_string(url) as store:
        store.put(
            (OWNER, "memories"),
            "values.md",
            example_values_store_item().model_dump(mode="json"),
        )
    return _as_role(url, RUNTIME_ROLE)


def test_migrations_are_idempotent_and_reported(pg_schema: tuple[str, bool]) -> None:
    url, has_vector = pg_schema

    again = migrator.apply(url, without_vector=not has_vector)
    report = {version: state for version, _, state in migrator.status(url)}

    assert again == []
    assert sorted(report) == [1, 2, 3, 4, 5, 6]
    assert report[4] == ("applied" if has_vector else "skipped")
    assert {report[v] for v in (1, 2, 3, 5, 6)} == {"applied"}


def test_vector_migration_fails_clearly_when_pgvector_is_unavailable(
    pg_schema: tuple[str, bool],
) -> None:
    url, has_vector = pg_schema
    if has_vector:
        pytest.skip("pgvector is available on this database")

    with pytest.raises(migrator.MigrationError, match="pgvector"):
        migrator.apply(url)


async def test_runtime_role_reads_values_but_cannot_write_them(
    runtime_url: str,
) -> None:
    pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]] = AsyncConnectionPool(
        runtime_url,
        min_size=1,
        max_size=2,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=False,
    )
    await pool.open()
    try:
        reader = StoreValuesReader(
            PostgresMemoryItemSource(pool, layout="document"),
            max_bytes=16_384,
            require_approval=True,
            clock=FakeClock(),
        )
        snapshot = await reader.read(OWNER)
        missing = await reader.read("nobody-synthetic")
        async with pool.connection() as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await connection.execute(
                    "UPDATE store SET value = '{}'::jsonb WHERE key = 'values.md'"
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await connection.execute("DELETE FROM store")
        # The library's own read needs UPDATE (TTL refresh in a CTE), which is
        # why the Judge reads with a plain SELECT instead.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await AsyncPostgresStore(pool).aget(
                (OWNER, "memories"), "values.md", refresh_ttl=False
            )
    finally:
        await pool.close()

    assert isinstance(snapshot, ApprovedValuesSnapshot)
    assert snapshot.revision == "7"
    assert snapshot.active_value_ids == frozenset({"V1", "V3"})
    assert not isinstance(missing, ApprovedValuesSnapshot)


async def test_runtime_role_can_use_the_judge_tables(runtime_url: str) -> None:
    storage = await PostgresStorage.connect(runtime_url)
    try:
        session = make_session()
        async with storage.transaction() as tx:
            assert await tx.sessions.insert(session)
            assert await tx.sessions.delete(session.owner_id, session.session_id)
        health = await storage.health()
    finally:
        await storage.close()

    assert health.ok is True and health.backend == "postgres"


async def test_pipeline_runs_end_to_end_on_postgresql(
    pg_schema: tuple[str, bool],
) -> None:
    url, has_vector = pg_schema
    pg.truncate_all(url)
    storage = await PostgresStorage.connect(url, vector_enabled=has_vector)
    h = await Harness.create(storage=storage, embedding_indexing_enabled=has_vector)
    try:
        offset = await h.play_feed(["conflict"] * 3 + ["neutral"] * 12, audio=False)
        response = await h.frame(offset, 10_000)
        reels = (await h.reels())[:15]
        thumbnail = await h.runtime.timeline.thumbnail(h.owner, reels[0].id)
    finally:
        await h.close()

    assert {r.evaluation_status for r in reels} == {EvaluationStatus.JUDGED}
    assert "notify" not in response.model_dump()
    assert thumbnail.jpeg[:3] == b"\xff\xd8\xff"


async def test_guardian_values_match_rendering_and_never_fall_back(
    pg_schema: tuple[str, bool],
    runtime_url: str,
) -> None:
    from guardian.memory.values import render_values
    from judge.values.markdown_parser import parse_values_markdown

    url, _ = pg_schema
    content = example_values_store_item().canonical_content()
    entries = parse_values_markdown(content)
    namespace = (OWNER, "guardian", "values")
    with PostgresStore.from_conn_string(url) as store:
        store.put(
            namespace,
            "V10",
            {
                "text": entries[0]
                .raw_markdown.replace("## V1:", "## V10:")
                .replace("In my words:", "Agreed wording:"),
                "updated": "2026-11-14T12:00:00Z",
            },
        )
        store.put(
            namespace,
            "V2",
            {
                "text": entries[1].raw_markdown.replace("## V3:", "## V2:"),
                "updated": "2026-11-14T12:00:00Z",
            },
        )
        expected = render_values(store, OWNER)
    storage = await PostgresStorage.connect(runtime_url)
    try:
        reader = StoreValuesReader(
            PostgresMemoryItemSource(storage.pool),
            max_bytes=16_384,
            require_approval=True,
            clock=FakeClock(),
        )
        result = await reader.read(OWNER)
        assert isinstance(result, ApprovedValuesSnapshot)
        assert result.content == expected
        assert [e.value_id for e in result.entries] == ["V2", "V10"]
        assert result.entries[1].in_my_words
        with PostgresStore.from_conn_string(url) as store:
            store.delete(namespace, "V2")
            store.delete(namespace, "V10")
        missing = await reader.read(OWNER)
        assert not isinstance(missing, ApprovedValuesSnapshot)
        assert missing.reason.value == "missing"  # old values.md still exists
    finally:
        await storage.close()
