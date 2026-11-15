"""Fixtures for the Guardian router tests. A scripted model stands in for Gemini."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

from guardian.datasource import FakeDataSource
from guardian.memory import inbox, locks, proposals
from guardian.tests.fakes import ScriptedChatModel
from guardian_router import background
from guardian_router.standalone import create_app

NOW = datetime(2026, 11, 14, 21, 0, tzinfo=UTC)
USER = "demo-user"
HEADERS = {"X-Demo-User-Id": USER}

HUSTLE = """## New: Less get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, get-rich-quick pitches
- Unless: none
- Suggested wording: "I don't want hustle and get-rich-quick videos"
- Evidence: fb_002, fb_004, fb_007"""
HUSTLE_PROMPT = (
    "You marked 3 hustle reels as not for you. I've suggested wording below."
)
LEARNED = "- You marked 3 hustle reels as not for you. [fb_002, fb_004, fb_007]\n\nWhat made them easy to skip?"


@dataclass
class Server:
    """A standalone app under test, with handles on what the tests need to inspect."""

    client: TestClient
    model: ScriptedChatModel
    store: InMemoryStore
    datasource: FakeDataSource
    launched: list[Callable[[], Any]] = field(default_factory=list)

    def say(self, message: str, **body: Any) -> str:
        """Send one message through the app's chat route and return the reply text."""
        payload = {"message": message, "profile": {"stated_goals": ""}, **body}
        response = self.client.post("/agent2/chat", json=payload, headers=HEADERS)
        if response.status_code != 200:
            raise AssertionError(response.text)
        return response.json()["text"]

    def add_learned_card(self, when: datetime = NOW) -> str:
        card = inbox.new_card(
            USER,
            inbox.KIND_LEARNED,
            LEARNED,
            when,
            card_id=inbox.learned_card_id("s_001"),
            session_id="s_001",
        )
        inbox.add_card(self.store, card)
        return card.id

    def add_value_card(self, when: datetime = NOW, text: str = HUSTLE) -> str:
        created = proposals.create_proposal(
            self.store,
            self.datasource,
            USER,
            value_id="new",
            new_text=text,
            evidence="fb_002, fb_004, fb_007",
            source=proposals.reflector_source("s_001", "run_1"),
            now=when,
        )
        if created.proposal is None:
            raise AssertionError(created.error)
        card = inbox.new_card(
            USER,
            inbox.KIND_VALUE_CHANGE,
            HUSTLE_PROMPT,
            when,
            proposal_id=created.proposal.id,
            session_id="s_001",
        )
        inbox.add_card(self.store, card)
        return card.id


@pytest.fixture
def serve() -> Callable[..., Server]:
    """Build a standalone app with a scripted model. Background work is recorded, not run."""

    def build(
        script: list[AIMessage] | None = None, seeded: bool = False, model: Any = None
    ) -> Server:
        model = model or ScriptedChatModel(script=script or [])
        store = InMemoryStore()
        launched: list[Callable[[], Any]] = []
        app = create_app(
            chat_model=model,
            clock=lambda: NOW,
            seeded=seeded,
            store=store,
            launch=launched.append,
        )
        return Server(
            client=TestClient(app),
            model=model,
            store=store,
            datasource=app.state.guardian_datasource,
            launched=launched,
        )

    return build


@pytest.fixture(autouse=True)
def clean_process_state() -> Iterator[None]:
    yield
    locks.set_provider(locks.LocalLocks())
    background.reset()


def pytest_configure(config: pytest.Config) -> None:
    # Shared with guardian/tests, whose fixtures these tests import.
    config.addinivalue_line(
        "markers",
        "postgres: needs TEST_DATABASE_URL pointing at a disposable Postgres database.",
    )
