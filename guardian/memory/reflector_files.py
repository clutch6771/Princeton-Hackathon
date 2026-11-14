"""Reading the Reflector-owned files from package code.

The Reflector writes brief.md, hypotheses.md and session summaries through its file
tools; the store keeps each file as a record whose `content` is the text (confirmed in
tests/test_library_assumptions.py). Package code only reads them here.
"""

from __future__ import annotations

from langgraph.store.base import BaseStore

from guardian.datasource import DataSource
from guardian.memory import keys

BRIEF = "brief.md"
HYPOTHESES = "hypotheses.md"
SESSIONS_FOLDER = "sessions"


def read_file(store: BaseStore, user_id: str, name: str) -> str:
    """The text of one of the user's Reflector-owned files; empty if it does not exist."""
    item = store.get(keys.reflector_files_namespace(user_id), f"/{name}")
    if item is None:
        return ""
    content = item.value.get("content", "")
    return content if isinstance(content, str) else "\n".join(content)


def session_summary_name(session_id: str) -> str:
    return f"{SESSIONS_FOLDER}/{session_id}.md"


def latest_session_summary(
    store: BaseStore, datasource: DataSource, user_id: str
) -> str:
    """The summary of the user's most recent session that has one; empty if none."""
    sessions = sorted(
        datasource.list_sessions(user_id),
        key=lambda session: session["start"],
        reverse=True,
    )
    for session in sessions:
        summary = read_file(store, user_id, session_summary_name(session["id"]))
        if summary.strip():
            return summary
    return ""
