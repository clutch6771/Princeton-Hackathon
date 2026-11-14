"""Perceptual signatures of a sampled frame.

Two 64-bit signatures are computed locally (no cloud call):

* ``full``: a difference hash of the whole frame. It changes sharply on any
  scene change, including hard cuts inside one reel.
* ``overlay``: the layout of bright overlay text in the lower band of the
  frame (creator name, caption). It tends to stay put while one reel is on
  screen and to change when another reel replaces it.

The band position and brightness levels are engineering heuristics. They have
not been tuned on real Instagram frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from PIL import Image

HASH_BITS: Final = 64
_DHASH_SIZE: Final = (9, 8)  # width, height -> 8 x 8 comparisons
OVERLAY_BAND_TOP: Final = 0.70
OVERLAY_BAND_BOTTOM: Final = 0.90
_OVERLAY_GRID: Final = (16, 4)  # width, height -> 64 cells
_BRIGHT_LEVEL: Final = 200
_CELL_DENSITY_LEVEL: Final = 20


@dataclass(frozen=True, slots=True)
class FrameSignature:
    """Whole-frame and overlay-band signatures of one frame."""

    full: int
    overlay: int


def hamming_distance(first: int, second: int) -> int:
    """Number of differing bits between two signatures."""
    return (first ^ second).bit_count()


def _bits_to_int(bits: np.ndarray) -> int:
    value = 0
    for bit in bits.astype(bool).ravel():
        value = (value << 1) | int(bit)
    return value


def _difference_hash(gray: Image.Image) -> int:
    small = np.asarray(
        gray.resize(_DHASH_SIZE, Image.Resampling.LANCZOS), dtype=np.int16
    )
    return _bits_to_int(small[:, 1:] > small[:, :-1])


def _overlay_hash(gray: Image.Image) -> int:
    width, height = gray.size
    top = int(height * OVERLAY_BAND_TOP)
    bottom = max(top + 1, int(height * OVERLAY_BAND_BOTTOM))
    band = np.asarray(gray.crop((0, top, width, bottom)), dtype=np.uint8)
    mask = Image.fromarray(np.where(band >= _BRIGHT_LEVEL, 255, 0).astype(np.uint8))
    density = np.asarray(mask.resize(_OVERLAY_GRID, Image.Resampling.BOX))
    return _bits_to_int(density >= _CELL_DENSITY_LEVEL)


def compute_signature(image: Image.Image) -> FrameSignature:
    """Compute both signatures of a decoded frame."""
    gray = image.convert("L")
    return FrameSignature(full=_difference_hash(gray), overlay=_overlay_hash(gray))
