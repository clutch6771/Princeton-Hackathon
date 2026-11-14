"""Acknowledgements for externally captured media; no notification delivery."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from judge.contracts.base import CONTRACT_VERSION, Contract, ContractVersion


class AckStatus(StrEnum):
    """What happened to an uploaded capture event.

    ``accepted`` means admission to ephemeral, in-memory processing. It is
    not durable storage of the media.
    """

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    LATE_DISCARDED = "late_discarded"
    PRIVACY_EXCLUDED = "privacy_excluded"
    CORRUPT_DISCARDED = "corrupt_discarded"


class IngestResponse(Contract):
    """Acknowledgement of an externally captured media upload."""

    contract_version: ContractVersion = CONTRACT_VERSION
    event_id: UUID
    session_id: UUID
    status: AckStatus
    prior_outcome: str | None = Field(
        default=None, description="For duplicates: outcome of the first delivery."
    )
    admitted_by_current_process: bool = Field(
        default=True,
        description=(
            "False when the first delivery was acknowledged by a server process "
            "that has since restarted; that media is not recoverable."
        ),
    )
