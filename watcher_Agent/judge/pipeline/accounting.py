"""Capture-ordered session counts, independent of notification policy."""

from judge.contracts.record import EvaluationStatus
from judge.contracts.session import SessionRecord
from judge.contracts.understanding import Verdict
from judge.persistence.interfaces import StorageTx


async def advance_accounting(tx: StorageTx, session: SessionRecord) -> SessionRecord:
    """Count newly terminal reels once, in capture order."""
    cursor = session.accounting_cursor
    judged, conflicts, excluded = (
        session.judged_count,
        session.conflict_count,
        session.excluded_count,
    )
    while True:
        # "Next reel after the cursor", not "cursor + 1": a deleted reel leaves
        # a gap in the numbering, and a gap must never stall the cursor.
        view = await tx.reels.next_accounting_view(session.session_id, cursor)
        if view is None or view.evaluation_status is EvaluationStatus.PENDING:
            break
        cursor = view.order_seq
        if view.evaluation_status is not EvaluationStatus.JUDGED:
            excluded += 1
            continue
        is_conflict = view.verdict is Verdict.CONFLICT
        judged, conflicts = judged + 1, conflicts + int(is_conflict)
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
