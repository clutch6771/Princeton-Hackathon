"""The protected store of approved values.

Reading is open to package code. Writing happens only through `_store_value`, which
only `proposals.decide` calls (a test enforces this). No agent tool reaches this part
of the store.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from langgraph.store.base import BaseStore

from guardian.memory import formats, keys

MAX_VALUES = 12
COUNTER_KEY = "value_counter"


@dataclass(frozen=True)
class StoredValue:
    id: str
    text: str  # the full entry, in the values.md format
    updated: str


def values_namespace(user_id: str) -> tuple[str, ...]:
    return keys.namespace(user_id, keys.VALUES)


def list_values(store: BaseStore, user_id: str) -> list[StoredValue]:
    """The user's approved values, in ID order."""
    items = keys.items(store, values_namespace(user_id))
    stored = [
        StoredValue(id=item.key, text=item.value["text"], updated=item.value["updated"])
        for item in items
    ]
    return sorted(stored, key=lambda value: formats.value_number(value.id))


def get_value(store: BaseStore, user_id: str, value_id: str) -> StoredValue | None:
    item = store.get(values_namespace(user_id), value_id)
    if item is None:
        return None
    return StoredValue(
        id=item.key, text=item.value["text"], updated=item.value["updated"]
    )


def render_values(store: BaseStore, user_id: str) -> str:
    """values.md as text; empty when the user has no approved values."""
    return "\n\n".join(value.text for value in list_values(store, user_id))


def has_values(store: BaseStore, user_id: str) -> bool:
    return bool(list_values(store, user_id))


def fingerprint(text: str | None) -> str:
    """Identifies a value's text at the time a proposal was made."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def allocate_value_id(store: BaseStore, user_id: str) -> str:
    """Take the next ID from the user's counter. The counter only increases."""
    namespace = keys.namespace(user_id, keys.META)
    counter = store.get(namespace, COUNTER_KEY)
    highest_stored = max(
        (formats.value_number(value.id) for value in list_values(store, user_id)),
        default=0,
    )
    last = max(counter.value["last"] if counter else 0, highest_stored)
    store.put(namespace, COUNTER_KEY, {"last": last + 1})
    return f"V{last + 1}"


def _store_value(
    store: BaseStore, user_id: str, value_id: str, text: str, now: datetime
) -> None:
    """Write one approved value. Call only from `proposals.decide`."""
    store.put(
        values_namespace(user_id), value_id, {"text": text, "updated": now.isoformat()}
    )
