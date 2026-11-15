"""Builders for synthetic storage records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from judge.contracts.candidate import (
    AudioStatus,
    CloseReason,
    CoverageMetadata,
    SegmentationStatus,
)
from judge.contracts.feedback import FeedbackLabel, FeedbackRecord, RetrievalMode
from judge.contracts.record import EvaluationStatus, PendingReel, ReelCompletion
from judge.contracts.session import (
    IngestKind,
    IngestReceipt,
    SessionCompletedPayload,
    SessionEndReason,
    SessionEvent,
    SessionRecord,
)
from judge.contracts.storage import EmbeddingProfile, ThumbnailRecord
from judge.contracts.understanding import ReelFeatures, TranscriptStatus, Verdict
from tests.support.builders import SOURCE_ID, T0_MS

OWNER = "demo-user-synthetic"
OTHER_OWNER = "someone-else-synthetic"
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
VALUES_SHA = "a" * 64
PROCESS_EPOCH = UUID("99999999-9999-4999-8999-999999999999")
PROFILE = EmbeddingProfile(
    profile_id="gemini-embedding-001:768:SEMANTIC_SIMILARITY:v1",
    model_id="gemini-embedding-001",
    dimension=768,
    task_type="SEMANTIC_SIMILARITY",
    version="v1",
)


def at(seconds: float) -> datetime:
    """Server time ``seconds`` after the synthetic origin."""
    return NOW + timedelta(seconds=seconds)


def make_session(owner_id: str = OWNER, **overrides: Any) -> SessionRecord:
    base: dict[str, Any] = {
        "session_id": uuid4(),
        "owner_id": owner_id,
        "source_id": SOURCE_ID,
        "started_at": NOW,
        "last_ingest_at": NOW,
    }
    return SessionRecord(**{**base, **overrides})


def make_receipt(session: SessionRecord, **overrides: Any) -> IngestReceipt:
    base: dict[str, Any] = {
        "event_id": uuid4(),
        "owner_id": session.owner_id,
        "session_id": session.session_id,
        "kind": IngestKind.FRAME,
        "source_seq": 0,
        "capture_start_ms": T0_MS,
        "capture_end_ms": T0_MS,
        "received_at": NOW,
        "byte_length": 1234,
        "outcome": "accepted",
        "process_epoch": PROCESS_EPOCH,
    }
    return IngestReceipt(**{**base, **overrides})


def make_pending(
    session: SessionRecord, order_seq: int = 1, **overrides: Any
) -> PendingReel:
    start = T0_MS + (order_seq - 1) * 10_000
    base: dict[str, Any] = {
        "reel_id": uuid4(),
        "owner_id": session.owner_id,
        "session_id": session.session_id,
        "source_id": session.source_id,
        "order_seq": order_seq,
        "idempotency_key": str(uuid4()),
        "start_ms": start,
        "end_ms": start + 8000,
        "boundary_uncertainty_ms": 2000,
        "first_second_observed": False,
        "segmentation_status": SegmentationStatus.APPROXIMATE,
        "close_reason": CloseReason.BOUNDARY,
        "created_at": NOW,
    }
    return PendingReel(**{**base, **overrides})


def make_coverage(**overrides: Any) -> CoverageMetadata:
    base: dict[str, Any] = {
        "observed_ms": 8000,
        "frame_count": 5,
        "selected_frame_count": 3,
        "audio_window_ms": 8000,
        "audio_kept_ms": 8000,
        "audio_boundary_guard_ms": 4000,
        "audio_uncertified_ms": 0,
        "audio_protected_ms": 0,
        "audio_corrupt_ms": 0,
        "audio_missing_ms": 0,
        "uncorroborated_changes": 0,
        "truncated": False,
    }
    return CoverageMetadata(**{**base, **overrides})


def make_features() -> ReelFeatures:
    return ReelFeatures(
        topics=("strength training",),
        format="tutorial",
        tone="calm",
        hook=None,
        emotional_pull=None,
        is_ad=False,
    )


def make_judged(
    pending: PendingReel, verdict: Verdict = Verdict.NEUTRAL, **overrides: Any
) -> ReelCompletion:
    """A judged completion for ``pending``."""
    base: dict[str, Any] = {
        "reel_id": pending.reel_id,
        "owner_id": pending.owner_id,
        "session_id": pending.session_id,
        "evaluation_status": EvaluationStatus.JUDGED,
        "completed_at": NOW,
        "coverage": make_coverage(),
        "audio_status": AudioStatus.COMPLETE,
        "transcript_status": TranscriptStatus.SPEECH,
        "transcript": "Keep your chest up.",
        "on_screen_text": "3 squat cues",
        "description": "A trainer demonstrates squat form.",
        "features": make_features(),
        "verdict": verdict,
        "confidence": 0.8,
        "value_ids": () if verdict is Verdict.NEUTRAL else ("V3",),
        "reason": "Synthetic reason relative to the approved values.",
        "values_revision": "7",
        "values_sha256": VALUES_SHA,
        "retrieval_mode": RetrievalMode.NONE,
        "model_id": "fake-understanding",
        "prompt_version": "judge-v1",
        "understanding_attempts": 1,
    }
    return ReelCompletion(**{**base, **overrides})


def make_failed(
    pending: PendingReel,
    status: EvaluationStatus = EvaluationStatus.FAILED_PROVIDER,
    error_code: str = "provider_timeout",
) -> ReelCompletion:
    """A terminal, non-judged completion for ``pending``."""
    return ReelCompletion(
        reel_id=pending.reel_id,
        owner_id=pending.owner_id,
        session_id=pending.session_id,
        evaluation_status=status,
        completed_at=NOW,
        error_code=error_code,
    )


def make_thumbnail(
    pending: PendingReel, jpeg: bytes = b"\xff\xd8\xffthumb"
) -> ThumbnailRecord:
    return ThumbnailRecord(
        reel_id=pending.reel_id,
        owner_id=pending.owner_id,
        jpeg=jpeg,
        width=180,
        height=320,
        sha256="b" * 64,
    )


def make_feedback(pending: PendingReel, **overrides: Any) -> FeedbackRecord:
    base: dict[str, Any] = {
        "feedback_id": uuid4(),
        "owner_id": pending.owner_id,
        "reel_id": pending.reel_id,
        "label": FeedbackLabel.DISAGREE,
        "comment": "Synthetic comment.",
        "source": "timeline",
        "created_at": NOW,
    }
    return FeedbackRecord(**{**base, **overrides})


def make_session_event(session: SessionRecord) -> SessionEvent:
    return SessionEvent(
        event_id=uuid4(),
        session_id=session.session_id,
        owner_id=session.owner_id,
        payload=SessionCompletedPayload(
            reel_count=3,
            judged_count=2,
            conflict_count=1,
            excluded_count=1,
            end_reason=SessionEndReason.CLIENT_END,
            started_at=NOW,
            ended_at=at(60),
        ),
        created_at=at(60),
    )
