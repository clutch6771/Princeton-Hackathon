"""Backend eligibility gate: only positively eligible Reels media is admitted."""

from __future__ import annotations

import pytest

from judge.config import PrivacyMode
from judge.contracts.capture import AudioChunk, FrameObservation
from judge.errors import PrivacyCapabilityUnavailableError
from judge.privacy.eligibility import BackendEligibilityGate, GateDecision
from tests.support.builders import T0_MS, audio_meta, frame_meta, privacy

pytestmark = pytest.mark.unit


def _frame(**privacy_overrides: str) -> FrameObservation:
    return FrameObservation.model_validate(
        frame_meta(privacy=privacy(**privacy_overrides))
    )


def _audio(
    eligible: list[tuple[int, int]] | None = None, **privacy_overrides: str
) -> AudioChunk:
    return AudioChunk.model_validate(
        audio_meta(eligible=eligible, privacy=privacy(**privacy_overrides))
    )


def test_enforce_admits_a_natively_certified_reels_frame() -> None:
    gate = BackendEligibilityGate(PrivacyMode.ENFORCE)

    screening = gate.screen_frame(_frame().privacy)

    assert screening.decision is GateDecision.ADMIT


@pytest.mark.parametrize(
    "overrides",
    [
        {"eligibility": "ineligible"},
        {"eligibility": "unknown"},
        {"surface": "other"},
        {"surface": "unknown"},
        {"eligibility": "ineligible", "surface": "other"},
    ],
)
def test_anything_not_positively_eligible_is_excluded(
    overrides: dict[str, str],
) -> None:
    gate = BackendEligibilityGate(PrivacyMode.ENFORCE)

    screening = gate.screen_frame(_frame(**overrides).privacy)

    assert screening.decision is GateDecision.EXCLUDE
    assert screening.reason


@pytest.mark.parametrize("capability", ["none", "dev_fake"])
def test_enforce_reports_capability_unavailable_without_a_native_gate(
    capability: str,
) -> None:
    gate = BackendEligibilityGate(PrivacyMode.ENFORCE)

    with pytest.raises(PrivacyCapabilityUnavailableError):
        gate.screen_frame(_frame(capability=capability).privacy)
    with pytest.raises(PrivacyCapabilityUnavailableError):
        gate.certified_intervals(_audio(capability=capability))


def test_dev_permissive_accepts_the_development_fake_gate() -> None:
    gate = BackendEligibilityGate(PrivacyMode.DEV_PERMISSIVE)

    screening = gate.screen_frame(_frame(capability="dev_fake").privacy)

    assert screening.decision is GateDecision.ADMIT


def test_dev_permissive_still_rejects_events_with_no_gate_at_all() -> None:
    gate = BackendEligibilityGate(PrivacyMode.DEV_PERMISSIVE)

    with pytest.raises(PrivacyCapabilityUnavailableError):
        gate.screen_frame(_frame(capability="none").privacy)


def test_dev_permissive_still_excludes_ineligible_frames() -> None:
    gate = BackendEligibilityGate(PrivacyMode.DEV_PERMISSIVE)

    screening = gate.screen_frame(
        _frame(capability="dev_fake", eligibility="ineligible").privacy
    )

    assert screening.decision is GateDecision.EXCLUDE


def test_audio_keeps_only_the_natively_certified_intervals() -> None:
    gate = BackendEligibilityGate(PrivacyMode.ENFORCE)
    chunk = _audio(eligible=[(T0_MS, T0_MS + 1800)])

    intervals = gate.certified_intervals(chunk)

    assert [(i.start_ms, i.end_ms) for i in intervals] == [(T0_MS, T0_MS + 1800)]


def test_audio_without_certified_intervals_keeps_nothing() -> None:
    gate = BackendEligibilityGate(PrivacyMode.ENFORCE)

    assert gate.certified_intervals(_audio(eligible=[])) == ()


@pytest.mark.parametrize(
    "overrides", [{"eligibility": "unknown"}, {"surface": "other"}]
)
def test_audio_from_an_ineligible_or_unknown_chunk_keeps_nothing(
    overrides: dict[str, str],
) -> None:
    gate = BackendEligibilityGate(PrivacyMode.ENFORCE)

    assert gate.certified_intervals(_audio(None, **overrides)) == ()


def test_gate_reports_its_mode_for_readiness() -> None:
    enforce = BackendEligibilityGate(PrivacyMode.ENFORCE).describe()
    permissive = BackendEligibilityGate(PrivacyMode.DEV_PERMISSIVE).describe()

    assert enforce == {"mode": "enforce", "native_gate_required": True}
    assert permissive == {"mode": "dev_permissive", "native_gate_required": False}
