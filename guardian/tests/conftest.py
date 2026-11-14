"""Fixtures shared by the Guardian tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from langgraph.store.memory import InMemoryStore

from guardian.datasource import FakeDataSource

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
