"""Fixtures shared by the Guardian tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from guardian import api, chat_agent, reflector
from guardian.datasource import FakeDataSource
from guardian.seed.install import install_initial_values
from guardian.tests.fakes import ScriptedChatModel

USER = "demo-user"
OTHER_USER = "someone-else"
NOW = datetime(2026, 11, 14, 22, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def clock() -> Callable[[], datetime]:
    return lambda: NOW


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


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
