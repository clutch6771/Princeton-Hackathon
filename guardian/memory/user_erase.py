"""Erase everything the package holds about one user, through the store and the
checkpointer's own interfaces. Used when storage is in memory; on Postgres the
account-reset route uses `guardian_router.reset_demo`, which also clears the Judge's rows.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

SEARCH_LIMIT = 10_000


def erase_user(
    store: BaseStore, checkpointer: BaseCheckpointSaver, user_id: str
) -> dict[str, int]:
    """Delete this user's records and chat threads. Returns how many of each went."""
    # A search by namespace prefix may be a LIKE pattern underneath, where "_" is a
    # wildcard, so every item is checked to belong to exactly this user.
    items = [
        item
        for item in store.search((user_id,), limit=SEARCH_LIMIT)
        if item.namespace[0] == user_id
    ]
    for item in items:
        store.delete(item.namespace, item.key)
    threads = sorted(
        {
            str(saved.config["configurable"]["thread_id"])
            for saved in checkpointer.list(None)
            if str(saved.config["configurable"]["thread_id"]).startswith(f"{user_id}:")
        }
    )
    for thread_id in threads:
        checkpointer.delete_thread(thread_id)
    return {"store": len(items), "threads": len(threads)}
