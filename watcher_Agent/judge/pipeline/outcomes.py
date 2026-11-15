"""Builders for the single terminal outcome of a reel."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from judge.contracts.feedback import FeedbackExample, RetrievalMode
from judge.contracts.record import (
    EvaluationStatus,
    PendingReelRef,
    ReelCompletion,
)
from judge.contracts.understanding import ReelUnderstandingResult
from judge.contracts.values import ApprovedValuesSnapshot
from judge.errors import ReelErrorCode
from judge.pipeline.job import JudgeJob


def judged_outcome(
    job: JudgeJob,
    result: ReelUnderstandingResult,
    *,
    values: ApprovedValuesSnapshot,
    examples: tuple[FeedbackExample, ...],
    model_id: str,
    prompt_version: str,
    attempts: int,
    now: datetime,
) -> ReelCompletion:
    """Outcome of an accepted, validated judgment."""
    candidate = job.candidate
    return ReelCompletion(
        reel_id=candidate.candidate_id,
        owner_id=candidate.owner_id,
        session_id=candidate.session_id,
        evaluation_status=EvaluationStatus.JUDGED,
        completed_at=now,
        coverage=candidate.coverage,
        audio_status=candidate.audio_status,
        transcript_status=result.transcript_status,
        transcript=result.transcript,
        on_screen_text=result.on_screen_text,
        description=result.description,
        features=result.features,
        verdict=result.verdict,
        confidence=result.confidence,
        value_ids=result.value_ids,
        reason=result.reason,
        values_revision=values.revision,
        values_sha256=values.content_sha256,
        feedback_example_ids=tuple(example.feedback_id for example in examples),
        retrieval_mode=(
            RetrievalMode.RECENT_EXPLICIT_FEEDBACK if examples else RetrievalMode.NONE
        ),
        model_id=model_id,
        prompt_version=prompt_version,
        understanding_attempts=attempts,
    )


def failed_outcome(
    job: JudgeJob,
    status: EvaluationStatus,
    error_code: ReelErrorCode,
    *,
    now: datetime,
    attempts: int = 0,
    values: ApprovedValuesSnapshot | None = None,
    model_id: str | None = None,
    prompt_version: str | None = None,
) -> ReelCompletion:
    """Outcome of an exposure that could not be judged. Verdict stays null."""
    candidate = job.candidate
    return ReelCompletion(
        reel_id=candidate.candidate_id,
        owner_id=candidate.owner_id,
        session_id=candidate.session_id,
        evaluation_status=status,
        completed_at=now,
        coverage=candidate.coverage,
        audio_status=candidate.audio_status,
        values_revision=values.revision if values else None,
        values_sha256=values.content_sha256 if values else None,
        model_id=model_id,
        prompt_version=prompt_version,
        understanding_attempts=attempts,
        error_code=error_code.value,
    )


def lost_outcome(
    ref: PendingReelRef,
    status: EvaluationStatus,
    error_code: ReelErrorCode,
    *,
    now: datetime,
) -> ReelCompletion:
    """Outcome for a pending reel whose volatile media no longer exists."""
    return ReelCompletion(
        reel_id=ref.reel_id,
        owner_id=ref.owner_id,
        session_id=ref.session_id,
        evaluation_status=status,
        completed_at=now,
        error_code=error_code.value,
    )


def skipped_ref(job: JudgeJob) -> tuple[UUID, str, UUID]:
    """Identity triple of a job, for logging without content."""
    candidate = job.candidate
    return candidate.candidate_id, candidate.owner_id, candidate.session_id
