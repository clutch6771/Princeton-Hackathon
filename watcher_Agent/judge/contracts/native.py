"""Device-segmented reel upload used by the current Swift app."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from judge.contracts.capture import (
    Eligibility,
    GateCapability,
    PrivacyProvenance,
    Surface,
)


class NativeReelUpload(BaseModel):
    """Current Swift payload; goals and sensitivity never become approved values."""

    model_config = ConfigDict(extra="ignore")
    session_id: UUID
    reel_index: int = Field(ge=1)
    dwell_seconds: float = Field(gt=0, le=90, allow_inf_nan=False)
    started_at: datetime | None = None
    frame_base64: str | None = Field(default=None, max_length=400_000)
    transcript_text: str | None = Field(default=None, max_length=4000)
    privacy: PrivacyProvenance = Field(
        default_factory=lambda: PrivacyProvenance(
            gate_id="ios-legacy-demo",
            gate_version="1",
            capability=GateCapability.DEV_FAKE,
            eligibility=Eligibility.ELIGIBLE,
            surface=Surface.INSTAGRAM_REELS,
            method="development-only-legacy-adapter",
        )
    )
