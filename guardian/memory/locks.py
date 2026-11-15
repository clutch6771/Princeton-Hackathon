"""Locks that keep the package's guarantees when more than one process is running.

Three things must never run twice at once for the same user: a decision on a value
card (decide-once, and the value-ID counter), saving a note (the note-ID counter),
and a Reflector run. In memory, a lock inside this process is enough. On Postgres the
same code takes an advisory lock in the database, so the guarantee also holds across
server processes.

`guardian.api.configure` chooses the provider; the default is in-process.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

VALUES = "values"  # proposals and decisions, including value-ID allocation
NOTES = "notes"
REFLECT = "reflect"

_ADVISORY_LOCK = "SELECT pg_advisory_lock(hashtextextended(%s, 0))"
_ADVISORY_TRY_LOCK = "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS acquired"
_ADVISORY_UNLOCK = "SELECT pg_advisory_unlock(hashtextextended(%s, 0))"


class Locks(Protocol):
    def hold(self, key: str) -> Any: ...  # context manager; waits for the lock

    def attempt(
        self, key: str
    ) -> Any: ...  # context manager yielding whether it got the lock


class LocalLocks:
    """Locks within one process."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._lock(key):
            yield

    @contextmanager
    def attempt(self, key: str) -> Iterator[bool]:
        lock = self._lock(key)
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()


class PostgresLocks:
    """Advisory locks in the database, shared by every process that uses it.

    A lock belongs to one database connection, so the connection is kept out of the
    pool for as long as the lock is held.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._pool.connection() as connection:
            connection.execute(_ADVISORY_LOCK, (key,))
            try:
                yield
            finally:
                connection.execute(_ADVISORY_UNLOCK, (key,))

    @contextmanager
    def attempt(self, key: str) -> Iterator[bool]:
        with self._pool.connection() as connection:
            row = connection.execute(_ADVISORY_TRY_LOCK, (key,)).fetchone()
            acquired = bool(row["acquired"])
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(_ADVISORY_UNLOCK, (key,))


_provider: Locks = LocalLocks()


def set_provider(provider: Locks) -> None:
    """Choose where locks are taken. Called by `guardian.api.configure`."""
    global _provider
    _provider = provider


def _key(user_id: str, purpose: str) -> str:
    return f"guardian:{purpose}:{user_id}"


def hold(user_id: str, purpose: str) -> Any:
    """Wait for this user's lock for one purpose, and hold it for the `with` block."""
    return _provider.hold(_key(user_id, purpose))


def attempt(user_id: str, purpose: str) -> Any:
    """Try to take this user's lock without waiting. Yields whether it was taken."""
    return _provider.attempt(_key(user_id, purpose))
