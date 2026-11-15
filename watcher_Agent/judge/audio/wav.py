"""Encode PCM as a WAV clip in memory (the format sent to the model)."""

from __future__ import annotations

import io
import wave

import numpy as np

_SAMPLE_WIDTH_BYTES = 2
_MONO = 1


def pcm_to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """Return a 16-bit mono WAV file as bytes. Nothing touches the disk."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(_MONO)
        clip.setsampwidth(_SAMPLE_WIDTH_BYTES)
        clip.setframerate(sample_rate)
        clip.writeframes(np.ascontiguousarray(samples, dtype="<i2").tobytes())
    return buffer.getvalue()
