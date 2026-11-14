"""Ordered notification accounting.

Runs inside the transaction that commits a reel outcome, with the session row
locked. The cursor advances over contiguous terminal reels in capture order:
judged reels join the open batch, failed or insufficient ones are counted as
excluded coverage. A pending reel stops the cursor until it is finalized, and
a job deadline guarantees that it always is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from judge.contracts.notification import (
    BatchOutcome,
    BatchStatus,
    NotificationBatch,
    NotificationPolicy,
    OutboxItem,
)
from judge.contracts.record import EvaluationStatus
from judge.contracts.session import SessionRecord
from judge.contracts.understanding import Verdict
from judge.notifications.outbox import expire_due
from judge.notifications.policy import decide_batch, is_cooling_down
from judge.notifications.text import notification_text
from judge.persistence.interfaces import StorageTx


@dataclass(frozen=True, slots=True)
class AccountingConfig:
    """Settings the accounting step depends on."""

    default_policy: NotificationPolicy
    notification_ttl_seconds: int
    max_dispatch_attempts: int


async def _open_batch(
    tx: StorageTx, session: SessionRecord, config: AccountingConfig, now: datetime
) -> NotificationBatch:
    policy = await tx.policy.get_current(session.owner_id) or config.default_policy
    batch = NotificationBatch(
        batch_id=uuid4(),
        owner_id=session.owner_id,
        session_id=session.session_id,
        batch_index=await tx.notifications.count_batches(session.session_id) + 1,
        policy_version=policy.version,
        batch_size=policy.batch_size,
        minimum_conflicts=policy.minimum_conflicts,
        cooldown_seconds=policy.cooldown_seconds,
        opened_at=now,
    )
    await tx.notifications.insert_batch(batch)
    return batch


async def _evaluate(
    tx: StorageTx, batch: NotificationBatch, config: AccountingConfig, now: datetime
) -> NotificationBatch:
    state = await tx.notifications.lock_user_state(batch.owner_id)
    live = await expire_due(tx, batch.owner_id, now, config.max_dispatch_attempts)
    outcome = decide_batch(
        conflict_count=batch.conflict_count,
        minimum_conflicts=batch.minimum_conflicts,
        cooling_down=is_cooling_down(
            now, state.last_dispatch_at, batch.cooldown_seconds
        ),
        has_pending=bool(live),
    )
    if outcome is BatchOutcome.TRIGGERED:
        title, body = notification_text(
            conflict_count=batch.conflict_count, batch_size=batch.batch_size
        )
        await tx.notifications.insert_outbox(
            OutboxItem(
                notification_id=uuid4(),
                owner_id=batch.owner_id,
                session_id=batch.session_id,
                batch_id=batch.batch_id,
                title=title,
                body=body,
                reel_ids=tuple(await tx.reels.list_batch_conflict_ids(batch.batch_id)),
                cooldown_seconds=batch.cooldown_seconds,
                created_at=now,
                expires_at=now + timedelta(seconds=config.notification_ttl_seconds),
            )
        )
    return batch.model_copy(
        update={
            "status": BatchStatus.EVALUATED,
            "outcome": outcome,
            "evaluated_at": now,
        }
    )


async def advance_accounting(
    tx: StorageTx, session: SessionRecord, config: AccountingConfig, now: datetime
) -> SessionRecord:
    """Count newly terminal reels once, in capture order."""
    cursor = session.accounting_cursor
    judged, conflicts, excluded = (
        session.judged_count,
        session.conflict_count,
        session.excluded_count,
    )
    batch = await tx.notifications.get_open_batch(session.session_id)
    while True:
        # "Next reel after the cursor", not "cursor + 1": a deleted reel leaves
        # a gap in the numbering, and a gap must never stall the cursor.
        view = await tx.reels.next_accounting_view(session.session_id, cursor)
        if view is None or view.evaluation_status is EvaluationStatus.PENDING:
            break
        cursor = view.order_seq
        if batch is None:
            batch = await _open_batch(tx, session, config, now)
        if view.evaluation_status is not EvaluationStatus.JUDGED:
            excluded += 1
            batch = batch.model_copy(
                update={"excluded_count": batch.excluded_count + 1}
            )
            continue
        is_conflict = view.verdict is Verdict.CONFLICT
        judged, conflicts = judged + 1, conflicts + int(is_conflict)
        batch = batch.model_copy(
            update={
                "judged_count": batch.judged_count + 1,
                "conflict_count": batch.conflict_count + int(is_conflict),
            }
        )
        await tx.reels.assign_batch(view.reel_id, batch.batch_id, batch.judged_count)
        if batch.is_full:
            await tx.notifications.save_batch(await _evaluate(tx, batch, config, now))
            batch = None
    if batch is not None:
        await tx.notifications.save_batch(batch)
    await tx.sessions.save_accounting(
        session.session_id,
        cursor=cursor,
        judged_count=judged,
        conflict_count=conflicts,
        excluded_count=excluded,
    )
    return session.model_copy(
        update={
            "accounting_cursor": cursor,
            "judged_count": judged,
            "conflict_count": conflicts,
            "excluded_count": excluded,
        }
    )


async def close_incomplete_batch(
    tx: StorageTx, session_id: UUID, now: datetime
) -> None:
    """Session ended: an incomplete tail never produces a warning."""
    batch = await tx.notifications.get_open_batch(session_id)
    if batch is None:
        return
    await tx.notifications.save_batch(
        batch.model_copy(
            update={
                "status": BatchStatus.EVALUATED,
                "outcome": BatchOutcome.INCOMPLETE_TAIL,
                "evaluated_at": now,
            }
        )
    )
