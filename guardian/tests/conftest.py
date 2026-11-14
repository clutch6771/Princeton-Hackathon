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
