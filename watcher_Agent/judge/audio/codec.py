"""AAC codec adapter: decode one uploaded chunk to PCM, entirely in memory.

Each chunk is decoded on its own. AAC container bytes from different chunks
are never concatenated. Decoding reads from a ``BytesIO``; nothing is written
to disk.
"""

from __future__ import annotations

import io
from typing import Final, Protocol

import av
import numpy as np

from judge.audio.pcm import PCM_SAMPLE_RATE
from judge.contracts.capture import AudioContainer
from judge.errors import AudioDecodeError

_FORMAT_BY_CONTAINER: Final = {
    AudioContainer.ADTS: "aac",
    AudioContainer.MP4: "mp4",
}


class AacDecoder(Protocol):
    """Capability: decode one AAC chunk to 16 kHz mono int16 PCM.

    Blocking and CPU-bound; call it from a worker thread. Raises
    ``AudioDecodeError`` for corrupt or unexpected input. No side effects.
    """

    def decode(self, payload: bytes, container: AudioContainer) -> np.ndarray:
        """Return every decoded sample, including encoder priming."""
        ...


class PyAvAacDecoder:
    """Decoder backed by PyAV (bundled FFmpeg)."""

    def decode(self, payload: bytes, container: AudioContainer) -> np.ndarray:
        if not payload:
            msg = "empty audio payload"
            raise AudioDecodeError(msg)
        try:
            return self._decode(payload, _FORMAT_BY_CONTAINER[container])
        except (av.error.FFmpegError, ValueError, IndexError, OSError) as exc:
            msg = f"audio chunk could not be decoded ({type(exc).__name__})"
            raise AudioDecodeError(msg) from exc

    def _decode(self, payload: bytes, input_format: str) -> np.ndarray:
        chunks: list[np.ndarray] = []
        with av.open(io.BytesIO(payload), mode="r", format=input_format) as source:
            stream = source.streams.audio[0]
            if stream.codec_context.name != "aac":
                msg = "audio stream is not AAC"
                raise AudioDecodeError(msg)
            resampler = av.AudioResampler(
                format="s16", layout="mono", rate=PCM_SAMPLE_RATE
            )
            for frame in source.decode(stream):
                chunks.extend(
                    out.to_ndarray().reshape(-1) for out in resampler.resample(frame)
                )
            chunks.extend(
                out.to_ndarray().reshape(-1) for out in resampler.resample(None)
            )
        if not chunks:
            msg = "audio chunk contained no decodable samples"
            raise AudioDecodeError(msg)
        return np.concatenate(chunks).astype(np.int16, copy=False)


def trim_decoded(
    decoded: np.ndarray, *, priming_samples: int, expected_samples: int
) -> np.ndarray:
    """Drop decoder priming and trailing padding; return a read-only copy.

    The first kept sample corresponds to the chunk's ``capture_start_ms``.
    Audio that is shorter than expected is kept as is, never padded.
    """
    start = min(max(priming_samples, 0), len(decoded))
    trimmed = decoded[start : start + max(expected_samples, 0)].copy()
    trimmed.setflags(write=False)
    return trimmed
