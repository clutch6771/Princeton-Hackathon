"""Frame signatures and the reel segmenter.

A hash change alone is never proof of a new reel: a boundary needs a sharp
whole-frame change plus corroborating evidence.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from judge.segmentation.frame_hash import (
    FrameSignature,
    compute_signature,
    hamming_distance,
)
from judge.segmentation.segmenter import (
    BoundaryEvidence,
    FrameSample,
    HashReelSegmenter,
    SegmenterConfig,
    TransitionKind,
)
from tests.support.builders import T0_MS
from tests.support.media import make_frame_image

pytestmark = pytest.mark.unit

CONFIG = SegmenterConfig(frame_threshold=20, overlay_threshold=12, gap_ms=8000)
SAME = FrameSignature(full=0, overlay=0)
SCENE_CHANGED = FrameSignature(full=(1 << 40) - 1, overlay=0)
OVERLAY_CHANGED = FrameSignature(full=0, overlay=(1 << 30) - 1)
BOTH_CHANGED = FrameSignature(full=(1 << 40) - 1, overlay=(1 << 30) - 1)


def _sample(
    ts_ms: int, signature: FrameSignature, transition_at_ms: int | None = None
) -> FrameSample:
    return FrameSample(
        event_id=uuid4(),
        ts_ms=ts_ms,
        signature=signature,
        transition_at_ms=transition_at_ms,
    )


def _signature(scene: int, overlay: int, variant: int = 0) -> FrameSignature:
    return compute_signature(
        make_frame_image(scene=scene, overlay=overlay, variant=variant)
    )


def test_hamming_distance_counts_differing_bits() -> None:
    assert hamming_distance(0b1010, 0b0110) == 2
    assert hamming_distance(7, 7) == 0


def test_signature_is_stable_for_the_same_scene_and_overlay() -> None:
    first, second = _signature(1, 1, variant=0), _signature(1, 1, variant=3)

    assert hamming_distance(first.full, second.full) < CONFIG.frame_threshold
    assert hamming_distance(first.overlay, second.overlay) < CONFIG.overlay_threshold


def test_signature_separates_scene_changes_from_overlay_changes() -> None:
    base = _signature(1, 1)
    cut = _signature(2, 1)
    new_reel = _signature(2, 2)

    assert hamming_distance(base.full, cut.full) >= CONFIG.frame_threshold
    assert hamming_distance(base.overlay, cut.overlay) < CONFIG.overlay_threshold
    assert hamming_distance(base.overlay, new_reel.overlay) >= (
        CONFIG.overlay_threshold
    )


def test_signature_does_not_depend_on_image_size() -> None:
    small = compute_signature(
        make_frame_image(scene=3, overlay=3, width=144, height=256)
    )
    large = compute_signature(
        make_frame_image(scene=3, overlay=3, width=288, height=512)
    )

    assert hamming_distance(small.full, large.full) < CONFIG.frame_threshold


def test_first_frame_of_a_session_is_a_start() -> None:
    assessment = HashReelSegmenter(CONFIG).assess(None, _sample(T0_MS, SAME))

    assert assessment.kind is TransitionKind.START


def test_similar_frames_are_the_same_reel() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(_sample(T0_MS, SAME), _sample(T0_MS + 2000, SAME))

    assert assessment.kind is TransitionKind.SAME
    assert assessment.evidence == ()


def test_scene_change_alone_is_an_in_reel_cut_not_a_boundary() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(
        _sample(T0_MS, SAME), _sample(T0_MS + 2000, SCENE_CHANGED)
    )

    assert assessment.kind is TransitionKind.CUT
    assert assessment.evidence == ()


def test_overlay_change_without_a_scene_change_is_not_a_boundary() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(
        _sample(T0_MS, SAME), _sample(T0_MS + 2000, OVERLAY_CHANGED)
    )

    assert assessment.kind is TransitionKind.SAME


def test_scene_change_with_overlay_change_is_a_boundary() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(
        _sample(T0_MS, SAME), _sample(T0_MS + 2000, BOTH_CHANGED)
    )

    assert assessment.kind is TransitionKind.BOUNDARY
    assert assessment.evidence == (BoundaryEvidence.OVERLAY_CHANGE,)


def test_scene_change_with_a_native_transition_hint_is_a_boundary() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(
        _sample(T0_MS, SAME),
        _sample(T0_MS + 2000, SCENE_CHANGED, transition_at_ms=T0_MS + 1200),
    )

    assert assessment.kind is TransitionKind.BOUNDARY
    assert assessment.evidence == (BoundaryEvidence.TRANSITION_HINT,)


def test_hint_and_overlay_evidence_are_both_reported() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(
        _sample(T0_MS, SAME),
        _sample(T0_MS + 2000, BOTH_CHANGED, transition_at_ms=T0_MS + 1200),
    )

    assert set(assessment.evidence) == {
        BoundaryEvidence.TRANSITION_HINT,
        BoundaryEvidence.OVERLAY_CHANGE,
    }


def test_transition_hint_without_a_frame_change_is_not_a_boundary() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(
        _sample(T0_MS, SAME),
        _sample(T0_MS + 2000, SAME, transition_at_ms=T0_MS + 1200),
    )

    assert assessment.kind is TransitionKind.SAME


def test_stale_transition_hint_before_the_previous_frame_is_ignored() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(
        _sample(T0_MS, SAME),
        _sample(T0_MS + 2000, SCENE_CHANGED, transition_at_ms=T0_MS - 500),
    )

    assert assessment.kind is TransitionKind.CUT


def test_long_frame_gap_is_a_boundary_even_when_frames_look_alike() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(_sample(T0_MS, SAME), _sample(T0_MS + 8001, SAME))

    assert assessment.kind is TransitionKind.BOUNDARY
    assert assessment.evidence == (BoundaryEvidence.FRAME_GAP,)


def test_gap_exactly_at_the_threshold_is_not_a_boundary() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(_sample(T0_MS, SAME), _sample(T0_MS + 8000, SAME))

    assert assessment.kind is TransitionKind.SAME


def test_assessment_reports_measured_distances() -> None:
    segmenter = HashReelSegmenter(CONFIG)

    assessment = segmenter.assess(
        _sample(T0_MS, SAME), _sample(T0_MS + 2000, BOTH_CHANGED)
    )

    assert assessment.full_distance == 40
    assert assessment.overlay_distance == 30
