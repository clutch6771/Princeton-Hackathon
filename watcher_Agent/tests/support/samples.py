"""Synthetic domain objects shared by unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from judge.contracts.candidate import AudioStatus
from judge.contracts.examples import example_values_store_item
from judge.contracts.feedback import FeedbackExample, FeedbackLabel, RetrievalMode
from judge.contracts.understanding import ModelJudgment, Verdict
from judge.contracts.values import ApprovedValuesSnapshot
from judge.transcriber.prepare import PreparedAudio
from judge.values.reader import StoredMemoryItem, build_snapshot

OWNER = "demo-user-synthetic"
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
FAKE_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt synthetic-clip"


def snapshot(content: str | None = None) -> ApprovedValuesSnapshot:
    """A pinned snapshot of the synthetic approved values."""
    payload = example_values_store_item().model_dump(mode="json")
    if content is not None:
        payload = {**payload, "content": content, "sha256": None}
    result = build_snapshot(
        OWNER,
        StoredMemoryItem(value=payload, updated_at=NOW),
        max_bytes=16_384,
        require_approval=True,
        now=NOW,
        source="test",
    )
    assert isinstance(result, ApprovedValuesSnapshot)
    return result


def prepared_audio(
    status: AudioStatus = AudioStatus.COMPLETE,
    *,
    supplied: bool | None = None,
    window_ms: int = 10_000,
    kept_ms: int | None = None,
) -> PreparedAudio:
    """Prepared audio in a given evidence state."""
    has_samples = status in {AudioStatus.COMPLETE, AudioStatus.PARTIAL}
    is_supplied = has_samples if supplied is None else supplied
    default_kept = {AudioStatus.COMPLETE: window_ms, AudioStatus.PARTIAL: 3000}
    kept = default_kept.get(status, 0) if kept_ms is None else kept_ms
    dropped = window_ms - kept
    return PreparedAudio(
        wav=FAKE_WAV if is_supplied else None,
        status=status,
        window_ms=window_ms,
        kept_ms=kept,
        uncertified_ms=dropped if status is AudioStatus.UNCERTIFIED else 0,
        protected_ms=dropped if status is AudioStatus.PROTECTED else 0,
        corrupt_ms=dropped if status is AudioStatus.CORRUPT else 0,
        missing_ms=(
            dropped if status in {AudioStatus.MISSING, AudioStatus.PARTIAL} else 0
        ),
    )


def judgment(**overrides: Any) -> ModelJudgment:
    """A model judgment that passes validation with complete audio."""
    base: dict[str, Any] = {
        "transcript": "Keep your chest up and push the floor away.",
        "audio_observation": "speech",
        "on_screen_text": "3 squat cues",
        "description": "A trainer demonstrates squat form with three spoken cues.",
        "topics": ("strength training", "squat form"),
        "format": "tutorial",
        "tone": "calm",
        "hook": "Stop squatting like this",
        "emotional_pull": "self-improvement",
        "is_ad": False,
        "verdict": "aligned",
        "confidence": 0.86,
        "value_ids": ("V3",),
        "reason": "This matches what you said about wanting form tutorials.",
        "evidence_basis": ("frames", "audio"),
    }
    return ModelJudgment.model_validate({**base, **overrides})


def silent_judgment(**overrides: Any) -> ModelJudgment:
    """A model judgment for a reel judged without any audio clip."""
    base: dict[str, Any] = {
        "transcript": "",
        "audio_observation": "no_audio_provided",
        "evidence_basis": ("frames", "on_screen_text"),
    }
    return judgment(**{**base, **overrides})


def feedback_example(
    comment: str | None = "That was a form tutorial, which I do want.",
    *,
    label: FeedbackLabel = FeedbackLabel.DISAGREE,
) -> FeedbackExample:
    """One explicit feedback example tied to the synthetic values revision."""
    values = snapshot()
    return FeedbackExample(
        feedback_id=uuid4(),
        reel_id=uuid4(),
        label=label,
        comment=comment,
        reel_description="A trainer demonstrates squat form.",
        prior_verdict=Verdict.CONFLICT,
        prior_value_ids=("V3",),
        values_revision=values.revision,
        values_sha256=values.content_sha256,
        created_at=NOW,
        retrieval_mode=RetrievalMode.RECENT_EXPLICIT_FEEDBACK,
    )
