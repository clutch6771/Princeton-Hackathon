"""Clock mapping: capture time aligns media, server time governs deadlines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from judge.errors import ClockSkewError
from judge.ingest.clock_mapping import ClockMapping

pytestmark = pytest.mark.unit

SERVER_T0 = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
CAPTURE_T0_MS = 1_800_000_000_000
TOLERANCE_MS = 5_000


def _at(seconds: float) -> datetime:
    return SERVER_T0 + timedelta(seconds=seconds)


def test_unmapped_clock_cannot_convert() -> None:
    assert ClockMapping().to_server_time(CAPTURE_T0_MS) is None


def test_first_event_establishes_the_offset() -> None:
    mapping = ClockMapping().observe(_at(0.4), CAPTURE_T0_MS, TOLERANCE_MS)

    assert mapping.to_server_time(CAPTURE_T0_MS) == _at(0.4)
    assert mapping.to_server_time(CAPTURE_T0_MS + 2000) == _at(2.4)


def test_offset_follows_the_smallest_observed_delay() -> None:
    mapping = ClockMapping().observe(_at(3.0), CAPTURE_T0_MS, TOLERANCE_MS)

    # A later event that arrived with less network delay tightens the estimate.
    mapping = mapping.observe(_at(2.2), CAPTURE_T0_MS + 2000, TOLERANCE_MS)

    assert mapping.to_server_time(CAPTURE_T0_MS) == _at(0.2)


def test_slower_events_do_not_loosen_the_estimate() -> None:
    mapping = ClockMapping().observe(_at(0.2), CAPTURE_T0_MS, TOLERANCE_MS)

    mapping = mapping.observe(_at(9.0), CAPTURE_T0_MS + 2000, TOLERANCE_MS)

    assert mapping.to_server_time(CAPTURE_T0_MS) == _at(0.2)


def test_observe_returns_a_new_mapping_and_leaves_the_old_one_unchanged() -> None:
    original = ClockMapping()

    updated = original.observe(_at(0.2), CAPTURE_T0_MS, TOLERANCE_MS)

    assert original.to_server_time(CAPTURE_T0_MS) is None
    assert updated is not original


def test_capture_time_far_ahead_of_the_mapping_is_rejected() -> None:
    mapping = ClockMapping().observe(_at(0.2), CAPTURE_T0_MS, TOLERANCE_MS)

    with pytest.raises(ClockSkewError):
        # Claims to be captured 60 s after t0 but arrives 1 s after t0.
        mapping.observe(_at(1.0), CAPTURE_T0_MS + 60_000, TOLERANCE_MS)


def test_capture_time_slightly_ahead_within_tolerance_is_accepted() -> None:
    mapping = ClockMapping().observe(_at(0.2), CAPTURE_T0_MS, TOLERANCE_MS)

    mapping = mapping.observe(_at(1.0), CAPTURE_T0_MS + 3000, TOLERANCE_MS)

    assert mapping.to_server_time(CAPTURE_T0_MS) is not None


def test_device_clock_may_differ_arbitrarily_from_the_server_clock() -> None:
    # The device is "years" behind the server; only the mapping matters.
    mapping = ClockMapping().observe(_at(0.3), 1_000, TOLERANCE_MS)

    assert mapping.to_server_time(3_000) == _at(2.3)
