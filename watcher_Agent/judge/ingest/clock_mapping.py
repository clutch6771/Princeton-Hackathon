"""Mapping between the capture device's clock and the server clock.

Rules:

* Capture timestamps align media (frames with audio, audio with reel
  windows). They are never compared with server time directly.
* Server time governs receipt, idle expiry, cooldown, leases and deadlines.
* Per session the server keeps ``offset = min(received_at - capture_ts)`` over
  accepted events. The smallest observed difference is the best estimate of
  "clock difference plus minimum network delay". It is used only to express
  an observed reel end on the server clock for latency measurement, and the
  result is approximate.
* A capture timestamp that runs ahead of that mapping by more than the
  tolerance cannot be explained by network delay and is rejected.

Native adapters must derive capture timestamps from a monotonic source
anchored once per session, so a wall-clock adjustment cannot move them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from judge.errors import ClockSkewError

_EPOCH = datetime.fromtimestamp(0, tz=UTC)


@dataclass(frozen=True, slots=True)
class ClockMapping:
    """Immutable per-session estimate of ``server_ms - capture_ms``."""

    offset_ms: int | None = None

    def observe(
        self, received_at: datetime, capture_ms: int, tolerance_ms: int
    ) -> ClockMapping:
        """Return the mapping updated with one accepted event."""
        received_ms = int(received_at.timestamp() * 1000)
        difference = received_ms - capture_ms
        if self.offset_ms is None:
            return ClockMapping(offset_ms=difference)
        if difference < self.offset_ms - tolerance_ms:
            msg = "capture timestamp is ahead of this session's clock mapping"
            raise ClockSkewError(msg)
        return ClockMapping(offset_ms=min(self.offset_ms, difference))

    def to_server_time(self, capture_ms: int) -> datetime | None:
        """Approximate server time of a capture timestamp, if mapped."""
        if self.offset_ms is None:
            return None
        return _EPOCH + timedelta(milliseconds=capture_ms + self.offset_ms)
