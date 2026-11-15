"""Fixtures shared by the Guardian tests.

The `store` fixture runs each test twice: in memory, and on Postgres when
TEST_DATABASE_URL is set. That variable must point only at a disposable database; the
tests delete the test users' rows from it. DATABASE_URL is never used by tests.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from dotenv import dotenv_values
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from guardian import api, chat_agent, config, reflector, storage
from guardian.datasource import FakeDataSource
from guardian.memory import locks
from guardian.seed.install import install_initial_values
from guardian.tests.fakes import ScriptedChatModel

USER = "demo-user"
OTHER_USER = "someone-else"
TEST_USERS = (USER, OTHER_USER, "a_b", "aXb")
NOW = datetime(2026, 11, 14, 22, 0, tzinfo=UTC)
TEST_DATABASE_URL_VAR = "TEST_DATABASE_URL"


def disposable_database_url() -> str:
    """The disposable test database, from the environment or the repository's .env."""
    value = os.environ.get(TEST_DATABASE_URL_VAR, "").strip()
    if value:
        return value
    from_file = dotenv_values(config.DEFAULT_ENV_PATH).get(TEST_DATABASE_URL_VAR)
    return str(from_file or "").strip()


def clean_test_users(opened: storage.Storage) -> None:
    """Delete everything the tests' users have in the test database."""
    with opened.pool.connection() as connection:
        for user in TEST_USERS:
            connection.execute(
                "DELETE FROM store WHERE prefix = %s OR prefix LIKE %s",
                (user, f"{user}.%"),
            )
            thread = f"{user}:%"
            connection.execute(
                "DELETE FROM checkpoint_writes WHERE thread_id LIKE %s", (thread,)
            )
            connection.execute(
                "DELETE FROM checkpoint_blobs WHERE thread_id LIKE %s", (thread,)
            )
            connection.execute(
                "DELETE FROM checkpoints WHERE thread_id LIKE %s", (thread,)
            )


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def clock() -> Callable[[], datetime]:
    return lambda: NOW


@pytest.fixture(scope="session")
def postgres_storage() -> Iterator[storage.Storage | None]:
    url = disposable_database_url()
    if not url:
        yield None
        return
    opened = storage.open_postgres(url)
    yield opened
    opened.close()


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
def store(
    request: pytest.FixtureRequest, postgres_storage: storage.Storage | None
) -> Iterator[Any]:
    """A store for the test, with the matching lock provider."""
    if request.param == "memory":
        locks.set_provider(locks.LocalLocks())
        yield InMemoryStore()
        return
    if postgres_storage is None:
        pytest.skip(f"{TEST_DATABASE_URL_VAR} is not set")
    clean_test_users(postgres_storage)
    locks.set_provider(postgres_storage.locks)
    yield postgres_storage.store
    clean_test_users(postgres_storage)
    locks.set_provider(locks.LocalLocks())


@pytest.fixture(autouse=True)
def reset_lock_provider() -> Iterator[None]:
    """No test leaves a database-backed lock provider behind for the next one."""
    yield
    locks.set_provider(locks.LocalLocks())


@pytest.fixture
def datasource(clock: Callable[[], datetime]) -> FakeDataSource:
    return FakeDataSource.from_seed(USER, clock=clock)


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_agent, "RETRY_INITIAL_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(reflector, "RETRY_INITIAL_DELAY_SECONDS", 0.0)


@pytest.fixture
def start(store, datasource, clock) -> Callable[[list[AIMessage]], ScriptedChatModel]:
    """Configure the package with a scripted chat model and return that model."""

    def configure(script: list[AIMessage]) -> ScriptedChatModel:
        model = ScriptedChatModel(script=script)
        api.configure(store, InMemorySaver(), datasource, clock=clock, chat_model=model)
        return model

    return configure


@pytest.fixture
def with_values(store, datasource) -> list[str]:
    """The persona's three approved values: V1 fitness, V2 news, V3 cooking."""
    return install_initial_values(store, datasource, USER, NOW)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: calls the real model API; needs GOOGLE_API_KEY. Run with -m live.",
    )
    config.addinivalue_line(
        "markers",
        "postgres: needs TEST_DATABASE_URL pointing at a disposable Postgres database.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip live tests unless they were asked for with `-m live`.

    This holds whichever pytest config is in effect, so running pytest from the
    repository root never spends API quota by accident.
    """
    if "live" in (config.getoption("-m") or ""):
        if "not live" not in config.getoption("-m"):
            return
    skip_live = pytest.mark.skip(reason="live test: run with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
