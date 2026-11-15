"""Registry of volatile per-session state held by this process.

State lives only here, in RAM. That is why the service must run as a single
process: a second process would not see these buffers. Dropping a session
releases every byte it held.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from judge.errors import OverloadedError
from judge.ingest.session_state import SessionState
from judge.runtime.media_budget import MediaBudget

_RETRY_AFTER_SECONDS = 5.0


class VolatileSessions:
    """Bounded map of session id to immutable ``SessionState``."""

    def __init__(self, *, max_sessions: int, budget: MediaBudget) -> None:
        self._max_sessions = max_sessions
        self._budget = budget
        self._states: dict[UUID, SessionState] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    def __len__(self) -> int:
        return len(self._states)

    def lock(self, session_id: UUID) -> asyncio.Lock:
        """The lock that serializes all work for one session."""
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def get(self, session_id: UUID) -> SessionState | None:
        """The current state, if this process holds one."""
        return self._states.get(session_id)

    def ensure_capacity(self, session_id: UUID) -> None:
        """Raise ``OverloadedError`` if a new session cannot be tracked."""
        if session_id not in self._states and len(self) >= self._max_sessions:
            msg = "too many active capture sessions; retry later"
            raise OverloadedError(msg, retry_after_seconds=_RETRY_AFTER_SECONDS)

    def put(self, state: SessionState) -> None:
        """Replace the state of a session (after a successful commit)."""
        self._states[state.session_id] = state

    def drop(self, session_id: UUID) -> None:
        """Forget a session and release all media it still holds."""
        state = self._states.pop(session_id, None)
        self._locks.pop(session_id, None)
        if state is not None:
            self._budget.release(session_id, state.held_bytes)

    def session_ids(self) -> list[UUID]:
        """Sessions currently tracked."""
        return list(self._states)

    def owned_by(self, owner_id: str) -> list[UUID]:
        """Sessions tracked for one owner."""
        return [s.session_id for s in self._states.values() if s.owner_id == owner_id]

    def drop_all(self) -> None:
        """Release everything (shutdown)."""
        for session_id in self.session_ids():
            self.drop(session_id)
