"""Entry points of the Guardian package (docs/GUARDIAN_SPEC_V5.md, "Interfaces").

The backend calls `configure` once at startup, then wraps these functions in routes.
Signatures match the spec; `configure` is the only addition.

    from guardian import api
    api.configure(store, checkpointer, datasource)
    api.chat(user_id, f"{user_id}:{date}", "hi")
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from guardian import chat_flow, config, models
from guardian.chat_agent import build_chat_agent
from guardian.datasource import DataSource
from guardian.deps import Deps
from guardian.memory import notes, reflector_files, values
from guardian.types import ChatContext, Decision, GuardianResponse

_deps: Deps | None = None
_chat_agent: Any | None = None


def configure(
    store: BaseStore,
    checkpointer: BaseCheckpointSaver,
    datasource: DataSource,
    clock: Callable[[], datetime] | None = None,
    *,
    chat_model: Any | None = None,
    reflector_model: Any | None = None,
) -> None:
    """Supply the store, checkpointer and data source. Call once before any entry point.

    `clock` defaults to the current UTC time. The models default to the ones named by
    GUARDIAN_CHAT_MODEL and GUARDIAN_REFLECTOR_MODEL; pass instances to override them.
    """
    global _deps, _chat_agent
    _deps = Deps(
        store=store,
        checkpointer=checkpointer,
        datasource=datasource,
        clock=clock or (lambda: datetime.now(UTC)),
        chat_model=chat_model,
        reflector_model=reflector_model,
    )
    _chat_agent = None


def _require_deps() -> Deps:
    if _deps is None:
        raise config.ConfigError(
            "guardian.api.configure(store, checkpointer, datasource) has not been called."
        )
    return _deps


def _agent(deps: Deps) -> Any:
    """The chat agent, built on first use."""
    global _chat_agent
    if _chat_agent is None:
        model = deps.chat_model or models.build_model(config.chat_model_name())
        _chat_agent = build_chat_agent(deps, model)
    return _chat_agent


def chat(
    user_id: str, thread_id: str, message: str, context: ChatContext | None = None
) -> GuardianResponse:
    """Send the user's message to the Guardian.

    Returns a reply, or a pending value card. While a card is pending on the thread,
    the message is not processed and the same card is returned with an error.
    """
    deps = _require_deps()
    return chat_flow.chat(deps, _agent(deps), user_id, thread_id, message, context)


def resume(user_id: str, thread_id: str, decision: Decision) -> GuardianResponse:
    """Apply the user's decision (approve, edit or reject) to the value card pending on a thread."""
    deps = _require_deps()
    return chat_flow.resume(deps, _agent(deps), user_id, thread_id, decision)


def get_values(user_id: str) -> str:
    """values.md as text, "" if none. Called by the Judge for each reel."""
    deps = _require_deps()
    if not chat_flow.is_valid_user_id(user_id):
        return ""
    return values.render_values(deps.store, user_id)


def has_values(user_id: str) -> bool:
    """Whether the user has at least one approved value. The app blocks recording until True."""
    return bool(get_values(user_id))


def get_memory(user_id: str) -> dict[str, str]:
    """Read-only view of what is stored about the user: file name to markdown."""
    deps = _require_deps()
    if not chat_flow.is_valid_user_id(user_id):
        return {"values.md": "", "brief.md": "", "hypotheses.md": "", "notes.md": ""}
    return {
        "values.md": values.render_values(deps.store, user_id),
        "brief.md": reflector_files.read_file(
            deps.store, user_id, reflector_files.BRIEF
        ),
        "hypotheses.md": reflector_files.read_file(
            deps.store, user_id, reflector_files.HYPOTHESES
        ),
        "notes.md": notes.render_notes(deps.store, user_id),
    }
