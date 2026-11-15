"""Timestamped PCM held in volatile memory, and notes about what was dropped."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import numpy as np

PCM_SAMPLE_RATE: Final = 16_000
MS_PER_SECOND: Final = 1000


def samples_to_ms(sample_count: int, sample_rate: int = PCM_SAMPLE_RATE) -> int:
    """Duration of ``sample_count`` samples, in whole milliseconds."""
    return sample_count * MS_PER_SECOND // sample_rate


def ms_to_samples(duration_ms: int, sample_rate: int = PCM_SAMPLE_RATE) -> int:
    """Number of samples in ``duration_ms``."""
    return duration_ms * sample_rate // MS_PER_SECOND


@dataclass(frozen=True, slots=True, eq=False)
class PcmSegment:
    """Decoded, permitted mono int16 samples starting at a capture time."""

    start_ms: int
    samples: np.ndarray

    @property
    def end_ms(self) -> int:
        """Capture time just after the last sample."""
        return self.start_ms + samples_to_ms(len(self.samples))

    @property
    def nbytes(self) -> int:
        """Bytes of raw media this segment holds."""
        return int(self.samples.nbytes)


class AudioNoteKind(StrEnum):
    """Why audio for an interval is not available as samples."""

    UNCERTIFIED = "uncertified"
    PROTECTED = "protected"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class AudioNote:
    """Metadata-only record of an interval whose audio was not kept."""

    kind: AudioNoteKind
    start_ms: int
    end_ms: int
