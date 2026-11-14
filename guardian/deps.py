"""What the package is configured with, and the per-run context given to agents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from guardian.datasource import DataSource


@dataclass(frozen=True)
class Deps:
    """Supplied once through `guardian.api.configure`."""

    store: BaseStore
    checkpointer: BaseCheckpointSaver
    datasource: DataSource
    clock: Callable[[], datetime]
    chat_model: Any | None = None  # a chat model instance; built from the environment when None
    reflector_model: Any | None = None


@dataclass(frozen=True)
class ChatRunContext:
    """Runtime context of one chat run. Tools read the user from here, never from the model."""

    user_id: str
    thread_id: str
