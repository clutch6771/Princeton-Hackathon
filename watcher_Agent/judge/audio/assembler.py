"""Assemble the permitted audio of one exposure by capture timestamps.

A five-second chunk may cross two reels, a Reel-to-DM transition, or an
uncertain interval. Samples are cut by capture time to the exposure's audio
window; only permitted samples are joined into one in-memory clip. An entire
cross-boundary chunk is never assigned to one reel.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from judge.audio.pcm import (
    PCM_SAMPLE_RATE,
    AudioNote,
    AudioNoteKind,
    PcmSegment,
    ms_to_samples,
    samples_to_ms,
)
from judge.contracts.candidate import AudioStatus

_STATUS_BY_NOTE: dict[AudioNoteKind, AudioStatus] = {
    AudioNoteKind.PROTECTED: AudioStatus.PROTECTED,
    AudioNoteKind.CORRUPT: AudioStatus.CORRUPT,
    AudioNoteKind.UNCERTIFIED: AudioStatus.UNCERTIFIED,
}


@dataclass(frozen=True, slots=True, eq=False)
class AssembledAudio:
    """The audio of one exposure plus an honest account of what is missing."""

    samples: np.ndarray
    window_ms: int
    kept_ms: int
    uncertified_ms: int
    protected_ms: int
    corrupt_ms: int
    missing_ms: int
    status: AudioStatus


class AudioAssembler(Protocol):
    """Capability: join permitted PCM for one exposure.

    Pure and synchronous; no I/O, no side effects, never raises for valid
    input. ``window`` is ``None`` when no audio can be attributed.
    """

    def assemble(
        self,
        segments: Sequence[PcmSegment],
        notes: Sequence[AudioNote],
        window: tuple[int, int] | None,
    ) -> AssembledAudio:
        """Return the assembled clip and its coverage."""
        ...


def _overlap_ms(
    notes: Sequence[AudioNote], kind: AudioNoteKind, window: tuple[int, int]
) -> int:
    start, end = window
    spans = sorted(
        (max(n.start_ms, start), min(n.end_ms, end)) for n in notes if n.kind is kind
    )
    total, cursor = 0, start
    for span_start, span_end in spans:
        span_start = max(span_start, cursor)
        if span_end > span_start:
            total += span_end - span_start
            cursor = span_end
    return total


class PcmAudioAssembler:
    """Timestamp-based assembler for 16 kHz mono PCM."""

    def __init__(
        self, *, sample_rate: int = PCM_SAMPLE_RATE, complete_ratio: float = 0.8
    ) -> None:
        self._sample_rate = sample_rate
        self._complete_ratio = complete_ratio

    def assemble(
        self,
        segments: Sequence[PcmSegment],
        notes: Sequence[AudioNote],
        window: tuple[int, int] | None,
    ) -> AssembledAudio:
        if window is None or window[1] <= window[0]:
            return self._result(np.zeros(0, dtype=np.int16), 0, {}, AudioStatus.MISSING)
        pieces = self._cut(segments, window)
        samples = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.int16)
        dropped = {kind: _overlap_ms(notes, kind, window) for kind in AudioNoteKind}
        window_ms = window[1] - window[0]
        kept_ms = min(window_ms, samples_to_ms(len(samples), self._sample_rate))
        status = self._status(kept_ms, window_ms, dropped)
        return self._result(samples, window_ms, dropped, status)

    def _cut(
        self, segments: Sequence[PcmSegment], window: tuple[int, int]
    ) -> list[np.ndarray]:
        start, end = window
        cursor = start
        pieces: list[np.ndarray] = []
        for segment in sorted(segments, key=lambda s: s.start_ms):
            piece_start = max(segment.start_ms, cursor)
            piece_end = min(segment.end_ms, end)
            if piece_end <= piece_start:
                continue
            first = ms_to_samples(piece_start - segment.start_ms, self._sample_rate)
            last = ms_to_samples(piece_end - segment.start_ms, self._sample_rate)
            pieces.append(segment.samples[first:last])
            cursor = piece_end
        return pieces

    def _status(
        self, kept_ms: int, window_ms: int, dropped: dict[AudioNoteKind, int]
    ) -> AudioStatus:
        if kept_ms >= self._complete_ratio * window_ms:
            return AudioStatus.COMPLETE
        if kept_ms > 0:
            return AudioStatus.PARTIAL
        # Nothing kept: say why, from evidence only (never from silence).
        for kind, status in _STATUS_BY_NOTE.items():
            if dropped.get(kind, 0) > 0 and dropped[kind] == max(dropped.values()):
                return status
        return AudioStatus.MISSING

    def _result(
        self,
        samples: np.ndarray,
        window_ms: int,
        dropped: dict[AudioNoteKind, int],
        status: AudioStatus,
    ) -> AssembledAudio:
        frozen = samples.copy()
        frozen.setflags(write=False)
        kept_ms = min(window_ms, samples_to_ms(len(frozen), self._sample_rate))
        uncertified = dropped.get(AudioNoteKind.UNCERTIFIED, 0)
        protected = dropped.get(AudioNoteKind.PROTECTED, 0)
        corrupt = dropped.get(AudioNoteKind.CORRUPT, 0)
        missing = max(0, window_ms - kept_ms - uncertified - protected - corrupt)
        return AssembledAudio(
            samples=frozen,
            window_ms=window_ms,
            kept_ms=kept_ms,
            uncertified_ms=uncertified,
            protected_ms=protected,
            corrupt_ms=corrupt,
            missing_ms=missing,
            status=status,
        )
