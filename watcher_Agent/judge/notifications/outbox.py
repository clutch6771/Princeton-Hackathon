"""Outbox delivery through ingest responses.

The backend cannot push to the device and cannot see whether a banner was
shown. Delivery is therefore: return a pending warning in the next ingest (or
session-end) response, keep a short lease, and return the same stable id again
if no acknowledgment arrives. A lost HTTP response is not proof of display.
Native adapters must also deduplicate by id and honour expiry and cooldown.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from judge.config import Settings
from judge.contracts.notification import (
    NotificationEnvelope,
    OutboxItem,
    OutboxStatus,
)
from judge.notifications.policy import is_cooling_down
from judge.persistence.interfaces import StorageTx


def _close(item: OutboxItem, status: OutboxStatus, reason: str) -> OutboxItem:
    return item.model_copy(
        update={"status": status, "closed_reason": reason, "lease_until": None}
    )


async def expire_due(
    tx: StorageTx, owner_id: str, now: datetime, max_attempts: int
) -> list[OutboxItem]:
    """Close expired or exhausted items; return those still deliverable."""
    live: list[OutboxItem] = []
    for item in await tx.notifications.list_undelivered(owner_id):
        lease_over = item.lease_until is None or item.lease_until <= now
        if item.expires_at <= now:
            await tx.notifications.save_outbox(
                _close(item, OutboxStatus.EXPIRED, "expired")
            )
        elif (
            item.status is OutboxStatus.LEASED
            and lease_over
            and item.dispatch_attempts >= max_attempts
        ):
            await tx.notifications.save_outbox(
                _close(item, OutboxStatus.UNACKNOWLEDGED, "attempts_exhausted")
            )
        else:
            live.append(item)
    return live


async def cancel_for_reels(
    tx: StorageTx, owner_id: str, reel_ids: Sequence[UUID]
) -> int:
    """Cancel undelivered warnings that mention deleted reels."""
    doomed = set(reel_ids)
    cancelled = 0
    for item in await tx.notifications.list_undelivered(owner_id):
        if doomed.intersection(item.reel_ids):
            await tx.notifications.save_outbox(
                _close(item, OutboxStatus.CANCELLED, "reel_deleted")
            )
            cancelled += 1
    return cancelled


class NotificationDispatcher:
    """Acknowledges, leases and returns at most one warning per response."""

    def __init__(self, settings: Settings) -> None:
        self._lease = timedelta(seconds=settings.notification_lease_seconds)
        self._max_attempts = settings.notification_max_dispatch_attempts

    async def acknowledge_and_dispatch(
        self,
        tx: StorageTx,
        owner_id: str,
        acknowledged: Sequence[UUID],
        now: datetime,
    ) -> NotificationEnvelope | None:
        """Process acknowledgments, then lease the next deliverable warning."""
        await self._acknowledge(tx, owner_id, acknowledged, now)
        state = await tx.notifications.lock_user_state(owner_id)
        live = await expire_due(tx, owner_id, now, self._max_attempts)
        for item in live:
            if item.status is OutboxStatus.LEASED:
                if item.lease_until is not None and item.lease_until > now:
                    return None
                return await self._lease_item(tx, item, now)
            if is_cooling_down(now, state.last_dispatch_at, item.cooldown_seconds):
                await tx.notifications.save_outbox(
                    _close(item, OutboxStatus.CANCELLED, "cooldown_at_dispatch")
                )
                continue
            await tx.notifications.set_last_dispatch(owner_id, now)
            return await self._lease_item(tx, item, now)
        return None

    async def _acknowledge(
        self, tx: StorageTx, owner_id: str, acknowledged: Sequence[UUID], now: datetime
    ) -> None:
        for notification_id in acknowledged:
            item = await tx.notifications.get_outbox(owner_id, notification_id)
            if item is None or not item.status.is_undelivered:
                continue
            await tx.notifications.save_outbox(
                item.model_copy(
                    update={
                        "status": OutboxStatus.ACKNOWLEDGED,
                        "acknowledged_at": now,
                        "lease_until": None,
                    }
                )
            )

    async def _lease_item(
        self, tx: StorageTx, item: OutboxItem, now: datetime
    ) -> NotificationEnvelope:
        leased = item.model_copy(
            update={
                "status": OutboxStatus.LEASED,
                "lease_until": now + self._lease,
                "dispatch_attempts": item.dispatch_attempts + 1,
                "dispatched_at": item.dispatched_at or now,
            }
        )
        await tx.notifications.save_outbox(leased)
        return leased.envelope()
