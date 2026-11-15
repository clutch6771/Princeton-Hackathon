"""Transcript schema, evidence-backed status, and modality metadata.

The model reports what it heard in the clip it was given. The final status is
decided here from evidence:

* No clip supplied: the status comes from why (missing, protected or
  privacy-withheld, corrupt). It is never inferred from silence.
* Clip supplied but covering only part of the exposure: ``partial``. Silence
  in part of a reel is not proof that the rest was silent or harmless.
* Otherwise: the model's observation (speech, music, silence,
  unintelligible).

Version 1 requests no transcript timestamps. If they are added, their origin
is the start of the assembled clip, and they are approximate because
discarded intervals are removed from the clip.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field

from judge.contracts.base import Contract
from judge.contracts.candidate import AudioStatus
from judge.contracts.understanding import AudioObservation, TranscriptStatus
from judge.errors import OutputValidationError, ReelErrorCode
from judge.transcriber.prepare import PreparedAudio

_STATUS_BY_OBSERVATION: Final = {
    AudioObservation.SPEECH: TranscriptStatus.SPEECH,
    AudioObservation.NON_SPEECH_MUSIC: TranscriptStatus.NON_SPEECH_MUSIC,
    AudioObservation.SILENCE: TranscriptStatus.SILENCE,
    AudioObservation.UNINTELLIGIBLE: TranscriptStatus.UNINTELLIGIBLE,
}
_STATUS_WITHOUT_CLIP: Final = {
    AudioStatus.CORRUPT: TranscriptStatus.CORRUPT,
    AudioStatus.PROTECTED: TranscriptStatus.PROTECTED_UNAVAILABLE,
    AudioStatus.UNCERTIFIED: TranscriptStatus.PROTECTED_UNAVAILABLE,
}
_HEARD_SOMETHING: Final = frozenset(
    {
        AudioObservation.SPEECH,
        AudioObservation.NON_SPEECH_MUSIC,
        AudioObservation.UNINTELLIGIBLE,
    }
)


class Transcript(Contract):
    """Spoken words heard in the supplied clip, with an evidence-backed status."""

    text: str
    status: TranscriptStatus
    audio_supplied: bool
    audio_coverage_ratio: float = Field(ge=0, le=1)
    clip_ms: int = Field(ge=0)
    timestamp_origin: Literal["assembled_clip_start"] = "assembled_clip_start"


class ModalityMetadata(Contract):
    """What evidence the model was actually given for one exposure."""

    frames_supplied: int = Field(ge=0)
    audio_supplied: bool
    audio_status: AudioStatus
    audio_clip_ms: int = Field(ge=0)
    audio_coverage_ratio: float = Field(ge=0, le=1)


def status_without_clip(audio: PreparedAudio) -> TranscriptStatus:
    """Transcript status when no audio clip could be supplied."""
    return _STATUS_WITHOUT_CLIP.get(audio.status, TranscriptStatus.MISSING_AUDIO)


def modality_metadata(
    *, frames_supplied: int, audio: PreparedAudio
) -> ModalityMetadata:
    """Describe the modalities supplied to the model."""
    return ModalityMetadata(
        frames_supplied=frames_supplied,
        audio_supplied=audio.supplied,
        audio_status=audio.status,
        audio_clip_ms=audio.kept_ms if audio.supplied else 0,
        audio_coverage_ratio=audio.coverage_ratio,
    )


def _fail(code: ReelErrorCode, message: str) -> OutputValidationError:
    return OutputValidationError(code, message)


def _transcript(
    text: str, status: TranscriptStatus, audio: PreparedAudio
) -> Transcript:
    return Transcript(
        text=text,
        status=status,
        audio_supplied=audio.supplied,
        audio_coverage_ratio=audio.coverage_ratio,
        clip_ms=audio.kept_ms if audio.supplied else 0,
    )


def resolve_transcript(
    observation: str, text: str, audio: PreparedAudio, *, max_chars: int
) -> Transcript:
    """Validate the model's transcript against the audio evidence."""
    heard = AudioObservation(observation)
    words = text.strip()
    if not audio.supplied:
        if words:
            raise _fail(
                ReelErrorCode.TRANSCRIPT_WITHOUT_AUDIO,
                "transcript text returned although no audio clip was supplied",
            )
        if heard in _HEARD_SOMETHING:
            raise _fail(
                ReelErrorCode.AUDIO_OBSERVATION_INCONSISTENT,
                "model reported hearing audio that was not supplied",
            )
        return _transcript("", status_without_clip(audio), audio)
    if heard is AudioObservation.NO_AUDIO_PROVIDED:
        raise _fail(
            ReelErrorCode.AUDIO_OBSERVATION_INCONSISTENT,
            "model reported no audio although a clip was supplied",
        )
    if (heard is AudioObservation.SPEECH) != bool(words):
        raise _fail(
            ReelErrorCode.TRANSCRIPT_INCONSISTENT,
            "transcript text and audio observation disagree",
        )
    if len(words) > max_chars:
        raise _fail(ReelErrorCode.TRANSCRIPT_TOO_LONG, "transcript exceeds the cap")
    if audio.status is not AudioStatus.COMPLETE:
        return _transcript(words, TranscriptStatus.PARTIAL, audio)
    return _transcript(words, _STATUS_BY_OBSERVATION[heard], audio)
