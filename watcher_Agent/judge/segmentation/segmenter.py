"""Reel segmenter: decide whether a new frame starts a new exposure.

A hash change alone is not proof of a new reel (reels contain hard cuts). A
boundary needs a sharp whole-frame change *and* corroborating evidence:

* a transition the native adapter observed since the previous frame, or
* a change of the overlay text layout.

A long gap without eligible frames is a boundary by itself: the exposure can
no longer be assumed continuous.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from judge.segmentation.frame_hash import FrameSignature, hamming_distance


class TransitionKind(StrEnum):
    """Relationship between a frame and the one before it."""

    START = "start"
    SAME = "same"
    CUT = "cut"
    BOUNDARY = "boundary"


class BoundaryEvidence(StrEnum):
    """Evidence that corroborated a boundary."""

    TRANSITION_HINT = "transition_hint"
    OVERLAY_CHANGE = "overlay_change"
    FRAME_GAP = "frame_gap"


@dataclass(frozen=True, slots=True)
class FrameSample:
    """What segmentation needs to know about one eligible frame."""

    event_id: UUID
    ts_ms: int
    signature: FrameSignature
    transition_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TransitionAssessment:
    """Result of comparing a frame with its predecessor."""

    kind: TransitionKind
    evidence: tuple[BoundaryEvidence, ...] = ()
    full_distance: int = 0
    overlay_distance: int = 0


@dataclass(frozen=True, slots=True)
class SegmenterConfig:
    """Tunable thresholds (engineering settings, not PRD values)."""

    frame_threshold: int
    overlay_threshold: int
    gap_ms: int


class ReelSegmenter(Protocol):
    """Capability: assess the transition between two consecutive frames."""

    def assess(
        self, previous: FrameSample | None, current: FrameSample
    ) -> TransitionAssessment:
        """Pure and synchronous. Never raises for valid samples."""
        ...


class HashReelSegmenter:
    """Segmenter based on frame signatures plus temporal evidence."""

    def __init__(self, config: SegmenterConfig) -> None:
        self._config = config

    def assess(
        self, previous: FrameSample | None, current: FrameSample
    ) -> TransitionAssessment:
        if previous is None:
            return TransitionAssessment(kind=TransitionKind.START)
        full = hamming_distance(previous.signature.full, current.signature.full)
        overlay = hamming_distance(
            previous.signature.overlay, current.signature.overlay
        )
        if current.ts_ms - previous.ts_ms > self._config.gap_ms:
            return TransitionAssessment(
                TransitionKind.BOUNDARY, (BoundaryEvidence.FRAME_GAP,), full, overlay
            )
        if full < self._config.frame_threshold:
            return TransitionAssessment(TransitionKind.SAME, (), full, overlay)
        evidence = self._corroboration(previous, current, overlay)
        kind = TransitionKind.BOUNDARY if evidence else TransitionKind.CUT
        return TransitionAssessment(kind, evidence, full, overlay)

    def _corroboration(
        self, previous: FrameSample, current: FrameSample, overlay_distance: int
    ) -> tuple[BoundaryEvidence, ...]:
        evidence: list[BoundaryEvidence] = []
        hint = current.transition_at_ms
        if hint is not None and previous.ts_ms < hint <= current.ts_ms:
            evidence.append(BoundaryEvidence.TRANSITION_HINT)
        if overlay_distance >= self._config.overlay_threshold:
            evidence.append(BoundaryEvidence.OVERLAY_CHANGE)
        return tuple(evidence)
