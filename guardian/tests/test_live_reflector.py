"""Live scenario: the Reflector reviews the seeded feedback session. Uses quota: run once.

Run with:  python -m pytest guardian/tests/test_live_reflector.py -m live -s
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from guardian import api, config
from guardian.datasource import SEED_DIR, FakeDataSource
from guardian.memory import inbox, proposals, reflector_files, reviews
from guardian.seed.install import install_initial_values

pytestmark = pytest.mark.live

logger = logging.getLogger("guardian.live")
USER = "demo-user"
SESSION = "s_001"  # 5 disagreements with comments, 3 "not for me" marks without


def seed(name: str) -> list[dict]:
    return json.loads((SEED_DIR / name).read_text())


def test_reflector_reviews_the_feedback_session_and_queues_cards():
    config.load_env()
    if not config.has_api_key():
        pytest.skip(f"{config.API_KEY_VAR} is not set")
    store = InMemoryStore()
    reels = [reel for reel in seed("reels.json") if reel["session_id"] == SESSION]
    datasource = FakeDataSource.from_records(
        USER, reels=reels, feedback=seed("feedback.json")
    )
    api.configure(store, InMemorySaver(), datasource)
    install_initial_values(store, datasource, USER, datetime.now(UTC))
    values_before = api.get_values(USER)

    reviewed = api.reflect_pending(USER)

    review = reviews.get_review(store, USER, SESSION)
    logger.info(
        "reviewed=%s status=%s attempts=%s", reviewed, review.status, review.attempts
    )
    for name in ("brief.md", "hypotheses.md", f"sessions/{SESSION}.md"):
        logger.info(
            "--- %s ---\n%s", name, reflector_files.read_file(store, USER, name)
        )
    pending = inbox.list_cards(store, USER, status=inbox.STATUS_PENDING)
    for card in pending:
        logger.info("--- card %s (%s) ---\n%s", card.id, card.kind, card.prompt)
        if card.proposal_id:
            proposal = proposals.get_proposal(store, USER, card.proposal_id)
            logger.info(
                "target=%s evidence=%s\n%s",
                proposal.target,
                proposal.evidence,
                proposal.proposed_text,
            )

    assert reviewed == 1
    assert api.get_values(USER) == values_before
    learned = [card for card in pending if card.kind == inbox.KIND_LEARNED]
    changes = [card for card in pending if card.kind == inbox.KIND_VALUE_CHANGE]
    assert len(learned) == 1 and learned[0].prompt.strip().endswith("?")
    assert 1 <= len(changes) <= 2
    cited = " ".join(
        proposals.get_proposal(store, USER, c.proposal_id).evidence for c in changes
    )
    assert "fb_" in cited
    brief = reflector_files.read_file(store, USER, reflector_files.BRIEF)
    assert 0 < len(brief.split()) <= reflector_files.BRIEF_WORD_CAP
