"""The Guardian router as a server of its own, on seed data.

For trying the app's chat before the team's server includes the router. It serves the
Guardian routes for one seeded demo user, with everything kept in memory, plus the two
calls the app makes around them: GET /health and POST /session/end (which starts a
review, as the team's server should). It has no Judge, timeline or feedback route.

Run from the repository root:

    uvicorn guardian_router.standalone:create_app --factory --host 0.0.0.0 --port 8000

Set GUARDIAN_DEV_SEEDED=1 to start with three approved values. Everything is lost
when the server stops.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from guardian import api
from guardian.datasource import FakeDataSource
from guardian.seed.install import install_initial_values
from guardian_router import background
from guardian_router.routes import (
    DEFAULT_USER,
    RuntimeDep,
    UserDep,
    new_runtime,
    router,
    utc_now,
)

SEEDED_VAR = "GUARDIAN_DEV_SEEDED"  # set to 1 to start with three approved values


def create_app(
    chat_model: Any | None = None,
    reflector_model: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    seeded: bool | None = None,
    store: BaseStore | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    launch: background.Launcher | None = None,
) -> FastAPI:
    """Build the app on seed data. Storage is in memory unless one is passed in."""
    now = clock or utc_now
    store = store or InMemoryStore()
    datasource = FakeDataSource.from_seed(DEFAULT_USER, clock=now)
    api.configure(
        store,
        checkpointer or InMemorySaver(),
        datasource,
        clock=now,
        chat_model=chat_model,
        reflector_model=reflector_model,
    )
    if seeded if seeded is not None else os.environ.get(SEEDED_VAR) == "1":
        install_initial_values(store, datasource, DEFAULT_USER, now())

    app = FastAPI(title="Guardian router, standalone on seed data")
    app.state.guardian_datasource = datasource
    app.state.guardian_runtime = new_runtime(now, launch)
    app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/session/end")
    def end_session(runtime: RuntimeDep, user_id: UserDep) -> dict[str, str]:
        background.start_session_end_review(user_id, runtime.launch)
        return {"status": "ended"}

    return app
