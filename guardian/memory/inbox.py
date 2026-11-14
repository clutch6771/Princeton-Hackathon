"""The inbox: cards the Reflector produces for the user to answer.

A code-owned record. Cards queued during a Reflector run are held as `staged` and are
invisible to the app; they become `pending` only when the run succeeds, and are
discarded when it fails. A value-change card points at its proposal record, which
holds the proposed text.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from langgraph.store.base import BaseStore

from guardian.memory import keys

STATUS_STAGED = "staged"
STATUS_PENDING = "pending"
STATUS_ANSWERED = "answered"

KIND_LEARNED = "learned"
KIND_VALUE_CHANGE = "value_change"
KIND_RERATE = "rerate"

MAX_PENDING = 5


@dataclass(frozen=True)
class Card:
    id: str
    user_id: str
    kind: str
    prompt: str  # text shown to the user
    status: str
    created: str
    session_id: str | None = None
    proposal_id: str | None = None  # value_change cards
    reel_ids: list[str] | None = None  # rerate cards
    run_id: str | None = None  # the Reflector run that queued it
    answered: str | None = None


def learned_card_id(session_id: str) -> str:
    """One fixed ID per session, so a repeated review cannot add a second card."""
    return f"learned_{session_id}"


def new_card(
    user_id: str,
    kind: str,
    prompt: str,
    now: datetime,
    *,
    card_id: str | None = None,
    status: str = STATUS_PENDING,
    session_id: str | None = None,
    proposal_id: str | None = None,
    run_id: str | None = None,
) -> Card:
    return Card(
        id=card_id or f"q_{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        kind=kind,
        prompt=prompt,
        status=status,
        created=now.isoformat(),
        session_id=session_id,
        proposal_id=proposal_id,
        run_id=run_id,
    )


def add_card(store: BaseStore, card: Card) -> None:
    store.put(keys.namespace(card.user_id, keys.INBOX), card.id, asdict(card))


def get_card(store: BaseStore, user_id: str, card_id: str) -> Card | None:
    """Look up a card in this user's inbox. Another user's ID is not found."""
    item = store.get(keys.namespace(user_id, keys.INBOX), card_id)
    return Card(**item.value) if item else None


def list_cards(store: BaseStore, user_id: str, status: str | None = None) -> list[Card]:
    items = keys.items(store, keys.namespace(user_id, keys.INBOX))
    cards = [Card(**item.value) for item in items]
    wanted = [card for card in cards if status is None or card.status == status]
    return sorted(wanted, key=lambda card: (card.created, card.id))


def count_open(store: BaseStore, user_id: str) -> int:
    """Cards that are pending or about to be: the number the cap applies to."""
    open_statuses = (STATUS_PENDING, STATUS_STAGED)
    return len([c for c in list_cards(store, user_id) if c.status in open_statuses])


def commit_run(store: BaseStore, user_id: str, run_id: str) -> list[Card]:
    """Make a successful run's staged cards visible."""
    committed = []
    for card in list_cards(store, user_id, status=STATUS_STAGED):
        if card.run_id == run_id:
            visible = replace(card, status=STATUS_PENDING)
            add_card(store, visible)
            committed.append(visible)
    return committed


def discard_staged(store: BaseStore, user_id: str) -> None:
    """Drop every staged card: a failed or crashed run leaves nothing behind."""
    namespace = keys.namespace(user_id, keys.INBOX)
    for card in list_cards(store, user_id, status=STATUS_STAGED):
        store.delete(namespace, card.id)


def mark_answered(store: BaseStore, card: Card, now: datetime) -> None:
    add_card(store, replace(card, status=STATUS_ANSWERED, answered=now.isoformat()))
