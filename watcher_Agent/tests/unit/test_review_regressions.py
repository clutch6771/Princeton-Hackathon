"""Regression tests for defects found in code review."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from judge.contracts.record import EvaluationStatus
from judge.contracts.understanding import Verdict
from judge.devfakes.memory_storage import MemoryStorage
from tests.support.builders import SESSION_ID
from tests.support.harness import Harness
from tests.support.records import make_judged, make_pending, make_session

pytestmark = pytest.mark.unit


class FlakyStorage(MemoryStorage):
    """Memory storage whose commits can be made to fail."""

    fail_commits = False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        async with super().transaction() as tx:
            yield tx
            if self.fail_commits:
                raise ConnectionError("synthetic commit failure")


async def test_failed_commit_releases_the_reserved_media_budget() -> None:
    storage = FlakyStorage()
    h = await Harness.create(storage=storage)
    try:
        storage.fail_commits = True
        for offset in (0, 2000, 4000):
            with pytest.raises(ConnectionError):
                await h.frame(offset, 1)
        with pytest.raises(ConnectionError):
            await h.audio(0, seconds=5.0)

        assert h.runtime.budget.used == 0
        assert len(h.runtime.volatile) == 0
    finally:
        storage.fail_commits = False
        await h.close()


async def test_one_failing_submission_does_not_strand_the_other_reels() -> None:
    storage = FlakyStorage()
    h = await Harness.create(
        storage=storage,
        worker_queue_capacity=1,
        final_audio_wait_seconds=50,
        job_deadline_seconds=500,
    )
    try:
        await h.runtime.queue.stop()
        for scene, start in ((1, 0), (2, 6000), (3, 12_000)):
            await h.play_reel(scene, start, audio=False)
        await h.frame(18_000, 4)
        held_before = h.runtime.budget.used
        h.clock.advance(60)
        storage.fail_commits = True

        # Reel 1 is queued; recording reels 2 and 3 as skipped fails. The
        # finalizer must carry on and release their frames regardless.
        finalized = await h.runtime.maintenance.finalizer.process(SESSION_ID)
        storage.fail_commits = False

        state = h.runtime.volatile.get(SESSION_ID)
        assert finalized == 3
        assert state is not None and state.closed == ()
        assert h.runtime.queue.depth == 1
        assert h.runtime.budget.used < held_before
    finally:
        storage.fail_commits = False
        await h.close()


async def test_a_deleted_reel_does_not_stall_ordered_accounting() -> None:
    h = await Harness.create()
    try:
        session = make_session(owner_id=h.owner)
        pending = [make_pending(session, order_seq=i) for i in (1, 2, 3)]
        async with h.runtime.storage.transaction() as tx:
            await tx.sessions.insert(session)
            for reel in pending:
                await tx.reels.insert_pending(reel)

        await h.runtime.deletion.delete_reel(h.owner, pending[0].reel_id)
        await h.runtime.completion.complete(make_judged(pending[1], Verdict.CONFLICT))
        await h.runtime.completion.complete(make_judged(pending[2]))

        async with h.runtime.storage.transaction() as tx:
            loaded = await tx.sessions.get(session.session_id)
            record = await tx.reels.get(h.owner, pending[2].reel_id)
        assert loaded is not None
        assert (loaded.accounting_cursor, loaded.judged_count) == (3, 2)
        assert loaded.conflict_count == 1
        assert record is not None
        assert record.evaluation_status is EvaluationStatus.JUDGED
    finally:
        await h.close()
