"""Code-owned records other than values and proposals: the chat log.

`chat()` appends every turn here. The quote check reads the user's messages, and the
Reflector receives a read-only copy. Turns older than seven days are dropped.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from langgraph.store.base import BaseStore

from guardian.memory import keys

CHATLOG_DAYS = 7
ROLE_USER = "user"
ROLE_GUARDIAN = "guardian"


def append_chat_turn(store: BaseStore, user_id: str, role: str, text: str, now: datetime) -> None:
    """Record one chat turn under today's date and drop days outside the window."""
    namespace = keys.namespace(user_id, keys.CHATLOG)
    day = now.date().isoformat()
    existing = store.get(namespace, day)
    turns = [*(existing.value["turns"] if existing else []), {"ts": now.isoformat(), "role": role, "text": text}]
    store.put(namespace, day, {"turns": turns})
    _drop_old_days(store, namespace, now.date())


def chat_turns(store: BaseStore, user_id: str) -> list[dict[str, Any]]:
    """Every kept turn, oldest first."""
    items = store.search(keys.namespace(user_id, keys.CHATLOG), limit=keys.SEARCH_LIMIT)
    return [turn for item in sorted(items, key=lambda item: item.key) for turn in item.value["turns"]]


def user_chat_messages(store: BaseStore, user_id: str) -> list[str]:
    return [turn["text"] for turn in chat_turns(store, user_id) if turn["role"] == ROLE_USER]


def chat_turns_since(store: BaseStore, user_id: str, since: datetime) -> list[dict[str, Any]]:
    return [turn for turn in chat_turns(store, user_id) if datetime.fromisoformat(turn["ts"]) >= since]


def _drop_old_days(store: BaseStore, namespace: tuple[str, ...], today: date) -> None:
    cutoff = today - timedelta(days=CHATLOG_DAYS)
    for item in store.search(namespace, limit=keys.SEARCH_LIMIT):
        if date.fromisoformat(item.key) < cutoff:
            store.delete(namespace, item.key)


def render_notes(store: BaseStore, user_id: str) -> str:
    """notes.md as text: one line per note, in ID order; empty when there are none."""
    items = store.search(keys.namespace(user_id, keys.NOTES), limit=keys.SEARCH_LIMIT)
    ordered = sorted(items, key=lambda item: int(item.key[1:]))
    return "\n".join(f'- {item.key}: {item.value["text"]} (said: "{item.value["evidence"]}")' for item in ordered)
