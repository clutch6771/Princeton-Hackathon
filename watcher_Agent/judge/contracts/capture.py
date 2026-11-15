"""Capture contracts: the metadata a native adapter uploads with each event.

The media itself (one JPEG, or one AAC chunk) travels as a separate binary
multipart part and exists only in volatile memory on the server.

Timestamps named ``capture_*`` are integer epoch milliseconds on the capture
device's clock. They align frames with audio; they are never compared with
server time directly (see ``judge.ingest.clock_mapping``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from judge.contracts.base import CONTRACT_VERSION, Contract, ContractVersion

MAX_INTERVALS_PER_CHUNK = 16


class Eligibility(StrEnum):
    """Result of the native feed-eligibility gate for one event."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class GateCapability(StrEnum):
    """Which kind of gate produced the privacy provenance."""

    NATIVE_GATE = "native_gate"
    DEV_FAKE = "dev_fake"
    NONE = "none"


class Surface(StrEnum):
    """The on-screen surface the gate recognised."""

    INSTAGRAM_REELS = "instagram_reels"
    OTHER = "other"
    UNKNOWN = "unknown"


class AudioContainer(StrEnum):
    """Container of an uploaded AAC chunk. Each chunk decodes independently."""

    ADTS = "adts"
    MP4 = "mp4"


class AudioCaptureState(StrEnum):
    """What the native adapter knows about app-audio availability."""

    AVAILABLE = "available"
    PROTECTED = "protected"
    UNAVAILABLE = "unavailable"


class TimeInterval(Contract):
    """Half-open capture-clock interval ``[start_ms, end_ms)``."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _positive_duration(self) -> Self:
        if self.end_ms <= self.start_ms:
            msg = "end_ms must be greater than start_ms"
            raise ValueError(msg)
        return self

    @property
    def duration_ms(self) -> int:
        """Length of the interval in milliseconds."""
        return self.end_ms - self.start_ms


class PrivacyProvenance(Contract):
    """Evidence about how (and whether) an event was privacy-screened."""

    gate_id: str = Field(min_length=1, max_length=64)
    gate_version: str = Field(min_length=1, max_length=32)
    capability: GateCapability
    eligibility: Eligibility
    surface: Surface
    method: str = Field(min_length=1, max_length=64)


class CaptureEvent(Contract):
    """Fields common to every uploaded capture event."""

    contract_version: ContractVersion = CONTRACT_VERSION
    event_id: UUID
    session_id: UUID
    source_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    source_seq: int = Field(ge=0)
    privacy: PrivacyProvenance


class FrameObservation(CaptureEvent):
    """Metadata for one sampled JPEG frame (nominally one every 2 seconds)."""

    capture_ts_ms: int = Field(gt=0)
    mime_type: Literal["image/jpeg"]
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    transition_at_ms: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Capture time of a reel-change transition the native adapter saw "
            "since the previous uploaded frame; null when none or unsupported."
        ),
    )

    @model_validator(mode="after")
    def _transition_not_after_frame(self) -> Self:
        if self.transition_at_ms is not None and (
            self.transition_at_ms > self.capture_ts_ms
        ):
            msg = "transition_at_ms must not be later than capture_ts_ms"
            raise ValueError(msg)
        return self


class AudioChunk(CaptureEvent):
    """Metadata for one app-audio chunk: 16 kHz mono AAC, nominally 5 seconds."""

    capture_start_ms: int = Field(gt=0)
    capture_end_ms: int = Field(gt=0)
    codec: Literal["aac"]
    container: AudioContainer
    sample_rate_hz: Literal[16000]
    channels: Literal[1]
    priming_samples: int = Field(
        default=0,
        ge=0,
        le=8192,
        description="Leading decoder-delay samples to discard for this chunk.",
    )
    capture_state: AudioCaptureState = AudioCaptureState.AVAILABLE
    eligible_intervals: tuple[TimeInterval, ...] = Field(
        default=(),
        max_length=MAX_INTERVALS_PER_CHUNK,
        description=(
            "Capture-clock intervals inside this chunk that the native gate "
            "certified as eligible Reels audio. Everything else is discarded."
        ),
    )

    @model_validator(mode="after")
    def _check_span_and_intervals(self) -> Self:
        if self.capture_end_ms <= self.capture_start_ms:
            msg = "capture_end_ms must be greater than capture_start_ms"
            raise ValueError(msg)
        cursor = self.capture_start_ms
        for interval in self.eligible_intervals:
            if interval.start_ms < cursor or interval.end_ms > self.capture_end_ms:
                msg = (
                    "eligible_intervals must be sorted, non-overlapping and "
                    "inside the chunk span"
                )
                raise ValueError(msg)
            cursor = interval.end_ms
        return self

    @property
    def span(self) -> TimeInterval:
        """The capture-clock interval this chunk covers."""
        return TimeInterval(start_ms=self.capture_start_ms, end_ms=self.capture_end_ms)
