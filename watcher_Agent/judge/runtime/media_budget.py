"""Finite accounting of raw media held in volatile memory.

Raw frames and audio exist only in RAM. This budget makes that memory finite
and makes overload explicit: a refused reservation means the caller must drop
or reject, never offload to disk or the database.
"""

from __future__ import annotations

from uuid import UUID


class MediaBudget:
    """Counts bytes of raw media held, per session and process-wide."""

    def __init__(self, *, total_limit: int, session_limit: int) -> None:
        self._total_limit = total_limit
        self._session_limit = session_limit
        self._used = 0
        self._by_session: dict[UUID, int] = {}

    @property
    def used(self) -> int:
        """Bytes currently reserved across all sessions."""
        return self._used

    @property
    def tracked_sessions(self) -> int:
        """Number of sessions that currently hold media."""
        return len(self._by_session)

    def session_used(self, session_id: UUID) -> int:
        """Bytes currently reserved by one session."""
        return self._by_session.get(session_id, 0)

    def try_reserve(self, session_id: UUID, nbytes: int) -> bool:
        """Reserve ``nbytes``; False when a limit would be exceeded."""
        if nbytes < 0:
            msg = "nbytes must be non-negative"
            raise ValueError(msg)
        session_total = self.session_used(session_id) + nbytes
        if session_total > self._session_limit:
            return False
        if self._used + nbytes > self._total_limit:
            return False
        self._used += nbytes
        if session_total:
            self._by_session[session_id] = session_total
        return True

    def release(self, session_id: UUID, nbytes: int) -> None:
        """Return ``nbytes`` (clamped to what the session actually holds)."""
        if nbytes < 0:
            msg = "nbytes must be non-negative"
            raise ValueError(msg)
        held = self.session_used(session_id)
        freed = min(held, nbytes)
        remaining = held - freed
        self._used -= freed
        if remaining:
            self._by_session[session_id] = remaining
        else:
            self._by_session.pop(session_id, None)
