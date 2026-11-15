"""The Reflector-owned files: brief.md, hypotheses.md and session summaries.

These are the only persistent files the Reflector's file tools can write. They live
in their own part of the store, one namespace per user, where each file is a record
whose `content` is the text (confirmed in tests/test_library_assumptions.py). Approved
values and the other code-owned records are stored elsewhere and are never mounted
in the Reflector's file system.

`GuardedFilesBackend` enforces the caps in code: an over-cap write is refused with a
message telling the agent to consolidate first.
"""

from __future__ import annotations

from typing import Any

from deepagents.backends import StoreBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    WriteResult,
)
from deepagents.backends.utils import perform_string_replacement
from langgraph.store.base import BaseStore

from guardian.datasource import DataSource
from guardian.memory import keys

BRIEF = "brief.md"
HYPOTHESES = "hypotheses.md"
SESSIONS_FOLDER = "sessions"

BRIEF_WORD_CAP = 150
SESSION_SUMMARY_WORD_CAP = 200
MAX_HYPOTHESES = 10
HYPOTHESIS_HEADER = "## "


def session_summary_name(session_id: str) -> str:
    return f"{SESSIONS_FOLDER}/{session_id}.md"


# --- Reading from package code ------------------------------------------------------


def read_file(store: BaseStore, user_id: str, name: str) -> str:
    """The text of one of the user's Reflector-owned files; empty if it does not exist."""
    item = store.get(keys.reflector_files_namespace(user_id), f"/{name}")
    if item is None:
        return ""
    content = item.value.get("content", "")
    return content if isinstance(content, str) else "\n".join(content)


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


# --- Snapshots, so a failed run can be undone ------------------------------------------


def snapshot(store: BaseStore, user_id: str, names: list[str]) -> dict[str, Any]:
    """The stored records of the named files as they are now (None where absent)."""
    namespace = keys.reflector_files_namespace(user_id)
    saved = {}
    for name in names:
        item = store.get(namespace, f"/{name}")
        saved[name] = dict(item.value) if item else None
    return saved


def restore(store: BaseStore, user_id: str, saved: dict[str, Any]) -> None:
    """Put the files back exactly as they were when the snapshot was taken."""
    namespace = keys.reflector_files_namespace(user_id)
    for name, value in saved.items():
        if value is None:
            store.delete(namespace, f"/{name}")
        else:
            store.put(namespace, f"/{name}", value)


def write_text(store: BaseStore, user_id: str, name: str, text: str) -> None:
    """Write a file from package code (used to store a normalized hypotheses.md)."""
    files_backend(store, user_id).write(f"/{name}", text)


# --- The backend mounted at /memories/ in the Reflector's file system ---------------------


def files_backend(store: BaseStore, user_id: str) -> StoreBackend:
    """The user's files, with no checks. For package code and tests."""
    namespace = keys.reflector_files_namespace(user_id)
    return StoreBackend(namespace=lambda _runtime: namespace, store=store)


def word_count(text: str) -> int:
    return len(text.split())


def write_error(file_path: str, content: str) -> str | None:
    """Why this content may not be written to this path, or None if it may."""
    name = file_path.lstrip("/")
    if name == BRIEF:
        words = word_count(content)
        if words > BRIEF_WORD_CAP:
            return (
                f"brief.md is limited to {BRIEF_WORD_CAP} words; this has {words}. "
                "Consolidate first: keep what still holds and drop the rest."
            )
        return None
    if name == HYPOTHESES:
        items = len(
            [
                line
                for line in content.splitlines()
                if line.startswith(HYPOTHESIS_HEADER)
            ]
        )
        if items > MAX_HYPOTHESES:
            return (
                f"hypotheses.md is limited to {MAX_HYPOTHESES} hypotheses; this has {items}. "
                "Consolidate first: merge or drop the weakest."
            )
        return None
    if name.startswith(f"{SESSIONS_FOLDER}/") and name.endswith(".md"):
        words = word_count(content)
        if words > SESSION_SUMMARY_WORD_CAP:
            return (
                f"A session summary is limited to {SESSION_SUMMARY_WORD_CAP} words; "
                f"this has {words}. Consolidate first: shorten it."
            )
        return None
    return (
        "Only brief.md, hypotheses.md and sessions/<session_id>.md can be written "
        "under /memories/."
    )


class GuardedFilesBackend(BackendProtocol):
    """The Reflector's view of its own files: only its three kinds, within their caps."""

    def __init__(self, inner: BackendProtocol) -> None:
        self._inner = inner

    def write(self, file_path: str, content: str) -> WriteResult:
        error = write_error(file_path, content)
        if error:
            return WriteResult(error=error)
        return self._inner.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        current = self._inner.read(file_path)
        if current.error is None and current.file_data is not None:
            outcome = perform_string_replacement(
                current.file_data["content"], old_string, new_string, replace_all
            )
            if not isinstance(outcome, str):
                error = write_error(file_path, outcome[0])
                if error:
                    return EditResult(error=error)
        return self._inner.edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error="Files under /memories/ cannot be deleted.")

    def ls(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.ls(*args, **kwargs)

    def read(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.read(*args, **kwargs)

    def glob(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.glob(*args, **kwargs)

    def grep(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.grep(*args, **kwargs)

    def download_files(self, paths: list[str]) -> Any:
        # Used by the library to preload brief.md and hypotheses.md into the prompt.
        return self._inner.download_files(paths)

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        return self._inner.upload_files(files)


def guarded_backend(store: BaseStore, user_id: str) -> GuardedFilesBackend:
    """The backend to mount at /memories/ for one user's Reflector run."""
    return GuardedFilesBackend(files_backend(store, user_id))
