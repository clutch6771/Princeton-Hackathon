"""Store namespaces. Every record is stored under the user's ID."""

from __future__ import annotations

from typing import Any

from langgraph.store.base import BaseStore

CODE_OWNED_ZONE = "guardian"
REFLECTOR_ZONE = "reflector_files"

VALUES = "values"
META = "meta"
PROPOSALS = "proposals"
CHATLOG = "chatlog"
NOTES = "notes"
INBOX = "inbox"
REVIEWS = "reviews"
REVIEW_SNAPSHOTS = "review_snapshots"

SEARCH_LIMIT = 1000


def namespace(user_id: str, kind: str) -> tuple[str, ...]:
    """Namespace of one kind of code-owned record for one user."""
    return (user_id, CODE_OWNED_ZONE, kind)


def reflector_files_namespace(user_id: str) -> tuple[str, ...]:
    """Namespace of the files the Reflector's file tools can write."""
    return (user_id, REFLECTOR_ZONE)


def items(store: BaseStore, namespace: tuple[str, ...]) -> list[Any]:
    """Every record stored in exactly this namespace.

    A store's search matches by prefix, and a database may treat characters such as
    "_" in a user ID as wildcards. Filtering on the exact namespace keeps one user's
    records out of another user's results whatever the store does.
    """
    found = store.search(namespace, limit=SEARCH_LIMIT)
    return [item for item in found if tuple(item.namespace) == tuple(namespace)]
