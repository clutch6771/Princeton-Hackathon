"""Audio: codec adapter, timestamp-based assembly, WAV output, preparation.

AAC container bytes are never concatenated. Every chunk is decoded on its
own, cut by capture timestamps, and only permitted samples are joined.
"""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from judge.audio.assembler import PcmAudioAssembler
from judge.audio.codec import PyAvAacDecoder, trim_decoded
from judge.audio.wav import pcm_to_wav
from judge.contracts.candidate import AudioStatus
from judge.contracts.capture import AudioContainer
from judge.errors import AudioDecodeError
from judge.ingest.session_state import AudioNote, AudioNoteKind, PcmSegment
from judge.transcriber.prepare import prepare_audio
from tests.support.builders import T0_MS
from tests.support.media import (
    FFMPEG_AAC_PRIMING_SAMPLES,
    SAMPLE_RATE,
    encode_aac,
    ramp,
    silence,
    sine,
)

pytestmark = pytest.mark.unit

ASSEMBLER = PcmAudioAssembler(sample_rate=SAMPLE_RATE, complete_ratio=0.8)


def _segment(offset_ms: int, samples: np.ndarray) -> PcmSegment:
    return PcmSegment(start_ms=T0_MS + offset_ms, samples=samples)


def _note(kind: AudioNoteKind, start: int, end: int) -> AudioNote:
    return AudioNote(kind=kind, start_ms=T0_MS + start, end_ms=T0_MS + end)


def _window(start: int, end: int) -> tuple[int, int]:
    return (T0_MS + start, T0_MS + end)


# --- codec adapter ----------------------------------------------------------


def test_decoder_returns_16k_mono_int16_pcm_from_adts() -> None:
    payload = encode_aac(sine(5.0))

    pcm = PyAvAacDecoder().decode(payload, AudioContainer.ADTS)

    assert pcm.dtype == np.int16
    assert pcm.ndim == 1
    assert len(pcm) >= 5 * SAMPLE_RATE


def test_decoder_supports_the_mp4_container() -> None:
    payload = encode_aac(sine(1.0), container="mp4")

    pcm = PyAvAacDecoder().decode(payload, AudioContainer.MP4)

    assert len(pcm) >= SAMPLE_RATE


def test_decoded_tone_survives_the_round_trip() -> None:
    original = sine(1.0)
    decoded = PyAvAacDecoder().decode(encode_aac(original), AudioContainer.ADTS)

    trimmed = trim_decoded(
        decoded, priming_samples=FFMPEG_AAC_PRIMING_SAMPLES, expected_samples=16_000
    )

    assert len(trimmed) == 16_000
    assert float(np.sqrt(np.mean(trimmed.astype(float) ** 2))) > 5000


@pytest.mark.parametrize("payload", [b"", b"\x00\x01not-aac" * 40])
def test_decoder_reports_corrupt_audio(payload: bytes) -> None:
    with pytest.raises(AudioDecodeError):
        PyAvAacDecoder().decode(payload, AudioContainer.ADTS)


def test_trim_removes_priming_and_padding() -> None:
    decoded = (np.arange(81_920) % 30_000).astype(np.int16)

    trimmed = trim_decoded(decoded, priming_samples=1024, expected_samples=80_000)

    assert len(trimmed) == 80_000
    assert trimmed[0] == 1024


def test_trim_keeps_shorter_audio_without_padding_it() -> None:
    decoded = np.arange(2000, dtype=np.int16)

    trimmed = trim_decoded(decoded, priming_samples=1024, expected_samples=80_000)

    assert len(trimmed) == 976


def test_trim_returns_a_read_only_copy() -> None:
    decoded = np.arange(4000, dtype=np.int16)

    trimmed = trim_decoded(decoded, priming_samples=0, expected_samples=4000)

    assert trimmed.flags.writeable is False


# --- assembly by capture timestamps ----------------------------------------


def test_chunk_crossing_two_reels_is_split_and_the_boundary_is_discarded() -> None:
    # One 5 s chunk. Reel A ends at +2000 (its last frame); reel B's first
    # frame is at +4000. The uncertain interval between belongs to neither.
    chunk = _segment(0, ramp(5.0))

    reel_a = ASSEMBLER.assemble([chunk], [], _window(0, 2000))
    reel_b = ASSEMBLER.assemble([chunk], [], _window(4000, 5000))

    assert len(reel_a.samples) == 2 * SAMPLE_RATE
    assert len(reel_b.samples) == 1 * SAMPLE_RATE
    assert reel_a.samples[0] == 0
    assert reel_a.samples[-1] == (2 * SAMPLE_RATE - 1) % 30_000
    assert reel_b.samples[0] == (4 * SAMPLE_RATE) % 30_000
    used = len(reel_a.samples) + len(reel_b.samples)
    assert used == 3 * SAMPLE_RATE < len(chunk.samples)


def test_chunk_crossing_into_a_private_screen_keeps_only_certified_samples() -> None:
    # The native gate certified only the first 1.8 s (Reel); the rest was a DM.
    certified = _segment(0, ramp(1.8))
    uncertified = _note(AudioNoteKind.UNCERTIFIED, 1800, 5000)

    assembled = ASSEMBLER.assemble([certified], [uncertified], _window(0, 4000))

    assert len(assembled.samples) == int(1.8 * SAMPLE_RATE)
    assert assembled.kept_ms == 1800
    assert assembled.uncertified_ms == 2200
    assert assembled.missing_ms == 0
    assert assembled.status is AudioStatus.PARTIAL


def test_segments_from_separate_chunks_are_joined_in_capture_order() -> None:
    second = _segment(5000, ramp(5.0) + 1)
    first = _segment(0, ramp(5.0))

    assembled = ASSEMBLER.assemble([second, first], [], _window(3000, 7000))

    assert len(assembled.samples) == 4 * SAMPLE_RATE
    assert assembled.samples[0] == (3 * SAMPLE_RATE) % 30_000
    assert assembled.samples[2 * SAMPLE_RATE] == 1
    assert assembled.status is AudioStatus.COMPLETE


def test_overlapping_segments_are_not_duplicated() -> None:
    first = _segment(0, ramp(5.0))
    overlapping = _segment(4000, ramp(5.0))

    assembled = ASSEMBLER.assemble([first, overlapping], [], _window(0, 9000))

    assert len(assembled.samples) == 9 * SAMPLE_RATE
    assert assembled.kept_ms == 9000


def test_gap_between_chunks_is_reported_as_missing() -> None:
    first = _segment(0, ramp(2.0))
    later = _segment(6000, ramp(2.0))

    assembled = ASSEMBLER.assemble([first, later], [], _window(0, 8000))

    assert assembled.kept_ms == 4000
    assert assembled.missing_ms == 4000
    assert assembled.status is AudioStatus.PARTIAL


def test_no_audio_at_all_is_missing_not_silence() -> None:
    assembled = ASSEMBLER.assemble([], [], _window(0, 6000))

    assert len(assembled.samples) == 0
    assert assembled.status is AudioStatus.MISSING
    assert assembled.missing_ms == 6000


def test_empty_window_is_missing() -> None:
    assembled = ASSEMBLER.assemble([_segment(0, ramp(5.0))], [], None)

    assert assembled.status is AudioStatus.MISSING
    assert assembled.window_ms == 0 and len(assembled.samples) == 0


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (AudioNoteKind.UNCERTIFIED, AudioStatus.UNCERTIFIED),
        (AudioNoteKind.PROTECTED, AudioStatus.PROTECTED),
        (AudioNoteKind.CORRUPT, AudioStatus.CORRUPT),
    ],
)
def test_nothing_kept_reports_why_from_evidence(
    kind: AudioNoteKind, expected: AudioStatus
) -> None:
    assembled = ASSEMBLER.assemble([], [_note(kind, 0, 6000)], _window(0, 6000))

    assert assembled.kept_ms == 0
    assert assembled.status is expected


def test_silent_audio_is_complete_audio_not_protected_audio() -> None:
    assembled = ASSEMBLER.assemble([_segment(0, silence(5.0))], [], _window(0, 5000))

    assert assembled.status is AudioStatus.COMPLETE
    assert assembled.protected_ms == 0


def test_coverage_at_the_complete_ratio_counts_as_complete() -> None:
    assembled = ASSEMBLER.assemble([_segment(0, ramp(4.0))], [], _window(0, 5000))

    assert assembled.status is AudioStatus.COMPLETE


def test_assembled_samples_are_read_only() -> None:
    assembled = ASSEMBLER.assemble([_segment(0, ramp(1.0))], [], _window(0, 1000))

    assert assembled.samples.flags.writeable is False


# --- WAV output and preparation --------------------------------------------


def test_wav_is_a_valid_16k_mono_16_bit_clip_built_in_memory() -> None:
    data = pcm_to_wav(sine(1.5), SAMPLE_RATE)

    with wave.open(io.BytesIO(data), "rb") as clip:
        assert clip.getframerate() == SAMPLE_RATE
        assert clip.getnchannels() == 1
        assert clip.getsampwidth() == 2
        assert clip.getnframes() == int(1.5 * SAMPLE_RATE)
    assert data[:4] == b"RIFF"


def test_prepared_audio_supplies_a_clip_when_enough_was_kept() -> None:
    assembled = ASSEMBLER.assemble([_segment(0, sine(5.0))], [], _window(0, 5000))

    prepared = prepare_audio(assembled, sample_rate=SAMPLE_RATE, min_ms_for_model=500)

    assert prepared.supplied is True
    assert prepared.wav is not None and prepared.wav[:4] == b"RIFF"
    assert prepared.status is AudioStatus.COMPLETE
    assert prepared.coverage_ratio == pytest.approx(1.0)


def test_prepared_audio_withholds_a_clip_that_is_too_short_to_judge() -> None:
    assembled = ASSEMBLER.assemble([_segment(0, sine(0.3))], [], _window(0, 6000))

    prepared = prepare_audio(assembled, sample_rate=SAMPLE_RATE, min_ms_for_model=500)

    assert prepared.supplied is False
    assert prepared.wav is None
    assert prepared.status is AudioStatus.PARTIAL


def test_prepared_audio_without_any_audio_supplies_nothing() -> None:
    assembled = ASSEMBLER.assemble([], [], _window(0, 6000))

    prepared = prepare_audio(assembled, sample_rate=SAMPLE_RATE, min_ms_for_model=500)

    assert prepared.supplied is False
    assert prepared.nbytes == 0
