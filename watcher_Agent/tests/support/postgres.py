"""PostgreSQL test helpers.

Integration tests run only when ``TEST_DATABASE_URL`` points at a disposable
database. They skip with an explicit reason otherwise. The schema is rebuilt
once per test session and truncated before every test.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from judge.persistence import migrator

SKIP_REASON = (
    "TEST_DATABASE_URL is not set; PostgreSQL integration tests were not run "
    "(see watcher_Agent/docs/DATABASE_INTEGRATION.txt, step 12)"
)
VECTOR_SKIP_REASON = (
    "pgvector is not available on the test database; vector tests were not run"
)
_TRUNCATE = (
    "TRUNCATE judge.sessions, judge.notification_policy, "
    "judge.user_notification_state CASCADE"
)


def database_url() -> str:
    """The disposable test database URL, or skip the test."""
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip(SKIP_REASON)
    return url


def rebuild_schema(url: str) -> bool:
    """Drop and re-apply all migrations. Returns pgvector availability."""
    migrator.rollback(url, to_version=0)
    has_vector = migrator.vector_extension_available(url)
    migrator.apply(url, without_vector=not has_vector)
    return has_vector


def truncate_all(url: str) -> None:
    """Remove every row from the Judge tables."""
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(_TRUNCATE)
