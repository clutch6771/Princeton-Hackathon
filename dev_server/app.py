"""TEMPORARY development server for integration checkpoint 1.

This is NOT the team's backend. It exists so the iOS app can reach the Guardian's
chat before the real backend serves these routes. Delete this folder once it does.

Four routes, one fixed demo user, in-memory storage, seed reel data:

    POST /chat          {"message": "...", "context": {...} or null}
    POST /chat/resume   {"action": "approve" | "edit" | "reject", "edited_text": ...}
    GET  /status        -> {"has_values": bool}
    GET  /memory        -> {"values.md": "...", "brief.md": "...", ...}

Run from the repository root:

    uvicorn dev_server.app:create_app --factory --host 0.0.0.0 --port 8000

Everything is lost when the server stops.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel

from guardian import api
from guardian.datasource import FakeDataSource
from guardian.seed.install import install_initial_values
from guardian.types import ChatContext, Decision

DEMO_USER = "demo-user"
SEEDED_VAR = "GUARDIAN_DEV_SEEDED"  # set to 1 to start with three approved values


class ChatRequest(BaseModel):
    message: str
    context: dict[str, Any] | None = None


class ResumeRequest(BaseModel):
    action: str
    edited_text: str | None = None


def create_app(
    chat_model: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    seeded: bool | None = None,
) -> FastAPI:
    """Build the app with fresh in-memory storage and the seed data."""
    now = clock or (lambda: datetime.now(UTC))
    store = InMemoryStore()
    datasource = FakeDataSource.from_seed(DEMO_USER, clock=now)
    api.configure(store, InMemorySaver(), datasource, clock=now, chat_model=chat_model)
    if seeded if seeded is not None else os.environ.get(SEEDED_VAR) == "1":
        install_initial_values(store, datasource, DEMO_USER, now())

    app = FastAPI(title="Guardian dev server (temporary)")

    def thread_id() -> str:
        return f"{DEMO_USER}:{now().date().isoformat()}"

    # Plain `def` routes: FastAPI runs them in a thread pool, because the Guardian's
    # functions block while they call the model.
    @app.post("/chat")
    def chat(body: ChatRequest) -> dict[str, Any]:
        # The Guardian validates the context and the decision itself.
        context = cast(ChatContext | None, body.context)
        return dict(api.chat(DEMO_USER, thread_id(), body.message, context))

    @app.post("/chat/resume")
    def resume(body: ResumeRequest) -> dict[str, Any]:
        decision = {"action": body.action, "edited_text": body.edited_text}
        return dict(api.resume(DEMO_USER, thread_id(), cast(Decision, decision)))

    @app.get("/status")
    def status() -> dict[str, bool]:
        return {"has_values": api.has_values(DEMO_USER)}

    @app.get("/memory")
    def memory() -> dict[str, str]:
        return api.get_memory(DEMO_USER)

    return app
