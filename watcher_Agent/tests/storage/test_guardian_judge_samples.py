"""Real Guardian approval -> SELECT-only values read -> xAI adapter -> SQL.

xAI HTTP responses are fixed references. Database and media codecs are real;
this test does not pretend that the model recognized content without a key.
"""

import httpx
import psycopg
import pytest
from langgraph.store.postgres import PostgresStore
from psycopg.conninfo import make_conninfo

from judge.contracts.values import ApprovedValuesSnapshot
from judge.evaluation import assess_decision
from judge.persistence import migrator
from judge.persistence.postgres import PostgresStorage
from judge.understanding.grok import GrokUnderstandingProvider
from judge.values.postgres_source import PostgresMemoryItemSource
from judge.values.reader import StoreValuesReader
from tests.support.guardian_samples import (
    OWNER,
    approve_guardian_values,
    demo_samples,
    execute_stream_sample,
    reference_for,
    replay_judgment,
)
from tests.support.harness import Harness
from tests.support.media import sine

pytestmark = pytest.mark.integration


async def test_guardian_approved_values_and_all_sample_decisions_on_postgres(
    pg_schema: tuple[str, bool],
) -> None:
    owner_url, _ = pg_schema
    migrator.setup_memory_store(owner_url)
    role = "judge_guardian_samples"
    with psycopg.connect(owner_url, autocommit=True) as connection:
        if (
            connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone()
            is None
        ):
            connection.execute("CREATE ROLE judge_guardian_samples LOGIN")
    migrator.grant_runtime_role(owner_url, role)
    with PostgresStore.from_conn_string(owner_url) as store:
        text, values = approve_guardian_values(store)
    runtime_url = make_conninfo(owner_url, user=role)
    storage = await PostgresStorage.connect(runtime_url, vector_enabled=False)
    current = demo_samples()[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/stt":
            return httpx.Response(200, json={"text": current["transcript"]})
        judgment = replay_judgment(current, audio=True)
        if not current["transcript"]:
            judgment = judgment.model_copy(update={"evidence_basis": ("frames",)})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": judgment.model_dump_json()}}]},
        )

    provider = GrokUnderstandingProvider(
        api_key="fake-xai-synthetic", transport=httpx.MockTransport(handler)
    )
    h = await Harness.create(
        storage=storage,
        provider=provider,
        values=values,
        embedding_indexing_enabled=False,
    )
    h.owner = OWNER
    reader = StoreValuesReader(
        PostgresMemoryItemSource(storage.pool),
        max_bytes=16_384,
        require_approval=True,
        clock=h.clock,
    )
    h.runtime.pipeline.values_gate.reader = reader
    try:
        actual_values = await reader.read(OWNER)
        assert isinstance(actual_values, ApprovedValuesSnapshot)
        assert actual_values.content == text
        for current in demo_samples():
            record = await execute_stream_sample(h, current, sine(3.0))
            assert (
                assess_decision(
                    record, reference_for(current, values), values
                ).confirmed
                is True
            )
            async with storage.transaction() as tx:
                reread = await tx.reels.get(OWNER, record.id)
            assert reread == record and record.model_id == "grok-4.7"
            assert record.values_sha256 == values.content_sha256
        async with storage.pool.connection() as runtime_connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await runtime_connection.execute("UPDATE store SET value = '{}'::jsonb")
        assert h.runtime.budget.used == 0
    finally:
        await h.close()
