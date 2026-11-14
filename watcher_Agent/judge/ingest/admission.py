"""Admission checks for uploaded media.

These run before any byte is kept: size limits, container sanity, real image
dimensions. Decoding happens from memory only. Error messages name the rule
that failed and never echo payload content.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Final

from PIL import Image, UnidentifiedImageError

from judge.config import Settings
from judge.contracts.capture import (
    AudioCaptureState,
    AudioChunk,
    AudioContainer,
    FrameObservation,
)
from judge.errors import (
    PayloadTooLargeError,
    UnsupportedMediaError,
    ValidationFailure,
)
from judge.segmentation.frame_hash import FrameSignature, compute_signature

_JPEG_MAGIC: Final = b"\xff\xd8\xff"
_ADTS_SYNC_BYTE: Final = 0xFF
_ADTS_SYNC_MASK: Final = 0xF0
_MP4_FTYP_OFFSET: Final = 4
_MP4_FTYP: Final = b"ftyp"
_MAX_PIXELS: Final = 4096 * 4096
_MS: Final = 1000


@dataclass(frozen=True, slots=True)
class InspectedFrame:
    """A frame that passed admission, with its locally computed signature."""

    signature: FrameSignature
    width: int
    height: int


def _open_jpeg(jpeg: bytes) -> Image.Image:
    if not jpeg.startswith(_JPEG_MAGIC):
        msg = "frame is not a JPEG"
        raise UnsupportedMediaError(msg)
    try:
        image = Image.open(io.BytesIO(jpeg))
        if image.format != "JPEG" or image.width * image.height > _MAX_PIXELS:
            msg = "frame is not an acceptable JPEG"
            raise UnsupportedMediaError(msg)
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        msg = "frame could not be decoded as a JPEG"
        raise UnsupportedMediaError(msg) from exc
    return image


def inspect_frame(
    jpeg: bytes, meta: FrameObservation, settings: Settings
) -> InspectedFrame:
    """Validate one uploaded frame and compute its signature (CPU-bound)."""
    if len(jpeg) > settings.frame_max_bytes:
        msg = f"frame exceeds {settings.frame_max_bytes} bytes"
        raise PayloadTooLargeError(msg)
    image = _open_jpeg(jpeg)
    if (image.width, image.height) != (meta.width, meta.height):
        msg = "declared frame dimensions do not match the image"
        raise ValidationFailure(msg)
    if max(image.width, image.height) > settings.frame_max_long_edge_px:
        msg = f"frame long edge exceeds {settings.frame_max_long_edge_px} px"
        raise ValidationFailure(msg)
    return InspectedFrame(
        signature=compute_signature(image), width=image.width, height=image.height
    )


def _matches_container(payload: bytes, container: AudioContainer) -> bool:
    if container is AudioContainer.ADTS:
        return (
            len(payload) >= 2
            and payload[0] == _ADTS_SYNC_BYTE
            and payload[1] & _ADTS_SYNC_MASK == _ADTS_SYNC_MASK
        )
    start, end = _MP4_FTYP_OFFSET, _MP4_FTYP_OFFSET + len(_MP4_FTYP)
    return payload[start:end] == _MP4_FTYP


def check_audio_payload(payload: bytes, meta: AudioChunk, settings: Settings) -> None:
    """Validate one uploaded audio chunk before it is decoded."""
    if len(payload) > settings.audio_chunk_max_bytes:
        msg = f"audio chunk exceeds {settings.audio_chunk_max_bytes} bytes"
        raise PayloadTooLargeError(msg)
    span_ms = meta.capture_end_ms - meta.capture_start_ms
    if span_ms > settings.audio_chunk_max_seconds * _MS:
        msg = f"audio chunk spans more than {settings.audio_chunk_max_seconds} s"
        raise ValidationFailure(msg)
    if meta.capture_state is not AudioCaptureState.AVAILABLE:
        return
    if not payload:
        msg = "audio payload is required when capture_state is 'available'"
        raise ValidationFailure(msg)
    if not _matches_container(payload, meta.container):
        msg = "audio payload does not match its declared container"
        raise UnsupportedMediaError(msg)
