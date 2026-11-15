"""Synthetic media, generated in memory. Nothing here is a real recording.

Frames imitate the two properties the segmenter relies on: a background that
changes with the scene, and bright overlay "text" in the lower band that
stays put while one reel is on screen.
"""

from __future__ import annotations

import io

import av
import numpy as np
from PIL import Image

SAMPLE_RATE = 16_000
FFMPEG_AAC_PRIMING_SAMPLES = 1024
_BLOCK_GRID = (8, 6)  # rows, columns of background blocks
_OVERLAY_GRID = (4, 16)  # rows, columns of overlay cells
_OVERLAY_TOP = 0.70
_OVERLAY_BOTTOM = 0.90


def make_frame_image(
    *,
    scene: int,
    overlay: int,
    variant: int = 0,
    width: int = 288,
    height: int = 512,
) -> Image.Image:
    """A portrait frame: blocky background plus bright overlay text rows."""
    rng = np.random.default_rng(scene)
    blocks = rng.integers(20, 170, size=_BLOCK_GRID, dtype=np.uint8)
    background = Image.fromarray(blocks, mode="L").resize(
        (width, height), Image.Resampling.NEAREST
    )
    pixels = np.asarray(background, dtype=np.int16) + (variant % 4)
    canvas = np.clip(pixels, 0, 255).astype(np.uint8)

    # Overlay "text": a 4 x 16 grid of bright cells unique to each overlay id,
    # so two different overlays differ in about half of the cells.
    cells = np.random.default_rng(10_000 + overlay).random(_OVERLAY_GRID) < 0.5
    top, bottom = int(height * _OVERLAY_TOP), int(height * _OVERLAY_BOTTOM)
    cell_height = (bottom - top) // _OVERLAY_GRID[0]
    cell_width = width // _OVERLAY_GRID[1]
    for row, column in zip(*np.nonzero(cells), strict=True):
        y0 = top + int(row) * cell_height
        x0 = int(column) * cell_width
        canvas[y0 + 2 : y0 + cell_height - 2, x0 + 1 : x0 + cell_width - 1] = 250
    return Image.fromarray(canvas, mode="L").convert("RGB")


def make_frame_jpeg(
    *,
    scene: int,
    overlay: int,
    variant: int = 0,
    width: int = 288,
    height: int = 512,
    quality: int = 60,
) -> bytes:
    """JPEG bytes of a synthetic frame."""
    image = make_frame_image(
        scene=scene, overlay=overlay, variant=variant, width=width, height=height
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def make_png(width: int = 64, height: int = 64) -> bytes:
    """A PNG, used to prove non-JPEG uploads are rejected."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def sine(seconds: float, *, frequency: float = 440.0) -> np.ndarray:
    """A sine tone as 16 kHz mono int16 PCM."""
    count = int(round(seconds * SAMPLE_RATE))
    t = np.arange(count) / SAMPLE_RATE
    return (0.4 * 32767 * np.sin(2 * np.pi * frequency * t)).astype(np.int16)


def silence(seconds: float) -> np.ndarray:
    """Digital silence as 16 kHz mono int16 PCM."""
    return np.zeros(int(round(seconds * SAMPLE_RATE)), dtype=np.int16)


def ramp(seconds: float) -> np.ndarray:
    """PCM whose value encodes its own sample index (mod 30000)."""
    count = int(round(seconds * SAMPLE_RATE))
    return (np.arange(count) % 30_000).astype(np.int16)


def encode_aac(pcm: np.ndarray, *, container: str = "adts") -> bytes:
    """Encode PCM as one independently decodable AAC chunk, in memory."""
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format=container) as output:
        stream = output.add_stream("aac", rate=SAMPLE_RATE)
        stream.layout = "mono"
        stream.bit_rate = 32_000
        frame = av.AudioFrame.from_ndarray(
            pcm.reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = SAMPLE_RATE
        resampler = av.AudioResampler(
            format=stream.format, layout="mono", rate=SAMPLE_RATE
        )
        for converted in [*resampler.resample(frame), *resampler.resample(None)]:
            for packet in stream.encode(converted):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    return buffer.getvalue()
