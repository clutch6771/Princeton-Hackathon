"""Proposal records and the single write path for approved values.

Agents propose; they never write. `create_proposal` runs every check and stores a
pending record. `decide` is the only code that changes approved values, and it takes
the proposal's ID and the user's decision, never text supplied by a model.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from langgraph.store.base import BaseStore

from guardian.datasource import DataSource
from guardian.memory import checks, formats, keys, values
from guardian.types import ValueChange

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SUPERSEDED = "superseded"

OUTCOME_APPROVED = "approved"
OUTCOME_EDITED = "edited"
OUTCOME_REJECTED = "rejected"
OUTCOME_SUPERSEDED = "superseded"

ACTION_APPROVE = "approve"
ACTION_EDIT = "edit"
ACTION_REJECT = "reject"
ACTIONS = (ACTION_APPROVE, ACTION_EDIT, ACTION_REJECT)

TARGET_NEW = "new"
SOURCE_CHAT = "chat"
SOURCE_REFLECTOR = "reflector"

_DECIDE_LOCK = threading.Lock()  # one decision at a time within this process


@dataclass(frozen=True)
class Proposal:
    id: str
    user_id: str
    target: str  # a value ID such as "V3", or "new"
    proposed_text: str  # canonical entry; the header reads "## New:" for a new value
    evidence: str
    old_text: str | None  # the target value's text when the proposal was made
    fingerprint: (
        str  # of old_text; a mismatch at decision time means the proposal is stale
    )
    status: str
    source: dict[str, str]  # where it came from: a chat thread or a Reflector run
    created: str
    decided: str | None = None
    result_value_id: str | None = None

    def to_value_change(self) -> ValueChange:
        """The card shown to the user."""
        return ValueChange(
            value_id=self.target,
            old_text=self.old_text,
            new_text=self.proposed_text,
            evidence=self.evidence,
        )


@dataclass(frozen=True)
class CreateResult:
    proposal: Proposal | None
    error: str | None


@dataclass(frozen=True)
class DecisionResult:
    ok: bool
    outcome: str | None  # approved, edited, rejected or superseded
    value_id: str | None
    error: str | None


def chat_source(thread_id: str, tool_call_id: str) -> dict[str, str]:
    return {"kind": SOURCE_CHAT, "thread_id": thread_id, "tool_call_id": tool_call_id}


def reflector_source(session_id: str, run_id: str) -> dict[str, str]:
    return {"kind": SOURCE_REFLECTOR, "session_id": session_id, "run_id": run_id}


# --- Reading -------------------------------------------------------------------------


def get_proposal(store: BaseStore, user_id: str, proposal_id: str) -> Proposal | None:
    """Look up a proposal in this user's records. Another user's ID is not found."""
    item = store.get(keys.namespace(user_id, keys.PROPOSALS), proposal_id)
    return Proposal(**item.value) if item else None


def list_proposals(store: BaseStore, user_id: str) -> list[Proposal]:
    items = store.search(
        keys.namespace(user_id, keys.PROPOSALS), limit=keys.SEARCH_LIMIT
    )
    return sorted(
        (Proposal(**item.value) for item in items),
        key=lambda proposal: (proposal.created, proposal.id),
    )


def find_by_tool_call(
    store: BaseStore, user_id: str, thread_id: str, tool_call_id: str
) -> Proposal | None:
    """The proposal a chat tool call already created, if any (used when the tool re-runs)."""
    wanted = chat_source(thread_id, tool_call_id)
    return next((p for p in list_proposals(store, user_id) if p.source == wanted), None)


def pending_for_thread(
    store: BaseStore, user_id: str, thread_id: str
) -> Proposal | None:
    """The value card waiting on a chat thread, if any."""
    return next(
        (
            proposal
            for proposal in list_proposals(store, user_id)
            if proposal.status == STATUS_PENDING
            and proposal.source.get("kind") == SOURCE_CHAT
            and proposal.source.get("thread_id") == thread_id
        ),
        None,
    )


def _save(store: BaseStore, proposal: Proposal) -> None:
    store.put(
        keys.namespace(proposal.user_id, keys.PROPOSALS), proposal.id, asdict(proposal)
    )


# --- Creating ------------------------------------------------------------------------


def create_proposal(
    store: BaseStore,
    datasource: DataSource,
    user_id: str,
    *,
    value_id: str,
    new_text: str,
    evidence: str,
    source: dict[str, str],
    now: datetime,
) -> CreateResult:
    """Check a proposed value change and record it as pending.

    Returns the proposal, or an error written for the agent that proposed it.
    """
    target = _normalize_target(value_id)
    if target is None:
        return CreateResult(
            None, 'value_id must be "new" or the ID of a stored value such as V3.'
        )
    parsed = formats.parse_entry(new_text)
    if parsed.entry is None:
        return CreateResult(None, parsed.error)
    entry = parsed.entry
    text = formats.render_entry(entry)
    current = values.get_value(store, user_id, target) if target != TARGET_NEW else None

    error = (
        _target_error(store, user_id, target, entry, current)
        or _wording_error(store, datasource, user_id, entry, current)
        or _evidence_error(datasource, user_id, entry, evidence)
        or _duplicate_error(store, user_id, target, text, source)
    )
    if error:
        return CreateResult(None, error)

    old_text = current.text if current else None
    proposal = Proposal(
        id=f"p_{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        target=target,
        proposed_text=text,
        evidence=evidence.strip(),
        old_text=old_text,
        fingerprint=values.fingerprint(old_text),
        status=STATUS_PENDING,
        source=dict(source),
        created=now.isoformat(),
    )
    _save(store, proposal)
    return CreateResult(proposal, None)


def _normalize_target(value_id: str) -> str | None:
    cleaned = (value_id or "").strip()
    if cleaned.lower() == TARGET_NEW:
        return TARGET_NEW
    return cleaned if formats.is_value_id(cleaned) else None


def _target_error(
    store: BaseStore,
    user_id: str,
    target: str,
    entry: formats.ValueEntry,
    current: values.StoredValue | None,
) -> str | None:
    if target == TARGET_NEW:
        if entry.header_id != formats.NEW_HEADER_ID:
            return 'For a new value, write the header as "## New: <title> (strength: ...)".'
        if len(values.list_values(store, user_id)) >= values.MAX_VALUES:
            return f"The user already has {values.MAX_VALUES} values, the maximum. Refine an existing value instead."
        return None
    if current is None:
        return f"{target} is not one of the user's stored values."
    if entry.header_id != target:
        return f'The header must name the value being changed: "## {target}: ...".'
    return None


def _wording_error(
    store: BaseStore,
    datasource: DataSource,
    user_id: str,
    entry: formats.ValueEntry,
    current: values.StoredValue | None,
) -> str | None:
    if (
        _carries_over_stored_wording(entry, current)
        or entry.wording_label == formats.WORDING_SUGGESTED
    ):
        return None
    if entry.wording_label == formats.WORDING_AGREED:
        return (
            '"Agreed wording" is set by the app when the user approves a suggestion. '
            'Use a "Suggested wording" line and tell the user it is your suggestion.'
        )
    if checks.quote_found(
        entry.wording_text, checks.user_statements(store, datasource, user_id)
    ):
        return None
    return (
        'The "In my words" quote was not found in anything the user wrote. '
        'Use a "Suggested wording" line instead and tell the user it is your suggestion, which they can edit.'
    )


def _carries_over_stored_wording(
    entry: formats.ValueEntry, current: values.StoredValue | None
) -> bool:
    """A change may keep the stored value's wording line, but only if it is identical.

    Any difference, even in case or spacing, makes it new wording, which must pass
    the quote check or be offered as a suggestion.
    """
    if current is None:
        return False
    stored = formats.parse_entry(current.text).entry
    return (
        stored is not None
        and stored.wording_label == entry.wording_label
        and stored.wording_text == entry.wording_text
    )


def _evidence_error(
    datasource: DataSource, user_id: str, entry: formats.ValueEntry, evidence: str
) -> str | None:
    if not (evidence or "").strip():
        return "Evidence is required: cite the user's words, reel IDs or feedback IDs."
    missing = checks.missing_evidence_ids(
        datasource, user_id, f"{evidence} {entry.evidence}"
    )
    if missing:
        return f"These IDs do not exist for this user: {', '.join(missing)}. Cite only real evidence."
    return None


def _duplicate_error(
    store: BaseStore, user_id: str, target: str, text: str, source: dict[str, str]
) -> str | None:
    for existing in list_proposals(store, user_id):
        if existing.target != target or not formats.same_text(
            existing.proposed_text, text
        ):
            continue
        if existing.status == STATUS_PENDING:
            return "The same change is already waiting for the user's decision."
        if existing.status == STATUS_REJECTED:
            return "The user already rejected this change. Do not propose it again in the same form."
    if source.get("kind") == SOURCE_CHAT and pending_for_thread(
        store, user_id, source.get("thread_id", "")
    ):
        return "A value card is already waiting in this conversation. Ask the user to decide on it first."
    return None


# --- Deciding: the single write path ---------------------------------------------------


def decide(
    store: BaseStore,
    user_id: str,
    proposal_id: str,
    *,
    action: str,
    edited_text: str | None,
    now: datetime,
) -> DecisionResult:
    """Apply the user's decision to a pending proposal.

    This is the only code that changes approved values. A failed check changes
    nothing and leaves the card pending, except a stale card, which is superseded.
    """
    with _DECIDE_LOCK:
        proposal = get_proposal(store, user_id, proposal_id)
        if proposal is None:
            return _failed("This card does not exist.")
        if action not in ACTIONS:
            return _failed(f"A value card can be answered with {', '.join(ACTIONS)}.")
        if proposal.status != STATUS_PENDING:
            return _failed("This card was already answered.")
        if action == ACTION_REJECT:
            _save(
                store,
                replace(proposal, status=STATUS_REJECTED, decided=now.isoformat()),
            )
            return DecisionResult(
                ok=True, outcome=OUTCOME_REJECTED, value_id=None, error=None
            )
        return _apply(store, proposal, action, edited_text, now)


def _apply(
    store: BaseStore,
    proposal: Proposal,
    action: str,
    edited_text: str | None,
    now: datetime,
) -> DecisionResult:
    user_id = proposal.user_id
    is_new = proposal.target == TARGET_NEW
    current = None if is_new else values.get_value(store, user_id, proposal.target)
    if values.fingerprint(current.text if current else None) != proposal.fingerprint:
        _save(
            store, replace(proposal, status=STATUS_SUPERSEDED, decided=now.isoformat())
        )
        return DecisionResult(
            ok=False,
            outcome=OUTCOME_SUPERSEDED,
            value_id=None,
            error="This value has changed since the card was made, so the card no longer applies.",
        )

    proposed = formats.parse_entry(proposal.proposed_text).entry
    final, error = _final_entry(proposed, action, edited_text)
    if final is None:
        return _failed(error)
    if is_new and len(values.list_values(store, user_id)) >= values.MAX_VALUES:
        return _failed(f"You already have {values.MAX_VALUES} values, the maximum.")

    value_id = values.allocate_value_id(store, user_id) if is_new else proposal.target
    stored = replace(
        final,
        header_id=value_id,
        wording_label=_stored_wording_label(proposed, final),
        evidence=formats.stamp_approved(final.evidence, now.date()),
    )
    values._store_value(store, user_id, value_id, formats.render_entry(stored), now)
    _save(
        store,
        replace(
            proposal,
            status=STATUS_APPROVED,
            decided=now.isoformat(),
            result_value_id=value_id,
        ),
    )
    outcome = OUTCOME_EDITED if action == ACTION_EDIT else OUTCOME_APPROVED
    return DecisionResult(ok=True, outcome=outcome, value_id=value_id, error=None)


def _final_entry(
    proposed: formats.ValueEntry, action: str, edited_text: str | None
) -> tuple[formats.ValueEntry | None, str | None]:
    """The entry to store: the proposal as it is, or the user's valid edit of it."""
    if action == ACTION_APPROVE:
        return proposed, None
    if not (edited_text or "").strip():
        return None, "An edit needs the edited text of the value."
    parsed = formats.parse_entry(edited_text)
    if parsed.entry is None:
        return None, parsed.error
    if parsed.entry.header_id != proposed.header_id:
        return (
            None,
            f'An edit cannot change which value the card is about. Keep the header "## {proposed.header_id}: ...".',
        )
    return parsed.entry, None


def _stored_wording_label(
    proposed: formats.ValueEntry, final: formats.ValueEntry
) -> str:
    """Code decides the stored label, whatever label the user left in place."""
    if not formats.same_text(final.wording_text, proposed.wording_text):
        return formats.WORDING_OWN  # the user typed it
    if proposed.wording_label == formats.WORDING_SUGGESTED:
        return formats.WORDING_AGREED
    return proposed.wording_label


def _failed(error: str | None) -> DecisionResult:
    return DecisionResult(ok=False, outcome=None, value_id=None, error=error)


def card(proposal: Proposal) -> dict[str, Any]:
    """The `Interrupt` payload for a pending chat proposal."""
    return {"kind": "value_change", "value_change": proposal.to_value_change()}
