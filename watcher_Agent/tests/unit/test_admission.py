"""Admission: payload limits and media sanity checks before anything is kept."""

from __future__ import annotations

from typing import Any

import pytest

from judge.contracts.capture import AudioChunk, FrameObservation
from judge.errors import (
    PayloadTooLargeError,
    UnsupportedMediaError,
    ValidationFailure,
)
from judge.ingest.admission import check_audio_payload, inspect_frame
from judge.segmentation.frame_hash import FrameSignature
from tests.support.builders import T0_MS, audio_meta, frame_meta
from tests.support.media import encode_aac, make_frame_jpeg, make_png, sine
from tests.support.settings import make_settings

pytestmark = pytest.mark.unit

SETTINGS = make_settings()


def _frame(**overrides: Any) -> FrameObservation:
    return FrameObservation.model_validate(frame_meta(**overrides))


def _audio(**overrides: Any) -> AudioChunk:
    return AudioChunk.model_validate(audio_meta(**overrides))


def test_valid_frame_yields_a_signature_and_its_real_dimensions() -> None:
    jpeg = make_frame_jpeg(scene=1, overlay=1)

    inspected = inspect_frame(jpeg, _frame(), SETTINGS)

    assert isinstance(inspected.signature, FrameSignature)
    assert (inspected.width, inspected.height) == (288, 512)


def test_frame_over_the_byte_limit_is_rejected_before_decoding() -> None:
    settings = make_settings(frame_max_bytes=1000)

    with pytest.raises(PayloadTooLargeError):
        inspect_frame(make_frame_jpeg(scene=1, overlay=1), _frame(), settings)


@pytest.mark.parametrize(
    "payload",
    [make_png(), b"", b"not an image at all", b"\xff\xd8\xff\xe0" + b"\x00" * 64],
)
def test_non_jpeg_or_undecodable_frames_are_rejected(payload: bytes) -> None:
    with pytest.raises(UnsupportedMediaError):
        inspect_frame(payload, _frame(), SETTINGS)


def test_truncated_jpeg_is_rejected() -> None:
    jpeg = make_frame_jpeg(scene=1, overlay=1)

    with pytest.raises(UnsupportedMediaError):
        inspect_frame(jpeg[: len(jpeg) // 3], _frame(), SETTINGS)


def test_declared_dimensions_must_match_the_image() -> None:
    jpeg = make_frame_jpeg(scene=1, overlay=1)

    with pytest.raises(ValidationFailure):
        inspect_frame(jpeg, _frame(width=512, height=288), SETTINGS)


def test_frame_longer_than_the_512_px_edge_is_rejected() -> None:
    jpeg = make_frame_jpeg(scene=1, overlay=1, width=338, height=600)

    with pytest.raises(ValidationFailure):
        inspect_frame(jpeg, _frame(width=338, height=600), SETTINGS)


def test_valid_adts_chunk_passes() -> None:
    check_audio_payload(encode_aac(sine(5.0)), _audio(), SETTINGS)


def test_valid_mp4_chunk_passes() -> None:
    payload = encode_aac(sine(1.0), container="mp4")

    check_audio_payload(payload, _audio(container="mp4"), SETTINGS)


def test_audio_over_the_byte_limit_is_rejected() -> None:
    settings = make_settings(audio_chunk_max_bytes=100)

    with pytest.raises(PayloadTooLargeError):
        check_audio_payload(encode_aac(sine(5.0)), _audio(), settings)


def test_audio_chunk_longer_than_the_maximum_span_is_rejected() -> None:
    chunk = _audio(end_ms=T0_MS + 6001, eligible=[])

    with pytest.raises(ValidationFailure):
        check_audio_payload(encode_aac(sine(1.0)), chunk, SETTINGS)


@pytest.mark.parametrize("container", ["adts", "mp4"])
def test_payload_that_does_not_match_its_declared_container_is_rejected(
    container: str,
) -> None:
    with pytest.raises(UnsupportedMediaError):
        check_audio_payload(b"RIFF....WAVEfmt ", _audio(container=container), SETTINGS)


def test_available_audio_requires_a_payload() -> None:
    with pytest.raises(ValidationFailure):
        check_audio_payload(b"", _audio(), SETTINGS)


@pytest.mark.parametrize("state", ["protected", "unavailable"])
def test_withheld_audio_needs_no_payload(state: str) -> None:
    check_audio_payload(b"", _audio(capture_state=state, eligible=[]), SETTINGS)
