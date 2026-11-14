"""Synthetic examples of every wire contract.

These are the single source of the committed fixtures in
``watcher_Agent/contracts/v1/fixtures`` and of the OpenAPI examples. Nothing
here is real: identifiers, values, text and timestamps are invented.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from pydantic import BaseModel

from judge.contracts.candidate import (
    AudioStatus,
    CloseReason,
    CoverageMetadata,
    SegmentationStatus,
)
from judge.contracts.capture import (
    AudioCaptureState,
    AudioChunk,
    AudioContainer,
    Eligibility,
    FrameObservation,
    GateCapability,
    PrivacyProvenance,
    Surface,
    TimeInterval,
)
from judge.contracts.feedback import (
    FeedbackExample,
    FeedbackLabel,
    FeedbackRequest,
    RetrievalMode,
)
from judge.contracts.notification import (
    AckStatus,
    IngestResponse,
    NotificationEnvelope,
    NotificationPolicyUpdate,
)
from judge.contracts.record import (
    EmbeddingInfo,
    EmbeddingStatus,
    EvaluationStatus,
    ReelRecord,
    StageTimings,
)
from judge.contracts.session import SessionEndRequest
from judge.contracts.understanding import (
    EvidenceModality,
    ReelFeatures,
    ReelUnderstandingResult,
    TranscriptStatus,
    Verdict,
)
from judge.contracts.values import (
    ValuesApprovalMetadata,
    ValuesLifecycleMetadata,
    ValuesStoreItem,
)

EXAMPLE_OWNER_ID: Final = "demo-user-synthetic"
EXAMPLE_SESSION_ID: Final = UUID("11111111-1111-4111-8111-111111111111")
EXAMPLE_SOURCE_ID: Final = "ios-broadcast-synthetic"
EXAMPLE_T0_MS: Final = 1_800_000_000_000
_REEL_A: Final = UUID("a0000000-0000-4000-8000-00000000000a")
_REEL_B: Final = UUID("b0000000-0000-4000-8000-00000000000b")
_REEL_C: Final = UUID("c0000000-0000-4000-8000-00000000000c")
_NOTIFICATION_ID: Final = UUID("d0000000-0000-4000-8000-00000000000d")
_NOW: Final = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)

EXAMPLE_VALUES_MARKDOWN: Final = """\
# Approved values (synthetic development sample)

## V1: Cooking I can actually make (strength: medium)
- Want more: short weeknight recipes, technique explainers
- Want less: extreme eating challenges
- Unless: the challenge is a professional chef demonstrating technique
- In my words: "show me food I could cook tonight"
- Evidence: onboarding conversation; approved 2026-11-14
- Approved: 2026-11-14T09:00:00Z

## V2: Fitness (strength: high)
- Want more: workouts
- Want less: none
- Unless: none
- In my words: "more gym content"
- Evidence: onboarding conversation; approved 2026-11-13
- Approved: 2026-11-13T09:00:00Z
- Status: superseded by V3

## V3: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: disagreed with a synthetic reel; approved 2026-11-14
- Approved: 2026-11-14T09:05:00Z
"""


def _privacy(
    eligibility: Eligibility = Eligibility.ELIGIBLE,
    surface: Surface = Surface.INSTAGRAM_REELS,
) -> PrivacyProvenance:
    return PrivacyProvenance(
        gate_id="feed-eligibility-gate",
        gate_version="1.0.0",
        capability=GateCapability.NATIVE_GATE,
        eligibility=eligibility,
        surface=surface,
        method="on_device_classifier",
    )


def _frame(event: str, seq: int, offset_ms: int, **extra: object) -> FrameObservation:
    return FrameObservation.model_validate(
        {
            "event_id": UUID(event),
            "session_id": EXAMPLE_SESSION_ID,
            "source_id": EXAMPLE_SOURCE_ID,
            "source_seq": seq,
            "capture_ts_ms": EXAMPLE_T0_MS + offset_ms,
            "mime_type": "image/jpeg",
            "width": 288,
            "height": 512,
            "privacy": _privacy(),
            **extra,
        }
    )


def _audio(event: str, seq: int, **extra: object) -> AudioChunk:
    start = EXAMPLE_T0_MS
    return AudioChunk.model_validate(
        {
            "event_id": UUID(event),
            "session_id": EXAMPLE_SESSION_ID,
            "source_id": EXAMPLE_SOURCE_ID,
            "source_seq": seq,
            "capture_start_ms": start,
            "capture_end_ms": start + 5000,
            "codec": "aac",
            "container": AudioContainer.ADTS,
            "sample_rate_hz": 16000,
            "channels": 1,
            "priming_samples": 2112,
            "eligible_intervals": (TimeInterval(start_ms=start, end_ms=start + 5000),),
            "privacy": _privacy(),
            **extra,
        }
    )


def _features() -> ReelFeatures:
    return ReelFeatures(
        topics=("strength training", "squat form"),
        format="tutorial",
        tone="calm",
        hook=None,
        emotional_pull="self-improvement",
        is_ad=False,
    )


def _coverage() -> CoverageMetadata:
    return CoverageMetadata(
        observed_ms=12_000,
        frame_count=7,
        selected_frame_count=3,
        audio_window_ms=12_000,
        audio_kept_ms=10_200,
        audio_boundary_guard_ms=4_000,
        audio_uncertified_ms=0,
        audio_protected_ms=0,
        audio_corrupt_ms=0,
        audio_missing_ms=1_800,
        uncorroborated_changes=1,
        truncated=False,
    )


def _envelope() -> NotificationEnvelope:
    return NotificationEnvelope(
        title="A check-in on your feed",
        body="3 of your last 15 reels went against what you told me you wanted.",
        reel_ids=(_REEL_A, _REEL_B, _REEL_C),
        notification_id=_NOTIFICATION_ID,
        expires_at=datetime(2026, 11, 14, 12, 2, 0, tzinfo=UTC),
    )


def _judged_record() -> ReelRecord:
    return ReelRecord(
        id=_REEL_A,
        session_id=EXAMPLE_SESSION_ID,
        start=datetime(2026, 11, 14, 11, 59, 40, tzinfo=UTC),
        end=datetime(2026, 11, 14, 11, 59, 52, tzinfo=UTC),
        thumbnail_url=f"/thumbs/{_REEL_A}.jpg",
        transcript="Keep your chest up and push the floor away.",
        on_screen_text="3 squat cues",
        description="A trainer demonstrates squat form with three spoken cues.",
        features=_features(),
        verdict=Verdict.ALIGNED,
        confidence=0.86,
        value_ids=("V3",),
        reason="This matches what you said about wanting form tutorials.",
        evaluation_status=EvaluationStatus.JUDGED,
        owner_id=EXAMPLE_OWNER_ID,
        source_id=EXAMPLE_SOURCE_ID,
        order_seq=1,
        idempotency_key="aaaaaaa1-0000-4000-8000-000000000001",
        boundary_uncertainty_ms=2000,
        first_second_observed=False,
        segmentation_status=SegmentationStatus.APPROXIMATE,
        close_reason=CloseReason.BOUNDARY,
        coverage=_coverage(),
        audio_status=AudioStatus.COMPLETE,
        transcript_status=TranscriptStatus.SPEECH,
        values_revision="7",
        values_sha256=values_sha256(EXAMPLE_VALUES_MARKDOWN),
        feedback_example_ids=(),
        retrieval_mode=RetrievalMode.NONE,
        model_id="gemini-3.5-flash-lite",
        prompt_version="judge-v1",
        understanding_attempts=1,
        embedding=EmbeddingInfo(
            profile_id="gemini-embedding-001:768:SEMANTIC_SIMILARITY:v1",
            model_id="gemini-embedding-001",
            dimension=768,
            status=EmbeddingStatus.INDEXED,
            attempts=1,
        ),
        timings=StageTimings(
            detect_ms=2100,
            audio_wait_ms=3100,
            queue_ms=5,
            context_ms=12,
            inference_ms=2300,
            validation_ms=1,
            persist_ms=18,
            total_ms=7536,
        ),
        latency_overrun=False,
        error_code=None,
        created_at=_NOW,
        completed_at=_NOW,
    )


def _unavailable_record() -> ReelRecord:
    return ReelRecord(
        id=_REEL_B,
        session_id=EXAMPLE_SESSION_ID,
        start=datetime(2026, 11, 14, 11, 59, 54, tzinfo=UTC),
        end=datetime(2026, 11, 14, 12, 0, 2, tzinfo=UTC),
        evaluation_status=EvaluationStatus.VALUES_UNAVAILABLE,
        owner_id=EXAMPLE_OWNER_ID,
        source_id=EXAMPLE_SOURCE_ID,
        order_seq=2,
        idempotency_key="aaaaaaa1-0000-4000-8000-000000000008",
        boundary_uncertainty_ms=2000,
        first_second_observed=False,
        segmentation_status=SegmentationStatus.APPROXIMATE,
        close_reason=CloseReason.BOUNDARY,
        audio_status=AudioStatus.PARTIAL,
        error_code="values_missing",
        created_at=_NOW,
        completed_at=_NOW,
    )


def values_sha256(content: str) -> str:
    """Hex SHA-256 of canonical values content (UTF-8)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def example_values_store_item() -> ValuesStoreItem:
    """The agreed Store payload for ``values.md`` (synthetic)."""
    return ValuesStoreItem(
        content=EXAMPLE_VALUES_MARKDOWN,
        revision=7,
        sha256=values_sha256(EXAMPLE_VALUES_MARKDOWN),
        approval=ValuesApprovalMetadata(
            status="approved",
            approved_at=datetime(2026, 11, 14, 9, 5, 0, tzinfo=UTC),
        ),
        lifecycle=ValuesLifecycleMetadata(status="active"),
    )


def build_examples() -> dict[str, BaseModel]:
    """Return ``{fixture file name: synthetic contract instance}``."""
    start = EXAMPLE_T0_MS
    return {
        "frame_observation.json": _frame("aaaaaaa1-0000-4000-8000-000000000001", 0, 0),
        "frame_observation_with_transition.json": _frame(
            "aaaaaaa1-0000-4000-8000-000000000008",
            7,
            14_000,
            transition_at_ms=start + 13_300,
        ),
        "frame_observation_ineligible.json": _frame(
            "aaaaaaa1-0000-4000-8000-000000000009",
            8,
            16_000,
            privacy=_privacy(Eligibility.INELIGIBLE, Surface.OTHER),
        ),
        "audio_chunk.json": _audio("bbbbbbb1-0000-4000-8000-000000000001", 0),
        "audio_chunk_reel_to_private.json": _audio(
            "bbbbbbb1-0000-4000-8000-000000000002",
            1,
            eligible_intervals=(TimeInterval(start_ms=start, end_ms=start + 1800),),
        ),
        "audio_chunk_protected.json": _audio(
            "bbbbbbb1-0000-4000-8000-000000000003",
            2,
            capture_state=AudioCaptureState.PROTECTED,
            eligible_intervals=(),
        ),
        "session_end_request.json": SessionEndRequest(
            session_id=EXAMPLE_SESSION_ID,
            acknowledged_notification_ids=(_NOTIFICATION_ID,),
        ),
        "feedback_request.json": FeedbackRequest(
            reel_id=_REEL_A,
            label=FeedbackLabel.DISAGREE,
            score=None,
            comment="This one was a form tutorial, which I do want.",
            source="timeline",
            client_feedback_id=UUID("eeeeeee1-0000-4000-8000-000000000001"),
        ),
        "feedback_example.json": FeedbackExample(
            feedback_id=UUID("fffffff1-0000-4000-8000-000000000001"),
            reel_id=_REEL_A,
            label=FeedbackLabel.DISAGREE,
            comment="This one was a form tutorial, which I do want.",
            reel_description="A trainer demonstrates squat form.",
            prior_verdict=Verdict.CONFLICT,
            prior_value_ids=("V3",),
            values_revision="7",
            values_sha256=values_sha256(EXAMPLE_VALUES_MARKDOWN),
            created_at=_NOW,
            retrieval_mode=RetrievalMode.RECENT_EXPLICIT_FEEDBACK,
        ),
        "ingest_response_no_notify.json": IngestResponse(
            event_id=UUID("aaaaaaa1-0000-4000-8000-000000000001"),
            session_id=EXAMPLE_SESSION_ID,
            status=AckStatus.ACCEPTED,
        ),
        "ingest_response_with_notify.json": IngestResponse(
            event_id=UUID("aaaaaaa1-0000-4000-8000-000000000008"),
            session_id=EXAMPLE_SESSION_ID,
            status=AckStatus.ACCEPTED,
            notify=_envelope(),
        ),
        "notification_policy_update.json": NotificationPolicyUpdate(
            batch_size=15, minimum_conflicts=3, cooldown_seconds=600
        ),
        "reel_understanding_result.json": ReelUnderstandingResult(
            transcript="Keep your chest up and push the floor away.",
            transcript_status=TranscriptStatus.SPEECH,
            on_screen_text="3 squat cues",
            description="A trainer demonstrates squat form with three spoken cues.",
            features=_features(),
            verdict=Verdict.ALIGNED,
            confidence=0.86,
            value_ids=("V3",),
            reason="This matches what you said about wanting form tutorials.",
            evidence_basis=(EvidenceModality.FRAMES, EvidenceModality.AUDIO),
        ),
        "reel_record_judged.json": _judged_record(),
        "reel_record_values_unavailable.json": _unavailable_record(),
        "approved_values_store_item.json": example_values_store_item(),
    }
