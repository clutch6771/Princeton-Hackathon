"""Volatile per-session state: pure transitions over immutable state."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import numpy as np
import pytest

from judge.contracts.candidate import CloseReason, SegmentationStatus
from judge.contracts.capture import AudioChunk
from judge.contracts.ingest import AckStatus
from judge.ingest.session_state import (
    AudioNote,
    AudioNoteKind,
    ClosedCandidate,
    KeptFrame,
    PcmSegment,
    SessionState,
    StateConfig,
    apply_audio,
    apply_feed_exit,
    apply_frame,
    close_for_end,
    new_session_state,
    prune_audio,
    reel_id_for,
    take_ready,
    with_order_seqs,
)
from judge.segmentation.frame_hash import FrameSignature
from judge.segmentation.segmenter import FrameSample, HashReelSegmenter, SegmenterConfig
from tests.support.builders import SESSION_ID, SOURCE_ID, T0_MS, audio_meta

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
MASK = (1 << 64) - 1
SIG_A = FrameSignature(full=0, overlay=0)
SIG_A_CUT = FrameSignature(full=(1 << 40) - 1, overlay=0)
SIG_B = FrameSignature(full=(1 << 40) - 1, overlay=(1 << 30) - 1)
SIG_C = FrameSignature(
    full=(((1 << 40) - 1) << 24) & MASK, overlay=(((1 << 30) - 1) << 34) & MASK
)
SEGMENTER = HashReelSegmenter(
    SegmenterConfig(frame_threshold=20, overlay_threshold=12, gap_ms=8000)
)
CONFIG = StateConfig(
    max_frames=3,
    max_candidate_ms=90_000,
    late_grace_ms=3000,
    hint_uncertainty_ms=250,
    default_uncertainty_ms=2000,
    final_audio_wait_seconds=6.0,
)


def _state() -> SessionState:
    return new_session_state(
        owner_id="demo-user-synthetic",
        session_id=SESSION_ID,
        source_id=SOURCE_ID,
        now=NOW,
    )


def _frame(
    offset_ms: int,
    signature: FrameSignature,
    *,
    transition_offset_ms: int | None = None,
    size: int = 100,
) -> tuple[FrameSample, KeptFrame]:
    event_id = uuid4()
    ts = T0_MS + offset_ms
    transition = None if transition_offset_ms is None else T0_MS + transition_offset_ms
    sample = FrameSample(
        event_id=event_id, ts_ms=ts, signature=signature, transition_at_ms=transition
    )
    return sample, KeptFrame(
        event_id=event_id, ts_ms=ts, jpeg=b"j" * size, width=288, height=512
    )


def _feed(
    state: SessionState,
    frames: list[tuple[FrameSample, KeptFrame]],
    config: StateConfig = CONFIG,
    monotonic: float = 100.0,
) -> tuple[SessionState, list[ClosedCandidate], list[AckStatus], list[KeptFrame]]:
    closed: list[ClosedCandidate] = []
    statuses: list[AckStatus] = []
    released: list[KeptFrame] = []
    for sample, kept in frames:
        result = apply_frame(
            state,
            sample,
            kept,
            config=config,
            segmenter=SEGMENTER,
            now=NOW,
            monotonic=monotonic,
        )
        state = result.state
        closed.extend(result.closed)
        statuses.append(result.status)
        released.extend(result.released)
    return state, closed, statuses, released


def _pcm(offset_ms: int, duration_ms: int) -> PcmSegment:
    samples = np.zeros(duration_ms * 16, dtype=np.int16)
    return PcmSegment(start_ms=T0_MS + offset_ms, samples=samples)


def _chunk(offset_ms: int, duration_ms: int = 5000) -> AudioChunk:
    return AudioChunk.model_validate(
        audio_meta(start_ms=T0_MS + offset_ms, end_ms=T0_MS + offset_ms + duration_ms)
    )


def test_first_frame_opens_a_candidate_with_an_unknown_start() -> None:
    state, closed, statuses, _ = _feed(_state(), [_frame(0, SIG_A)])

    assert closed == []
    assert statuses == [AckStatus.ACCEPTED]
    assert state.open is not None
    assert state.open.start_ms == T0_MS
    assert state.open.first_second_observed is False


def test_boundary_closes_the_exposure_and_opens_the_next() -> None:
    frames = [
        _frame(0, SIG_A),
        _frame(2000, SIG_A),
        _frame(4000, SIG_A),
        _frame(6000, SIG_B),
        _frame(8000, SIG_B),
    ]

    state, closed, _, _ = _feed(_state(), frames)

    assert len(closed) == 1
    first = closed[0]
    assert (first.start_ms, first.end_ms) == (T0_MS, T0_MS + 4000)
    assert first.frame_count == 3
    assert first.close_reason is CloseReason.BOUNDARY
    assert first.boundary_uncertainty_ms == 2000
    assert first.audio_window == (T0_MS, T0_MS + 4000)
    assert first.segmentation_status is SegmentationStatus.APPROXIMATE
    assert first.first_second_observed is False
    assert state.open is not None and state.open.start_ms == T0_MS + 6000
    assert state.closed == tuple(closed)


def test_reel_id_is_derived_from_the_first_frame_and_is_stable() -> None:
    frames = [_frame(0, SIG_A), _frame(2000, SIG_B)]

    _, closed, _, _ = _feed(_state(), frames)

    expected = reel_id_for(SESSION_ID, frames[0][0].event_id)
    assert closed[0].reel_id == expected
    assert closed[0].first_event_id == frames[0][0].event_id
    assert isinstance(expected, UUID)
    assert reel_id_for(SESSION_ID, frames[0][0].event_id) == expected


def test_boundary_interval_audio_belongs_to_neither_reel() -> None:
    frames = [
        _frame(0, SIG_A),
        _frame(2000, SIG_A),
        _frame(4000, SIG_B),
        _frame(6000, SIG_B),
        _frame(8000, SIG_C),
    ]

    _, closed, _, _ = _feed(_state(), frames)

    reel_a, reel_b = closed
    assert reel_a.audio_window == (T0_MS, T0_MS + 2000)
    assert reel_b.audio_window == (T0_MS + 4000, T0_MS + 6000)
    assert reel_a.audio_window[1] < reel_b.audio_window[0]
    assert reel_b.boundary_guard_ms == 4000


def test_in_reel_cut_stays_in_the_same_exposure_and_is_flagged_uncertain() -> None:
    frames = [
        _frame(0, SIG_A),
        _frame(2000, SIG_A_CUT),
        _frame(4000, SIG_A_CUT),
        _frame(6000, SIG_C),
    ]

    _, closed, _, _ = _feed(_state(), frames)

    assert len(closed) == 1
    assert closed[0].frame_count == 3
    assert closed[0].uncorroborated_changes == 1
    assert closed[0].segmentation_status is SegmentationStatus.UNCERTAIN


def test_transition_hints_tighten_boundaries_and_audio_windows() -> None:
    frames = [
        _frame(0, SIG_A),
        _frame(2000, SIG_A),
        _frame(4000, SIG_A),
        _frame(6000, SIG_B, transition_offset_ms=5200),
        _frame(8000, SIG_B),
        _frame(10_000, SIG_B),
        _frame(12_000, SIG_C, transition_offset_ms=11_500),
    ]

    _, closed, _, _ = _feed(_state(), frames)

    reel_a, reel_b = closed
    assert reel_a.audio_window == (T0_MS, T0_MS + 4950)
    assert reel_b.audio_window == (T0_MS + 5450, T0_MS + 11_250)
    assert reel_b.boundary_uncertainty_ms == 250
    assert reel_b.first_second_observed is True
    assert reel_b.segmentation_status is SegmentationStatus.CLEAN
    assert reel_a.segmentation_status is SegmentationStatus.APPROXIMATE


def test_hint_far_from_the_first_frame_does_not_claim_the_first_second() -> None:
    frames = [
        _frame(0, SIG_A),
        _frame(4000, SIG_B, transition_offset_ms=2500),
        _frame(6000, SIG_C),
    ]

    _, closed, _, _ = _feed(_state(), frames)

    assert closed[1].first_second_observed is False


def test_single_frame_exposure_is_kept_with_zero_observed_span() -> None:
    frames = [_frame(0, SIG_A), _frame(2000, SIG_B), _frame(4000, SIG_C)]

    _, closed, _, _ = _feed(_state(), frames)

    short = closed[1]
    assert short.frame_count == 1
    assert short.start_ms == short.end_ms == T0_MS + 2000
    assert short.audio_window is None
    assert short.segmentation_status is SegmentationStatus.UNCERTAIN


def test_long_frame_gap_closes_the_exposure() -> None:
    frames = [_frame(0, SIG_A), _frame(2000, SIG_A), _frame(12_000, SIG_A)]

    state, closed, _, _ = _feed(_state(), frames)

    assert len(closed) == 1
    assert closed[0].close_reason is CloseReason.FRAME_GAP
    assert closed[0].end_ms == T0_MS + 2000
    assert closed[0].segmentation_status is SegmentationStatus.UNCERTAIN
    assert state.open is not None and state.open.start_ms == T0_MS + 12_000


def test_leaving_the_feed_closes_the_exposure_and_resets_the_start() -> None:
    state, _, _, _ = _feed(_state(), [_frame(0, SIG_A), _frame(2000, SIG_A)])

    state, closed = apply_feed_exit(
        state, T0_MS + 3000, config=CONFIG, now=NOW, monotonic=100.0
    )
    state, later, _, _ = _feed(state, [_frame(9000, SIG_A)])

    assert len(closed) == 1
    assert closed[0].close_reason is CloseReason.LEFT_FEED
    assert closed[0].end_ms == T0_MS + 2000
    assert closed[0].audio_window == (T0_MS, T0_MS + 2000)
    assert later == []
    assert state.open is not None and state.open.start_ms == T0_MS + 9000
    assert state.open.first_second_observed is False


def test_feed_exit_with_nothing_open_changes_nothing_but_the_last_frame() -> None:
    state, closed = apply_feed_exit(
        _state(), T0_MS, config=CONFIG, now=NOW, monotonic=100.0
    )

    assert closed == ()
    assert state.open is None


def test_exposure_is_truncated_at_the_maximum_duration() -> None:
    config = replace(CONFIG, max_candidate_ms=6000)
    frames = [_frame(offset, SIG_A) for offset in range(0, 14_000, 2000)]
    frames.append(_frame(14_000, SIG_B))

    state, closed, statuses, released = _feed(_state(), frames, config=config)

    assert len(closed) == 1
    assert closed[0].close_reason is CloseReason.MAX_DURATION
    assert closed[0].truncated is True
    assert closed[0].end_ms == T0_MS + 6000
    assert closed[0].segmentation_status is SegmentationStatus.UNCERTAIN
    # Frames of the same reel after truncation are processed but not kept.
    assert set(statuses) == {AckStatus.ACCEPTED}
    late_same_reel = {kept.ts_ms for _, kept in frames[4:7]}
    assert late_same_reel <= {frame.ts_ms for frame in released}
    assert state.open is not None and state.open.start_ms == T0_MS + 14_000


def test_selection_offers_first_middle_last_from_a_bounded_reservoir() -> None:
    frames = [_frame(offset, SIG_A) for offset in range(0, 20_000, 2000)]

    state, _, _, released = _feed(_state(), frames)

    assert state.open is not None
    selection = state.open.selection
    chosen = selection.frames(3)
    assert [f.ts_ms for f in chosen] == [T0_MS, T0_MS + 8000, T0_MS + 18_000]
    assert selection.held_count <= 6
    assert len(released) == len(frames) - selection.held_count
    assert state.open.frame_count == len(frames)


def test_selection_memory_stays_bounded_for_a_very_long_exposure() -> None:
    frames = [_frame(offset, SIG_A, size=1000) for offset in range(0, 88_000, 2000)]

    state, _, _, _ = _feed(_state(), frames)

    assert state.open is not None
    assert state.open.selection.held_count <= 6
    assert state.open.selection.nbytes <= 6 * 1000
    middle = state.open.selection.frames(3)[1]
    assert abs(middle.ts_ms - (T0_MS + 43_000)) <= 12_000


def test_selection_with_two_frames_returns_both() -> None:
    state, _, _, _ = _feed(_state(), [_frame(0, SIG_A), _frame(2000, SIG_A)])

    assert state.open is not None
    assert [f.ts_ms for f in state.open.selection.frames(3)] == [T0_MS, T0_MS + 2000]


def test_late_frame_inside_the_open_exposure_is_evidence_not_a_boundary() -> None:
    state, _, _, _ = _feed(
        _state(), [_frame(0, SIG_A), _frame(2000, SIG_A), _frame(4000, SIG_A)]
    )

    state, closed, statuses, _ = _feed(state, [_frame(3000, SIG_B)])

    assert statuses == [AckStatus.ACCEPTED]
    assert closed == []
    assert state.open is not None
    assert state.open.frame_count == 4
    assert state.last_frame is not None and state.last_frame.ts_ms == T0_MS + 4000


def test_late_frame_outside_the_grace_or_exposure_is_discarded() -> None:
    state, _, _, _ = _feed(
        _state(), [_frame(10_000, SIG_A), _frame(12_000, SIG_A), _frame(16_000, SIG_A)]
    )

    _, _, statuses, released = _feed(
        state, [_frame(5000, SIG_A), _frame(12_500, SIG_A)]
    )

    assert statuses == [AckStatus.LATE_DISCARDED, AckStatus.LATE_DISCARDED]
    assert len(released) == 2


def test_transitions_never_mutate_the_previous_state() -> None:
    before = _state()
    sample, kept = _frame(0, SIG_A)

    result = apply_frame(
        before,
        sample,
        kept,
        config=CONFIG,
        segmenter=SEGMENTER,
        now=NOW,
        monotonic=100.0,
    )

    assert before.open is None and before.last_frame is None
    assert result.state is not before


def test_audio_is_buffered_with_capture_timestamps_and_a_watermark() -> None:
    chunk = _chunk(0)

    result = apply_audio(_state(), chunk, (_pcm(0, 5000),), (), now=NOW)

    assert result.status is AckStatus.ACCEPTED
    assert result.state.audio_watermark_ms == T0_MS + 5000
    assert [s.start_ms for s in result.state.pcm] == [T0_MS]
    assert result.released_bytes == 0


def test_audio_chunks_are_kept_sorted_by_capture_time() -> None:
    state = apply_audio(_state(), _chunk(5000), (_pcm(5000, 5000),), (), now=NOW).state

    state = apply_audio(state, _chunk(0), (_pcm(0, 5000),), (), now=NOW).state

    assert [s.start_ms for s in state.pcm] == [T0_MS, T0_MS + 5000]
    assert state.audio_watermark_ms == T0_MS + 10_000


def test_audio_notes_record_uncertified_intervals_without_samples() -> None:
    note = AudioNote(
        kind=AudioNoteKind.UNCERTIFIED, start_ms=T0_MS + 1800, end_ms=T0_MS + 5000
    )

    state = apply_audio(_state(), _chunk(0), (_pcm(0, 1800),), (note,), now=NOW).state

    assert state.notes == (note,)
    assert state.pcm[0].end_ms == T0_MS + 1800


def test_candidate_waits_for_its_final_audio_then_becomes_ready() -> None:
    state, closed, _, _ = _feed(
        _state(), [_frame(0, SIG_A), _frame(2000, SIG_A), _frame(4000, SIG_B)]
    )
    assert closed[0].audio_window == (T0_MS, T0_MS + 2000)

    state, ready = take_ready(state, monotonic=101.0, config=CONFIG)
    assert ready == ()

    state = apply_audio(state, _chunk(0), (_pcm(0, 5000),), (), now=NOW).state
    state, ready = take_ready(state, monotonic=101.5, config=CONFIG)

    assert [c.reel_id for c in ready] == [closed[0].reel_id]
    assert state.closed == ()
    assert state.finalized_until_ms == T0_MS + 2000


def test_candidate_proceeds_without_audio_after_the_final_wait_deadline() -> None:
    state, _, _, _ = _feed(
        _state(), [_frame(0, SIG_A), _frame(2000, SIG_A), _frame(4000, SIG_B)]
    )

    state, early = take_ready(state, monotonic=105.9, config=CONFIG)
    state, ready = take_ready(state, monotonic=106.0, config=CONFIG)

    assert early == ()
    assert len(ready) == 1


def test_candidate_with_no_audio_window_is_ready_immediately() -> None:
    state, _, _, _ = _feed(_state(), [_frame(0, SIG_A), _frame(2000, SIG_B)])

    _, ready = take_ready(state, monotonic=100.0, config=CONFIG)

    assert len(ready) == 1 and ready[0].audio_window is None


def test_audio_entirely_before_the_finalized_point_is_late() -> None:
    state, _, _, _ = _feed(
        _state(), [_frame(6000, SIG_A), _frame(8000, SIG_A), _frame(10_000, SIG_B)]
    )
    state, _ = take_ready(state, monotonic=200.0, config=CONFIG)

    result = apply_audio(state, _chunk(0), (_pcm(0, 5000),), (), now=NOW)

    assert result.status is AckStatus.LATE_DISCARDED
    assert result.state.pcm == ()
    assert result.released_bytes == 5000 * 16 * 2


def test_prune_releases_audio_that_no_open_work_can_use() -> None:
    state, _, _, _ = _feed(
        _state(), [_frame(0, SIG_A), _frame(6000, SIG_A), _frame(8000, SIG_B)]
    )
    state = apply_audio(state, _chunk(0), (_pcm(0, 5000),), (), now=NOW).state
    state = apply_audio(state, _chunk(5000), (_pcm(5000, 5000),), (), now=NOW).state
    state, ready = take_ready(state, monotonic=100.0, config=CONFIG)
    assert len(ready) == 1

    pruned, released_bytes = prune_audio(state)

    assert [s.start_ms for s in pruned.pcm] == [T0_MS + 5000]
    assert released_bytes == 5000 * 16 * 2


def test_session_end_closes_the_open_exposure_and_makes_everything_ready() -> None:
    state, _, _, _ = _feed(
        _state(), [_frame(0, SIG_A), _frame(2000, SIG_B), _frame(4000, SIG_B)]
    )

    state, closed = close_for_end(state, config=CONFIG, now=NOW, monotonic=100.0)
    state, ready = take_ready(state, monotonic=100.0, config=CONFIG)

    assert [c.close_reason for c in closed] == [CloseReason.SESSION_END]
    assert closed[0].audio_window == (T0_MS + 2000, T0_MS + 4000)
    assert len(ready) == 2
    assert state.open is None and state.ending is True


def test_order_sequence_numbers_are_attached_after_storage_allocates_them() -> None:
    state, closed, _, _ = _feed(_state(), [_frame(0, SIG_A), _frame(2000, SIG_B)])

    updated = with_order_seqs(state, {closed[0].reel_id: 7})

    assert updated.closed[0].order_seq == 7
    assert state.closed[0].order_seq == 0
