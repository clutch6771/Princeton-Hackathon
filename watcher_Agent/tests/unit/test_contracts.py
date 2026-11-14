"""Versioned contracts: strict, immutable, and in sync with exported schemas."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from judge.contracts.base import CONTRACT_VERSION
from judge.contracts.candidate import AudioStatus, CloseReason, SegmentationStatus
from judge.contracts.capture import AudioChunk, FrameObservation, TimeInterval
from judge.contracts.feedback import FeedbackExample, FeedbackRequest
from judge.contracts.notification import (
    AckStatus,
    IngestResponse,
    NotificationEnvelope,
    NotificationPolicy,
    NotificationPolicyUpdate,
)
from judge.contracts.record import (
    EvaluationStatus,
    ReelCompletion,
    ReelRecord,
)
from judge.contracts.schema_export import build_schemas
from judge.contracts.session import SessionEndRequest
from judge.contracts.understanding import (
    ReelFeatures,
    ReelUnderstandingResult,
    TranscriptStatus,
    Verdict,
)
from judge.contracts.values import (
    ApprovedValuesSnapshot,
    ValueEntry,
    ValueStatus,
)
from tests.support.builders import T0_MS, audio_meta, frame_meta, privacy

pytestmark = pytest.mark.unit

FIXTURE_MODELS: dict[str, type[BaseModel]] = {
    "frame_observation.json": FrameObservation,
    "frame_observation_with_transition.json": FrameObservation,
    "frame_observation_ineligible.json": FrameObservation,
    "audio_chunk.json": AudioChunk,
    "audio_chunk_reel_to_private.json": AudioChunk,
    "audio_chunk_protected.json": AudioChunk,
    "session_end_request.json": SessionEndRequest,
    "feedback_request.json": FeedbackRequest,
    "feedback_example.json": FeedbackExample,
    "ingest_response_no_notify.json": IngestResponse,
    "ingest_response_with_notify.json": IngestResponse,
    "notification_policy_update.json": NotificationPolicyUpdate,
    "reel_understanding_result.json": ReelUnderstandingResult,
    "reel_record_judged.json": ReelRecord,
    "reel_record_values_unavailable.json": ReelRecord,
}


@pytest.mark.parametrize("name", sorted(FIXTURE_MODELS))
def test_committed_fixture_validates_against_its_contract(
    name: str, fixtures_dir: Path
) -> None:
    payload = json.loads((fixtures_dir / name).read_text(encoding="utf-8"))

    parsed = FIXTURE_MODELS[name].model_validate(payload)

    assert parsed.model_dump(mode="json")["contract_version"] == CONTRACT_VERSION


def test_committed_schemas_match_the_models(contracts_dir: Path) -> None:
    expected = build_schemas()

    committed = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(contracts_dir.glob("*.schema.json"))
    }

    assert (
        committed == expected
    ), "contract schemas drifted; run scripts/export_contract_schemas.py"


def test_schema_export_covers_every_required_contract() -> None:
    names = set(build_schemas())

    assert {
        "frame_observation.schema.json",
        "audio_chunk.schema.json",
        "reel_candidate.schema.json",
        "approved_values_snapshot.schema.json",
        "feedback_example.schema.json",
        "reel_understanding_result.schema.json",
        "reel_record.schema.json",
        "notification_envelope.schema.json",
    } <= names


def test_frame_observation_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        FrameObservation.model_validate(frame_meta(video_url="https://x.test/v"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"contract_version": "2"},
        {"mime_type": "image/png"},
        {"mime_type": "video/mp4"},
        {"width": 0},
        {"height": 5000},
        {"source_seq": -1},
        {"capture_ts_ms": 0},
        {"source_id": "has spaces"},
        {"event_id": "not-a-uuid"},
    ],
)
def test_frame_observation_rejects_invalid_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FrameObservation.model_validate(frame_meta(**overrides))


def test_frame_observation_requires_privacy_provenance() -> None:
    payload = frame_meta()
    del payload["privacy"]

    with pytest.raises(ValidationError):
        FrameObservation.model_validate(payload)


def test_contracts_are_immutable() -> None:
    frame = FrameObservation.model_validate(frame_meta())

    with pytest.raises(ValidationError):
        frame.capture_ts_ms = 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"sample_rate_hz": 44100},
        {"channels": 2},
        {"codec": "opus"},
        {"container": "wav"},
        {"capture_end_ms": T0_MS},
        {"capture_end_ms": T0_MS - 1},
        {"priming_samples": -1},
        {"capture_state": "muted"},
    ],
)
def test_audio_chunk_rejects_invalid_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AudioChunk.model_validate(audio_meta(**overrides))


@pytest.mark.parametrize(
    "eligible",
    [
        [(T0_MS - 10, T0_MS + 100)],
        [(T0_MS, T0_MS + 5001)],
        [(T0_MS + 100, T0_MS + 100)],
        [(T0_MS, T0_MS + 3000), (T0_MS + 2000, T0_MS + 4000)],
        [(T0_MS + 3000, T0_MS + 4000), (T0_MS, T0_MS + 1000)],
    ],
)
def test_audio_chunk_rejects_intervals_outside_or_overlapping(
    eligible: list[tuple[int, int]],
) -> None:
    with pytest.raises(ValidationError):
        AudioChunk.model_validate(audio_meta(eligible=eligible))


def test_audio_chunk_may_certify_only_part_of_its_span() -> None:
    chunk = AudioChunk.model_validate(
        audio_meta(eligible=[(T0_MS, T0_MS + 1800), (T0_MS + 3200, T0_MS + 5000)])
    )

    assert [i.duration_ms for i in chunk.eligible_intervals] == [1800, 1800]


def test_audio_chunk_with_no_certified_interval_is_valid_but_empty() -> None:
    chunk = AudioChunk.model_validate(audio_meta(eligible=[]))

    assert chunk.eligible_intervals == ()


def test_time_interval_must_have_positive_duration() -> None:
    with pytest.raises(ValidationError):
        TimeInterval(start_ms=10, end_ms=10)


def _features() -> ReelFeatures:
    return ReelFeatures(
        topics=("strength training",),
        format="tutorial",
        tone="calm",
        hook=None,
        emotional_pull=None,
        is_ad=False,
    )


def _result(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "contract_version": "1",
        "transcript": "",
        "transcript_status": "missing_audio",
        "on_screen_text": "3 form cues",
        "description": "A trainer demonstrates a squat.",
        "features": _features().model_dump(mode="json"),
        "verdict": "aligned",
        "confidence": 0.8,
        "value_ids": ["V3"],
        "reason": "Matches what you said about form tutorials.",
        "evidence_basis": ["frames", "on_screen_text"],
    }
    return {**base, **overrides}


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), float("inf")])
def test_understanding_result_requires_finite_confidence_in_unit_interval(
    confidence: float,
) -> None:
    with pytest.raises(ValidationError):
        ReelUnderstandingResult.model_validate(_result(confidence=confidence))


@pytest.mark.parametrize("verdict", ["bad", "inflammatory", "harmful", ""])
def test_understanding_result_only_allows_the_prd_verdict_enum(verdict: str) -> None:
    with pytest.raises(ValidationError):
        ReelUnderstandingResult.model_validate(_result(verdict=verdict))


def test_prd_verdict_enum_values() -> None:
    assert {v.value for v in Verdict} == {"aligned", "neutral", "conflict"}


def test_transcript_status_distinguishes_every_required_case() -> None:
    assert {s.value for s in TranscriptStatus} == {
        "speech",
        "non_speech_music",
        "silence",
        "missing_audio",
        "protected_unavailable",
        "corrupt",
        "unintelligible",
        "partial",
    }


def _completion(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "reel_id": str(uuid4()),
        "owner_id": "demo-user-synthetic",
        "session_id": str(uuid4()),
        "evaluation_status": "judged",
        "completed_at": datetime(2026, 11, 14, tzinfo=UTC).isoformat(),
        "description": "A trainer demonstrates a squat.",
        "features": _features().model_dump(mode="json"),
        "verdict": "aligned",
        "confidence": 0.8,
        "value_ids": ["V3"],
        "reason": "Matches what you said about form tutorials.",
    }
    return {**base, **overrides}


def test_judged_completion_requires_a_verdict() -> None:
    with pytest.raises(ValidationError):
        ReelCompletion.model_validate(_completion(verdict=None))


@pytest.mark.parametrize(
    "status",
    [s for s in EvaluationStatus if s not in {EvaluationStatus.JUDGED}],
)
def test_non_judged_states_can_never_carry_a_verdict(
    status: EvaluationStatus,
) -> None:
    with pytest.raises(ValidationError):
        ReelCompletion.model_validate(_completion(evaluation_status=status.value))


def test_completion_cannot_be_pending() -> None:
    with pytest.raises(ValidationError):
        ReelCompletion.model_validate(
            _completion(
                evaluation_status="pending",
                verdict=None,
                confidence=None,
                features=None,
            )
        )


def test_failed_completion_has_null_verdict_not_neutral() -> None:
    completion = ReelCompletion.model_validate(
        _completion(
            evaluation_status="values_unavailable",
            verdict=None,
            confidence=None,
            features=None,
            description="",
            reason="",
            value_ids=[],
            error_code="values_missing",
        )
    )

    assert completion.verdict is None
    assert completion.evaluation_status is EvaluationStatus.VALUES_UNAVAILABLE


def test_evaluation_status_terminal_flags() -> None:
    assert EvaluationStatus.PENDING.is_terminal is False
    assert all(s.is_terminal for s in EvaluationStatus if s is not s.PENDING)


@pytest.mark.parametrize(
    ("batch_size", "minimum_conflicts"), [(15, 16), (15, 0), (0, 0), (5, 6)]
)
def test_policy_requires_one_to_batch_size_conflicts(
    batch_size: int, minimum_conflicts: int
) -> None:
    with pytest.raises(ValidationError):
        NotificationPolicyUpdate(
            contract_version="1",
            batch_size=batch_size,
            minimum_conflicts=minimum_conflicts,
            cooldown_seconds=600,
        )
    with pytest.raises(ValidationError):
        NotificationPolicy(
            version=1,
            batch_size=batch_size,
            minimum_conflicts=minimum_conflicts,
            cooldown_seconds=600,
        )


def test_ingest_response_keeps_notify_at_the_response_root() -> None:
    event_id, session_id = uuid4(), uuid4()

    silent = IngestResponse(
        event_id=event_id, session_id=session_id, status=AckStatus.ACCEPTED
    ).model_dump(mode="json")
    envelope = NotificationEnvelope(
        title="A check-in on your feed",
        body="3 of your last 15 reels went against what you told me you wanted.",
        reel_ids=(uuid4(), uuid4(), uuid4()),
        notification_id=uuid4(),
        expires_at=datetime(2026, 11, 14, 12, 2, tzinfo=UTC),
    )
    loud = IngestResponse(
        event_id=event_id,
        session_id=session_id,
        status=AckStatus.ACCEPTED,
        notify=envelope,
    ).model_dump(mode="json")

    assert "notify" in silent and silent["notify"] is None
    assert set(loud["notify"]) == {
        "title",
        "body",
        "reel_ids",
        "notification_id",
        "expires_at",
    }
    assert "data" not in loud and "success" not in loud


def _entry(value_id: str, status: ValueStatus) -> ValueEntry:
    return ValueEntry(
        value_id=value_id,
        title=f"Synthetic value {value_id}",
        strength="high",
        want_more="form tutorials",
        want_less="physique rating",
        unless="none",
        in_my_words="gym stuff is great, comparing bodies is not",
        evidence="approved 2026-11-14",
        approved_at="2026-11-14",
        status=status,
        superseded_by=None,
        raw_markdown=f"## {value_id}: Synthetic value {value_id}",
    )


def test_snapshot_exposes_only_active_value_ids() -> None:
    snapshot = ApprovedValuesSnapshot(
        owner_id="demo-user-synthetic",
        revision="7",
        content_sha256="0" * 64,
        content="synthetic",
        entries=(
            _entry("V1", ValueStatus.ACTIVE),
            _entry("V2", ValueStatus.SUPERSEDED),
            _entry("V3", ValueStatus.DELETED),
            _entry("V4", ValueStatus.ACTIVE),
        ),
        approved_at=None,
        loaded_at=datetime(2026, 11, 14, tzinfo=UTC),
        source="fake",
    )

    assert snapshot.active_value_ids == frozenset({"V1", "V4"})
    assert [e.value_id for e in snapshot.active_entries] == ["V1", "V4"]


def test_privacy_provenance_enums_are_closed() -> None:
    with pytest.raises(ValidationError):
        FrameObservation.model_validate(
            frame_meta(privacy=privacy(eligibility="probably"))
        )
    with pytest.raises(ValidationError):
        FrameObservation.model_validate(
            frame_meta(privacy=privacy(capability="trust_me"))
        )


def test_operational_enums_cover_the_documented_states() -> None:
    assert {s.value for s in SegmentationStatus} == {
        "clean",
        "approximate",
        "uncertain",
    }
    assert {s.value for s in AudioStatus} == {
        "complete",
        "partial",
        "missing",
        "uncertified",
        "protected",
        "corrupt",
    }
    assert {r.value for r in CloseReason} == {
        "boundary",
        "left_feed",
        "frame_gap",
        "max_duration",
        "session_end",
    }
