"""The Guardian's last message on each chat thread, while it is still unanswered.

When the app opens the chat it calls `chat` with an empty message and the Guardian
speaks first. If its last message has not been answered yet, that message is
returned again instead of a new one, without calling the model. The record is
dropped as soon as the user writes something.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from langgraph.store.base import BaseStore

from guardian.memory import keys

_KEY_PREFIX = "last_reply:"


@dataclass(frozen=True)
class LastReply:
    reply: str
    context: str | None  # what the reply was about: a notification, a card, or nothing


def _key(thread_id: str) -> str:
    return f"{_KEY_PREFIX}{thread_id}"


def get(store: BaseStore, user_id: str, thread_id: str) -> LastReply | None:
    item = store.get(keys.namespace(user_id, keys.META), _key(thread_id))
    return LastReply(**item.value) if item else None


def remember(
    store: BaseStore, user_id: str, thread_id: str, reply: str, context: str | None
) -> None:
    record = LastReply(reply=reply, context=context)
    store.put(keys.namespace(user_id, keys.META), _key(thread_id), asdict(record))


def forget(store: BaseStore, user_id: str, thread_id: str) -> None:
    store.delete(keys.namespace(user_id, keys.META), _key(thread_id))
