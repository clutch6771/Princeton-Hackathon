"""Backend defense-in-depth eligibility gate.

Two boundaries exist:

1. The native pre-upload ``FeedEligibilityGate`` (future adapter). It decides
   what may be uploaded at all and certifies audio intervals.
2. This backend gate. It re-checks the provenance of every event and drops
   anything that is not positively eligible Instagram Reels media *before*
   any provider request is built.

The backend gate never sends media anywhere to decide eligibility, and it
cannot undo an upload of private media: that guarantee belongs to the native
gate. In ``enforce`` mode an upload without a native gate is rejected with
``privacy_capability_unavailable``. ``dev_permissive`` additionally accepts
the development fake gate and is refused outside ``APP_ENV=development``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from judge.config import PrivacyMode
from judge.contracts.capture import (
    AudioChunk,
    Eligibility,
    GateCapability,
    PrivacyProvenance,
    Surface,
    TimeInterval,
)
from judge.errors import PrivacyCapabilityUnavailableError


class GateDecision(StrEnum):
    """What to do with an event's media."""

    ADMIT = "admit"
    EXCLUDE = "exclude"


@dataclass(frozen=True, slots=True)
class FrameScreening:
    """Outcome of screening one event's privacy provenance."""

    decision: GateDecision
    reason: str


class BackendEligibilityGate:
    """Admits only natively certified, positively eligible Reels media."""

    def __init__(self, mode: PrivacyMode) -> None:
        self._mode = mode

    def _require_capability(self, privacy: PrivacyProvenance) -> None:
        trusted = {GateCapability.NATIVE_GATE}
        if self._mode is PrivacyMode.DEV_PERMISSIVE:
            trusted.add(GateCapability.DEV_FAKE)
        if privacy.capability not in trusted:
            msg = (
                "privacy capability unavailable: this upload was not certified "
                "by a trusted feed-eligibility gate"
            )
            raise PrivacyCapabilityUnavailableError(msg)

    def screen_frame(self, privacy: PrivacyProvenance) -> FrameScreening:
        """Decide whether an event's media may be kept in volatile memory."""
        self._require_capability(privacy)
        if privacy.eligibility is not Eligibility.ELIGIBLE:
            return FrameScreening(
                GateDecision.EXCLUDE, f"eligibility_{privacy.eligibility.value}"
            )
        if privacy.surface is not Surface.INSTAGRAM_REELS:
            return FrameScreening(
                GateDecision.EXCLUDE, f"surface_{privacy.surface.value}"
            )
        return FrameScreening(GateDecision.ADMIT, "eligible_reels")

    def certified_intervals(self, chunk: AudioChunk) -> tuple[TimeInterval, ...]:
        """Capture-clock intervals of a chunk whose samples may be kept.

        A frame sampled every two seconds cannot certify the audio between
        frames, so only intervals certified by the native gate are kept.
        """
        screening = self.screen_frame(chunk.privacy)
        if screening.decision is not GateDecision.ADMIT:
            return ()
        return chunk.eligible_intervals

    def describe(self) -> dict[str, object]:
        """Privacy capability summary for the readiness endpoint."""
        return {
            "mode": self._mode.value,
            "native_gate_required": self._mode is PrivacyMode.ENFORCE,
        }
