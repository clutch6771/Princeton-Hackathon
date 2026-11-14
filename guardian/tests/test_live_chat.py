"""Live scenario: the values interview on the real model. Uses quota: run once.

Run with:  python -m pytest guardian/tests/test_live_chat.py -m live -s
"""

from __future__ import annotations

import logging

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from guardian import api, config
from guardian.datasource import FakeDataSource
from guardian.memory import formats, values

pytestmark = pytest.mark.live

logger = logging.getLogger("guardian.live")
USER = "demo-user"
THREAD = f"{USER}:live-interview"
MAX_TURNS_BEFORE_A_CARD = 5

# What a user plausibly says, in order, until the Guardian proposes a value.
USER_TURNS = [
    "hi",
    "I want to see less body comparison content. Gym stuff is great, comparing bodies is not.",
    "The last one was a before and after transformation video with someone flexing at the end.",
    "Form tutorials and training plans are fine, I'd still want those.",
    "Yes, please propose that.",
]


@pytest.fixture
def live_api() -> InMemoryStore:
    config.load_env()
    if not config.has_api_key():
        pytest.skip(f"{config.API_KEY_VAR} is not set")
    store = InMemoryStore()
    api.configure(store, InMemorySaver(), FakeDataSource.from_seed(USER))
    return store


def test_interview_produces_a_value_card_and_approval_writes_a_valid_entry(
    live_api: InMemoryStore,
):
    card = None
    for turn, message in enumerate(USER_TURNS[:MAX_TURNS_BEFORE_A_CARD], start=1):
        response = api.chat(USER, THREAD, message)
        logger.info("turn %d user: %s", turn, message)
        logger.info(
            "turn %d guardian: %s | card: %s | error: %s",
            turn,
            response["reply"],
            bool(response["pending"]),
            response["error"],
        )
        assert response["error"] is None
        if response["pending"]:
            card = response["pending"]["value_change"]
            break

    assert card is not None, "no value card after the scripted interview"
    logger.info("card text:\n%s", card["new_text"])
    assert formats.parse_entry(card["new_text"]).ok

    resumed = api.resume(USER, THREAD, {"action": "approve", "edited_text": None})

    logger.info("after approval: %s | error: %s", resumed["reply"], resumed["error"])
    assert resumed["error"] is None and resumed["pending"] is None
    stored = values.list_values(live_api, USER)
    assert [value.id for value in stored] == ["V1"]
    assert formats.parse_entry(stored[0].text).ok
    assert api.get_values(USER) == stored[0].text and api.has_values(USER)
    logger.info("stored value:\n%s", stored[0].text)
