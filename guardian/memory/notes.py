"""Notes: things the user said about their life and feelings.

A code-owned record. The chat agent's `save_note` tool is the only writer, and every
note is validated here: it must cite an exact quote of something the user wrote, so a
note is a fact the user stated, never an interpretation of their behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from langgraph.store.base import BaseStore

from guardian.datasource import DataSource
from guardian.memory import checks, formats, keys, locks

MAX_NOTES = 40
MAX_NOTE_CHARACTERS = 300
COUNTER_KEY = "note_counter"
NOTE_ID_PREFIX = "N"


@dataclass(frozen=True)
class Note:
    id: str
    text: str
    evidence: str
    created: str


@dataclass(frozen=True)
class NoteResult:
    note_id: str | None
    error: str | None


def _number(note_id: str) -> int:
    return int(note_id[len(NOTE_ID_PREFIX) :])


def list_notes(store: BaseStore, user_id: str) -> list[Note]:
    """The user's notes, in ID order."""
    items = keys.items(store, keys.namespace(user_id, keys.NOTES))
    found = [
        Note(
            id=item.key,
            text=item.value["text"],
            evidence=item.value["evidence"],
            created=item.value["created"],
        )
        for item in items
    ]
    return sorted(found, key=lambda note: _number(note.id))


def render_notes(store: BaseStore, user_id: str) -> str:
    """notes.md as text: one line per note; empty when there are none."""
    return "\n".join(
        f'- {note.id}: {note.text} (said: "{note.evidence}")'
        for note in list_notes(store, user_id)
    )


def _save_note(
    store: BaseStore,
    datasource: DataSource,
    user_id: str,
    text: str,
    evidence: str,
    now: datetime,
) -> NoteResult:
    """Validate and store one note. Returns its ID, or an error written for the agent."""
    cleaned = " ".join((text or "").split())
    quote = (evidence or "").strip().strip(formats.QUOTE_CHARACTERS).strip()
    if not cleaned or not quote:
        return NoteResult(
            None, "A note needs both the fact and the user's exact quote."
        )
    if len(cleaned) > MAX_NOTE_CHARACTERS:
        return NoteResult(None, f"A note is at most {MAX_NOTE_CHARACTERS} characters.")
    if not checks.quote_found(
        quote, checks.user_statements(store, datasource, user_id)
    ):
        return NoteResult(
            None,
            "The evidence must be an exact quote of something the user wrote. "
            "Nothing was saved.",
        )

    existing = list_notes(store, user_id)
    repeated = next((n for n in existing if formats.same_text(n.text, cleaned)), None)
    if repeated is not None:
        return NoteResult(repeated.id, None)
    if len(existing) >= MAX_NOTES:
        return NoteResult(
            None,
            f"The user already has {MAX_NOTES} notes, the maximum. Nothing was saved.",
        )

    note_id = _allocate_note_id(store, user_id, existing)
    store.put(
        keys.namespace(user_id, keys.NOTES),
        note_id,
        {"text": cleaned, "evidence": quote, "created": now.isoformat()},
    )
    return NoteResult(note_id, None)


def save_note(
    store: BaseStore,
    datasource: DataSource,
    user_id: str,
    text: str,
    evidence: str,
    now: datetime,
) -> NoteResult:
    """Validate and store one note, one at a time per user, so note IDs never collide."""
    with locks.hold(user_id, locks.NOTES):
        return _save_note(store, datasource, user_id, text, evidence, now)


def _allocate_note_id(store: BaseStore, user_id: str, existing: list[Note]) -> str:
    """Take the next note ID. The counter only increases, so an ID is never reused."""
    namespace = keys.namespace(user_id, keys.META)
    counter = store.get(namespace, COUNTER_KEY)
    highest = max((_number(note.id) for note in existing), default=0)
    last = max(counter.value["last"] if counter else 0, highest)
    store.put(namespace, COUNTER_KEY, {"last": last + 1})
    return f"{NOTE_ID_PREFIX}{last + 1}"
