"""Shared base for every Judge contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

ContractVersion = Literal["1"]
CONTRACT_VERSION: Final[ContractVersion] = "1"


class Contract(BaseModel):
    """Immutable, strict model: unknown fields are rejected, never ignored."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def ms_to_datetime(epoch_ms: int) -> datetime:
    """Convert capture-clock epoch milliseconds to an aware UTC datetime."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
