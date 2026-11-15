"""Understanding contracts: what the model returns and what the Judge accepts.

``ModelJudgment`` is the response schema sent to the provider. It is kept
deliberately plain (inline enums, no nullable fields) so that every provider's
structured-output mode can honour it. ``ReelUnderstandingResult`` is the
validated result the pipeline stores.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from judge.contracts.base import CONTRACT_VERSION, Contract, ContractVersion


class Verdict(StrEnum):
    """PRD verdict enum, always relative to the user's approved values."""

    ALIGNED = "aligned"
    NEUTRAL = "neutral"
    CONFLICT = "conflict"


class TranscriptStatus(StrEnum):
    """Evidence-backed transcript state stored on a reel."""

    SPEECH = "speech"
    NON_SPEECH_MUSIC = "non_speech_music"
    SILENCE = "silence"
    MISSING_AUDIO = "missing_audio"
    PROTECTED_UNAVAILABLE = "protected_unavailable"
    CORRUPT = "corrupt"
    UNINTELLIGIBLE = "unintelligible"
    PARTIAL = "partial"


class AudioObservation(StrEnum):
    """What the model reports hearing in the supplied clip."""

    SPEECH = "speech"
    NON_SPEECH_MUSIC = "non_speech_music"
    SILENCE = "silence"
    UNINTELLIGIBLE = "unintelligible"
    NO_AUDIO_PROVIDED = "no_audio_provided"


class EvidenceModality(StrEnum):
    """Kinds of evidence a verdict may rest on."""

    FRAMES = "frames"
    ON_SCREEN_TEXT = "on_screen_text"
    AUDIO = "audio"


class ReelFeatures(Contract):
    """PRD ``features`` block of a reel record."""

    topics: tuple[str, ...] = Field(max_length=8)
    format: str = Field(max_length=120)
    tone: str = Field(max_length=120)
    hook: str | None = Field(
        default=None,
        max_length=400,
        description="Null unless the first second of the exposure was observed.",
    )
    emotional_pull: str | None = Field(default=None, max_length=400)
    is_ad: bool


class ModelJudgment(BaseModel):
    """The single structured object the model returns for one reel."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    transcript: str = Field(
        description=(
            "Words actually heard in the audio clip. Empty when no clip was "
            "provided or no intelligible speech was heard. Never on-screen text."
        )
    )
    audio_observation: Literal[
        "speech", "non_speech_music", "silence", "unintelligible", "no_audio_provided"
    ] = Field(description="What the audio clip contained.")
    on_screen_text: str = Field(description="Text visible in the frames, or empty.")
    description: str = Field(
        description="One or two neutral sentences about what the evidence shows."
    )
    topics: tuple[str, ...] = Field(description="Up to five short topic labels.")
    format: str = Field(description="For example: tutorial, skit, vlog, ad.")
    tone: str = Field(description="For example: calm, humorous, urgent.")
    hook: str = Field(
        description="Opening hook, only if the first second was observed; else empty."
    )
    emotional_pull: str = Field(description="Main emotional appeal, or empty.")
    is_ad: bool = Field(description="True when the reel is an advertisement.")
    verdict: Literal["aligned", "neutral", "conflict"] = Field(
        description="Relative to the approved values only."
    )
    confidence: float = Field(description="Between 0 and 1.")
    value_ids: tuple[str, ...] = Field(
        description="IDs of the approved values relied on. Empty when neutral."
    )
    reason: str = Field(
        description="One sentence, in terms of what the person said they want."
    )
    evidence_basis: tuple[Literal["frames", "on_screen_text", "audio"], ...] = Field(
        description="Only the evidence types that actually support the verdict."
    )


class ReelUnderstandingResult(Contract):
    """Validated outcome of the one joint understand/transcribe/judge call."""

    contract_version: ContractVersion = CONTRACT_VERSION
    transcript: str
    transcript_status: TranscriptStatus
    on_screen_text: str
    description: str = Field(min_length=1)
    features: ReelFeatures
    verdict: Verdict
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    value_ids: tuple[str, ...]
    reason: str = Field(min_length=1)
    evidence_basis: tuple[EvidenceModality, ...]
