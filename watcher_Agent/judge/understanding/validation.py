"""Deterministic validation of the model's structured output.

The model proposes; this code decides what is stored. A result that fails
here is recorded as ``failed_validation`` with ``verdict = null``. There is no
repair loop and no second judging call, and a failure never becomes neutral.
"""

from __future__ import annotations

import math
from typing import Final

from judge.contracts.understanding import (
    EvidenceModality,
    ModelJudgment,
    ReelFeatures,
    ReelUnderstandingResult,
    TranscriptStatus,
    Verdict,
)
from judge.contracts.values import ApprovedValuesSnapshot
from judge.errors import OutputValidationError, ReelErrorCode
from judge.transcriber.prepare import PreparedAudio
from judge.transcriber.transcript import resolve_transcript

MAX_TOPICS: Final = 8
_MAX_TOPIC_CHARS: Final = 80
_MAX_LABEL_CHARS: Final = 120
_MAX_FEATURE_CHARS: Final = 400
_MAX_REASON_CHARS: Final = 400
_MAX_DESCRIPTION_CHARS: Final = 1200
_MAX_ON_SCREEN_CHARS: Final = 2000
_ELLIPSIS: Final = "…"


def _bounded(text: str, limit: int) -> str:
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + _ELLIPSIS


def _optional(text: str, limit: int) -> str | None:
    return _bounded(text, limit) or None


def _unique[T](items: tuple[T, ...]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(items))


def _check_value_ids(
    judgment: ModelJudgment, snapshot: ApprovedValuesSnapshot
) -> tuple[str, ...]:
    value_ids = _unique(tuple(v.strip() for v in judgment.value_ids if v.strip()))
    if any(value_id not in snapshot.active_value_ids for value_id in value_ids):
        raise OutputValidationError(
            ReelErrorCode.VALUE_IDS_UNKNOWN,
            "the judgment cites a value that is not an active approved value",
        )
    if judgment.verdict != Verdict.NEUTRAL.value and not value_ids:
        raise OutputValidationError(
            ReelErrorCode.VALUE_IDS_MISSING,
            "an aligned or conflict verdict must cite an approved value",
        )
    return value_ids


def _check_evidence(
    judgment: ModelJudgment, audio: PreparedAudio, native_transcript: str | None = None
) -> tuple[EvidenceModality, ...]:
    basis = _unique(tuple(EvidenceModality(item) for item in judgment.evidence_basis))
    if not basis:
        raise OutputValidationError(
            ReelErrorCode.EVIDENCE_BASIS_EMPTY, "the verdict names no evidence"
        )
    if EvidenceModality.AUDIO in basis and not audio.supplied and not native_transcript:
        raise OutputValidationError(
            ReelErrorCode.SPEECH_EVIDENCE_UNSUPPORTED,
            "the verdict relies on audio that was not supplied",
        )
    return basis


def _features(judgment: ModelJudgment, *, first_second_observed: bool) -> ReelFeatures:
    topics = tuple(
        _bounded(topic, _MAX_TOPIC_CHARS) for topic in judgment.topics if topic.strip()
    )
    return ReelFeatures(
        topics=topics[:MAX_TOPICS],
        format=_bounded(judgment.format, _MAX_LABEL_CHARS),
        tone=_bounded(judgment.tone, _MAX_LABEL_CHARS),
        # A first-second hook cannot be claimed when it was not observed.
        hook=(
            _optional(judgment.hook, _MAX_FEATURE_CHARS)
            if first_second_observed
            else None
        ),
        emotional_pull=_optional(judgment.emotional_pull, _MAX_FEATURE_CHARS),
        is_ad=judgment.is_ad,
    )


def validate_judgment(
    judgment: ModelJudgment,
    *,
    snapshot: ApprovedValuesSnapshot,
    audio: PreparedAudio,
    first_second_observed: bool,
    transcript_max_chars: int,
    native_transcript: str | None = None,
) -> ReelUnderstandingResult:
    """Return the accepted result or raise ``OutputValidationError``."""
    if not judgment.description.strip():
        raise OutputValidationError(
            ReelErrorCode.DESCRIPTION_MISSING, "the judgment has no description"
        )
    confidence = judgment.confidence
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise OutputValidationError(
            ReelErrorCode.CONFIDENCE_OUT_OF_RANGE,
            "confidence must be a finite number between 0 and 1",
        )
    transcript = resolve_transcript(
        judgment.audio_observation,
        judgment.transcript,
        audio,
        max_chars=transcript_max_chars,
    )
    value_ids = _check_value_ids(judgment, snapshot)
    if not judgment.reason.strip():
        raise OutputValidationError(
            ReelErrorCode.REASON_MISSING, "the verdict has no explanation"
        )
    return ReelUnderstandingResult(
        transcript=(native_transcript if native_transcript else transcript.text),
        transcript_status=(
            TranscriptStatus.PARTIAL if native_transcript else transcript.status
        ),
        on_screen_text=_bounded(judgment.on_screen_text, _MAX_ON_SCREEN_CHARS),
        description=_bounded(judgment.description, _MAX_DESCRIPTION_CHARS),
        features=_features(judgment, first_second_observed=first_second_observed),
        verdict=Verdict(judgment.verdict),
        confidence=confidence,
        value_ids=value_ids,
        reason=_bounded(judgment.reason, _MAX_REASON_CHARS),
        evidence_basis=_check_evidence(judgment, audio, native_transcript),
    )
