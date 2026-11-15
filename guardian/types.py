"""Public interface types, as defined in docs/GUARDIAN_SPEC_V5.md ("Interfaces").

The app and backend teams build against these shapes. Do not change a field
without changing the spec.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict


class ChatContext(TypedDict, total=False):
    entry: Literal["onboarding", "chat", "drift_notification", "reel", "learned"]
    reel_ids: list[
        str
    ]  # drift_notification: the conflicting reels; reel: one reel from the Timeline
    session_id: str  # learned: open the chat on a session's "What I learned"
    # Optional addition for the app's chat adapter: the goal the user typed when
    # they set up the app, passed as background during onboarding.
    stated_goals: str


class ValueChange(TypedDict):
    value_id: str  # "V3", or "new"
    old_text: str | None
    new_text: str  # full values.md entry; may carry a "Suggested wording" line
    evidence: str  # quotes, reel IDs or feedback IDs


class Interrupt(TypedDict):
    # "value_removal" (added after V5): the card asks to delete `value_change.value_id`;
    # `old_text` is the value as stored and `new_text` is empty. Approve or reject only.
    kind: Literal["value_change", "value_removal"]
    value_change: ValueChange


class GuardianResponse(TypedDict):
    reply: str | None
    pending: Interrupt | None  # set when the run is paused for approval
    error: (
        str | None
    )  # for display, e.g. an edited value that does not match the format


class AnswerResult(TypedDict):
    ok: bool
    error: str | None


class Decision(TypedDict):
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None


class Question(TypedDict):
    id: str
    kind: Literal["learned", "value_change", "rerate"]
    prompt: str
    session_id: str | None
    value_change: ValueChange | None
    reel_ids: list[str] | None
    created: datetime


class Answer(TypedDict, total=False):
    action: Literal["approve", "edit", "reject", "rate", "skip", "dismiss"]
    edited_text: str
    ratings: dict[str, Literal["fine", "not_for_me"]]
