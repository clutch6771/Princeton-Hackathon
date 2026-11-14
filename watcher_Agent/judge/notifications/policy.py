"""Notification policy: pure decisions plus the versioned configuration service.

Interpretations where the PRD leaves mechanics open:

* Batches are non-overlapping, session-scoped, and hold only successfully
  judged reels, in capture order.
* A batch triggers when ``conflict_count >= minimum_conflicts``.
* Each complete batch is evaluated exactly once. While cooling down, or while
  another warning is still undelivered, the batch is recorded as suppressed
  and is never sent later.
* Cooldown is per user, across sessions.
* A policy change applies when the next batch opens.
"""

from __future__ import annotations

from datetime import datetime

from judge.config import Settings
from judge.contracts.notification import (
    BatchOutcome,
    NotificationPolicy,
    NotificationPolicyUpdate,
)
from judge.persistence.interfaces import Storage
from judge.runtime.clock import Clock

DEFAULT_POLICY_VERSION = 0


def default_policy(settings: Settings) -> NotificationPolicy:
    """The configured default (PRD: 15 reels, 3 conflicts, 600 seconds)."""
    return NotificationPolicy(
        version=DEFAULT_POLICY_VERSION,
        batch_size=settings.notification_batch_size,
        minimum_conflicts=settings.notification_min_conflicts,
        cooldown_seconds=settings.notification_cooldown_seconds,
    )


def is_cooling_down(
    now: datetime, last_dispatch_at: datetime | None, cooldown_seconds: int
) -> bool:
    """True while fewer than ``cooldown_seconds`` passed since a dispatch."""
    if last_dispatch_at is None:
        return False
    return (now - last_dispatch_at).total_seconds() < cooldown_seconds


def decide_batch(
    *,
    conflict_count: int,
    minimum_conflicts: int,
    cooling_down: bool,
    has_pending: bool,
) -> BatchOutcome:
    """Outcome of evaluating one complete batch."""
    if conflict_count < minimum_conflicts:
        return BatchOutcome.BELOW_THRESHOLD
    if has_pending:
        return BatchOutcome.SUPPRESSED_PENDING
    if cooling_down:
        return BatchOutcome.SUPPRESSED_COOLDOWN
    return BatchOutcome.TRIGGERED


class PolicyService:
    """Validated, versioned policy configuration for a future settings GUI."""

    def __init__(self, *, settings: Settings, storage: Storage, clock: Clock) -> None:
        self._default = default_policy(settings)
        self._storage = storage
        self._clock = clock

    async def get(self, owner_id: str) -> NotificationPolicy:
        """The owner's current policy, or the configured default."""
        async with self._storage.transaction() as tx:
            current = await tx.policy.get_current(owner_id)
        return current or self._default

    async def update(
        self, owner_id: str, update: NotificationPolicyUpdate
    ) -> NotificationPolicy:
        """Store a new policy version. Open batches keep their old policy."""
        async with self._storage.transaction() as tx:
            return await tx.policy.insert_next(
                owner_id,
                batch_size=update.batch_size,
                minimum_conflicts=update.minimum_conflicts,
                cooldown_seconds=update.cooldown_seconds,
                updated_at=self._clock.now(),
            )
