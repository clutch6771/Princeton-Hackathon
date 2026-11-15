"""Session contracts: lifecycle, ingest receipts and the completion event."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field

from judge.contracts.base import CONTRACT_VERSION, Contract, ContractVersion

SESSION_COMPLETED_EVENT = "session_completed"


class SessionStatus(StrEnum):
    """Lifecycle of a capture session."""

    ACTIVE = "active"
    ENDING = "ending"
    ENDED = "ended"


class SessionEndReason(StrEnum):
    """Why a session stopped accepting ingest."""

    CLIENT_END = "client_end"
    IDLE_TIMEOUT = "idle_timeout"


class SessionRecord(Contract):
    """One capture session: lifecycle, ordering cursor and counts."""

    session_id: UUID
    owner_id: str
    source_id: str
    status: SessionStatus = SessionStatus.ACTIVE
    started_at: datetime
    last_ingest_at: datetime
    ended_at: datetime | None = None
    end_reason: SessionEndReason | None = None
    next_order_seq: int = Field(default=1, ge=1)
    accounting_cursor: int = Field(
        default=0, ge=0, description="Last order_seq counted, in capture order."
    )
    judged_count: int = Field(default=0, ge=0)
    conflict_count: int = Field(default=0, ge=0)
    excluded_count: int = Field(default=0, ge=0)


class SessionCompletedPayload(Contract):
    """Payload of the lifecycle event written when a session completes."""

    reel_count: int = Field(ge=0)
    judged_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    end_reason: SessionEndReason
    started_at: datetime
    ended_at: datetime


class SessionEvent(Contract):
    """Lifecycle event for future consumers. Writing it invokes nobody."""

    contract_version: ContractVersion = CONTRACT_VERSION
    event_id: UUID
    session_id: UUID
    owner_id: str
    event_type: Literal["session_completed"] = "session_completed"
    payload: SessionCompletedPayload
    created_at: datetime


class IngestKind(StrEnum):
    """Which stream an ingest receipt belongs to."""

    FRAME = "frame"
    AUDIO = "audio"


class IngestReceipt(Contract):
    """Idempotency record for one capture event. Metadata only, never media."""

    event_id: UUID
    owner_id: str
    session_id: UUID
    kind: IngestKind
    source_seq: int
    capture_start_ms: int
    capture_end_ms: int
    received_at: datetime
    byte_length: int = Field(ge=0)
    outcome: str
    process_epoch: UUID = Field(
        description="Identity of the server process that held the media."
    )


class SessionEndRequest(Contract):
    """Body of ``POST /session/end``."""

    contract_version: ContractVersion = CONTRACT_VERSION
    session_id: UUID


class SessionEndResponse(Contract):
    """Response of ``POST /session/end``."""

    contract_version: ContractVersion = CONTRACT_VERSION
    session_id: UUID
    status: SessionStatus
