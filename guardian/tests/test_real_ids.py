"""Tests with IDs as the real Judge writes them: reels, sessions and feedback are UUIDs.

The seed data uses short IDs (rl_001, fb_001, s_001). The checks must hold for both.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from guardian.datasource import FakeDataSource
from guardian.memory import checks, proposals, records, reviews
from guardian.tests.conftest import NOW, USER

REEL = "be395b2d-64e2-5cae-833a-bd7a0bec4401"
OTHER_REEL = "0b9f4c2e-6a1d-4e57-9c3b-1f2a3b4c5d6e"
SESSION = "d9616ad1-b79b-4122-85df-3b633690ad28"
OTHER_SESSION = "7d1e2f3a-4b5c-4d6e-8f90-a1b2c3d4e5f6"
FEEDBACK = "3c2b1a09-8f7e-4d6c-9b5a-4f3e2d1c0b9a"
UNKNOWN = "ffffffff-ffff-4fff-8fff-ffffffffffff"
THREAD = f"{USER}:2026-11-14"


def reel(reel_id: str, session_id: str, minutes_ago: int) -> dict:
    start = NOW - timedelta(minutes=minutes_ago)
    return {
        "id": reel_id,
        "session_id": session_id,
        "start": start,
        "end": start + timedelta(seconds=12),
        "description": "Before-and-after body transformation montage",
        "transcript": "Ninety days, that's all it took me.",
        "verdict": "conflict",
        "confidence": 0.8,
        "value_ids": ["V1"],
        "reason": "Shows body comparison.",
    }


@pytest.fixture
def judge_like(clock) -> FakeDataSource:
    """Records shaped like the Judge's: every ID is a lower-case UUID."""
    return FakeDataSource.from_records(
        USER,
        reels=[reel(REEL, SESSION, 120), reel(OTHER_REEL, OTHER_SESSION, 60)],
        feedback=[
            {
                "id": FEEDBACK,
                "reel_id": REEL,
                "ts": NOW - timedelta(minutes=100),
                "label": "disagree",
                "comment": "Not a conflict",
                "source": "app",
            }
        ],
        clock=clock,
        unlisted_sessions=frozenset(),
    )


def test_uuids_are_recognised_as_cited_ids_alongside_seed_ids():
    text = f"see {REEL.upper()} and rl_001, then {FEEDBACK}; approved 2026-11-14"

    assert checks.cited_ids(text) == [REEL, "rl_001", FEEDBACK]


def test_cited_uuids_must_exist_for_this_user(judge_like):
    known = f"reel {REEL}, feedback {FEEDBACK}, session {SESSION}"

    assert checks.missing_evidence_ids(judge_like, USER, known) == []
    assert checks.missing_evidence_ids(judge_like, USER, REEL.upper()) == []
    assert checks.missing_evidence_ids(judge_like, USER, f"{REEL}, {UNKNOWN}") == [
        UNKNOWN
    ]
    assert checks.missing_evidence_ids(judge_like, "someone-else", REEL) == [REEL]


def test_a_proposal_citing_a_uuid_that_does_not_exist_is_refused(store, judge_like):
    def propose(evidence: str) -> proposals.CreateResult:
        text = f"""## New: Less body comparison (strength: high)
- Want more: none
- Want less: before/after transformations
- Unless: none
- Suggested wording: "I want less body comparison"
- Evidence: {evidence}"""
        return proposals.create_proposal(
            store,
            judge_like,
            USER,
            value_id="new",
            new_text=text,
            evidence=evidence,
            source=proposals.chat_source(THREAD, f"call-{evidence[:8]}"),
            now=NOW,
        )

    refused = propose(f"disagreed with {UNKNOWN}")
    accepted = propose(f"disagreed with {REEL}")

    assert refused.proposal is None and UNKNOWN in refused.error
    assert accepted.error is None


def test_a_summary_bullet_citing_a_uuid_that_does_not_exist_fails_the_review(
    judge_like,
):
    def missing(bullet: str) -> list[str]:
        return checks.missing_evidence_ids(judge_like, USER, bullet)

    good = f"## What I learned\n- You disagreed with one flag. [{FEEDBACK}]"
    bad = f"## What I learned\n- You disagreed with one flag. [{UNKNOWN}]"

    assert reviews.parse_summary(good, missing).bullets
    with pytest.raises(reviews.ReviewError, match="does not exist"):
        reviews.parse_summary(bad, missing)


def test_sessions_seen_counts_distinct_uuid_sessions_and_not_reels():
    hypotheses = f"""## H1: Comparison content late at night
- Evidence: {SESSION} (reel {REEL}), {OTHER_SESSION}, {SESSION}
- Sessions seen: 9 · Last seen: 2026-11-14
- Status: watching"""

    normalized = reviews.normalize_hypotheses(
        hypotheses, session_ids={SESSION, OTHER_SESSION}
    )

    assert "- Sessions seen: 2 · Last seen: 2026-11-14" in normalized


def test_a_quote_check_is_unaffected_by_uuids_in_the_text(store, judge_like):
    records.append_chat_turn(store, USER, records.ROLE_USER, "I want less of it", NOW)

    assert checks.quote_found(
        "i want less of it", checks.user_statements(store, judge_like, USER)
    )
