"""Notification contracts: policy, batches, outbox and the response envelope.

Warnings explain conflicts with the user's approved values. They never
suppress content and never describe content as universally bad.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from judge.contracts.base import CONTRACT_VERSION, Contract, ContractVersion

MAX_BATCH_SIZE = 100
MAX_COOLDOWN_SECONDS = 86_400


class NotificationEnvelope(Contract):
    """The ``notify`` object returned at the root of ingest responses."""

    title: str
    body: str
    reel_ids: tuple[UUID, ...]
    notification_id: UUID = Field(description="Stable across redelivery.")
    expires_at: datetime


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
    """Response of both ingest endpoints. ``notify`` stays at the root."""

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
    notify: NotificationEnvelope | None = None


class _PolicyFields(Contract):
    batch_size: int = Field(ge=1, le=MAX_BATCH_SIZE)
    minimum_conflicts: int = Field(ge=1)
    cooldown_seconds: int = Field(ge=0, le=MAX_COOLDOWN_SECONDS)

    @model_validator(mode="after")
    def _conflicts_within_batch(self) -> Self:
        if self.minimum_conflicts > self.batch_size:
            msg = "minimum_conflicts must satisfy 1 <= minimum_conflicts <= batch_size"
            raise ValueError(msg)
        return self


class NotificationPolicyUpdate(_PolicyFields):
    """Body of ``PUT /config/notification-policy``."""

    contract_version: ContractVersion = CONTRACT_VERSION


class NotificationPolicy(_PolicyFields):
    """One immutable, versioned notification policy."""

    version: int = Field(ge=0, description="0 means the configured default.")
    updated_at: datetime | None = None


class BatchStatus(StrEnum):
    """Lifecycle of a notification batch."""

    OPEN = "open"
    EVALUATED = "evaluated"


class BatchOutcome(StrEnum):
    """Result of evaluating one complete (or abandoned) batch, exactly once."""

    TRIGGERED = "triggered"
    BELOW_THRESHOLD = "below_threshold"
    SUPPRESSED_COOLDOWN = "suppressed_cooldown"
    SUPPRESSED_PENDING = "suppressed_pending"
    INCOMPLETE_TAIL = "incomplete_tail"


class NotificationBatch(Contract):
    """A non-overlapping, session-scoped batch of judged reels."""

    batch_id: UUID
    owner_id: str
    session_id: UUID
    batch_index: int = Field(ge=1)
    policy_version: int = Field(ge=0)
    batch_size: int = Field(ge=1)
    minimum_conflicts: int = Field(ge=1)
    cooldown_seconds: int = Field(ge=0)
    status: BatchStatus = BatchStatus.OPEN
    judged_count: int = Field(default=0, ge=0)
    conflict_count: int = Field(default=0, ge=0)
    excluded_count: int = Field(
        default=0, ge=0, description="Failed or insufficient observations."
    )
    outcome: BatchOutcome | None = None
    opened_at: datetime
    evaluated_at: datetime | None = None

    @property
    def is_full(self) -> bool:
        """True once the batch holds ``batch_size`` judged reels."""
        return self.judged_count >= self.batch_size


class OutboxStatus(StrEnum):
    """Delivery state of one warning."""

    PENDING = "pending"
    LEASED = "leased"
    ACKNOWLEDGED = "acknowledged"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    UNACKNOWLEDGED = "unacknowledged"

    @property
    def is_undelivered(self) -> bool:
        """Still eligible for (re)delivery through an ingest response."""
        return self in {OutboxStatus.PENDING, OutboxStatus.LEASED}


class OutboxItem(Contract):
    """One warning awaiting delivery through an ingest response."""

    notification_id: UUID
    owner_id: str
    session_id: UUID
    batch_id: UUID
    title: str
    body: str
    reel_ids: tuple[UUID, ...]
    cooldown_seconds: int = Field(ge=0)
    status: OutboxStatus = OutboxStatus.PENDING
    created_at: datetime
    expires_at: datetime
    lease_until: datetime | None = None
    dispatch_attempts: int = Field(default=0, ge=0)
    dispatched_at: datetime | None = None
    acknowledged_at: datetime | None = None
    closed_reason: str | None = None

    def envelope(self) -> NotificationEnvelope:
        """The client-facing view of this warning."""
        return NotificationEnvelope(
            title=self.title,
            body=self.body,
            reel_ids=self.reel_ids,
            notification_id=self.notification_id,
            expires_at=self.expires_at,
        )


class UserNotificationState(Contract):
    """Per-user cooldown anchor, shared across sessions."""

    owner_id: str
    last_dispatch_at: datetime | None = None
