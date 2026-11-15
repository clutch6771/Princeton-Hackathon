"""The adapter behind POST /agent2/chat, the chat route the iOS app calls.

The app has a chat screen and nothing else: no value-card component, no inbox and no
resume call. So this adapter turns the Guardian's structured responses into chat text:

- A value card is shown as plain text ending "Reply approve or reject." The decision
  is read in code, never by the model: exactly approve / yes or reject / no. Anything
  else shows the card again. Editing is not offered here.
- Inbox cards from the Reflector are presented at the start of a turn, one at a time,
  oldest first. A "What I learned" card is shown and dismissed; a value card waits for
  approve or reject.
- The checkpoint digest the app attaches becomes a drift-notification entry.
- The goal the user typed in the app is passed as background during onboarding.

What is shown to whom is kept in this process only. Losing it (a restart) is safe: a
card that was on screen is shown again before it can be decided.
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from guardian import api, chat_flow
from guardian.memory import formats
from guardian.types import (
    Answer,
    ChatContext,
    Decision,
    GuardianResponse,
    Interrupt,
    Question,
    ValueChange,
)

CARD_ENDING = "Reply approve or reject."
APPROVE_WORDS = frozenset({"approve", "yes"})
REJECT_WORDS = frozenset({"reject", "no"})
SUGGESTED_WORDING_NOTE = (
    "The wording is my suggestion. To put it in your own words, reply reject and "
    "tell me how you would say it."
)
INBOX_LEAD_IN = "Before I answer, something from reviewing your last session:"
# The message that triggered this card is not answered afterwards (the card ends with
# its own question), so the lead-in must not promise an answer.
LEARNED_LEAD_IN = "Here is what I learned from your last session:"
LEARNED_AFTER_ANSWER = "Also, here is what I learned from your last session:"
OFFER_ONE = (
    "I also have a flagged reel from your latest check-in. " "Want me to go through it?"
)
OFFER_MANY = (
    "I also have {count} flagged reels from your latest check-in. "
    "Want me to go through them?"
)
# A reply that accepts the offer to go through the flagged reels begins with one of these.
YES_WORDS = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "y"}
)
# A message is about the flagged reels if it names them or the notification...
_REEL_WORDS = re.compile(
    r"\b(reels?|videos?|clips?|flag(?:s|ged)?|notifications?|alerts?"
    r"|conflict(?:s|ed)?|check-?ins?)\b",
    re.IGNORECASE,
)
# ...or is a short message that points at them ("why?", "and these?"), which right
# after a notification can only be about it.
POINTING_WORDS = frozenset({"why", "what", "which", "huh", "these", "those", "them"})
SHORT_MESSAGE_WORDS = 4
INBOX_APPROVED = "Saved. It is now part of your values."
INBOX_REJECTED = "Okay, I left your values as they were."
INBOX_FAILED = "That card could not be applied."
NOTHING_TO_SAY = "I have nothing to add just now."
# The goal the app sends when the user has not typed one. It is not the user's words.
APP_DEFAULT_GOAL = "low-stress feed"
INBOX_KINDS = ("learned", "value_change")
# The app stops waiting for a chat reply after 30 seconds (`timeoutInterval` in its
# LiveAgentBackend) and asks the user to try again. A turn that took about that long
# was probably never shown.
APP_GIVES_UP_AFTER_SECONDS = 29.0
VALUE_HEADER_PREFIX = "## "
TIMEZONE_VAR = "GUARDIAN_TIMEZONE"  # an IANA name; where a chat day starts and ends
DEFAULT_TIMEZONE = "America/New_York"


@dataclass(frozen=True)
class Digest:
    """The part of the app's checkpoint digest the Guardian uses."""

    key: str
    reel_ids: tuple[str, ...]


@dataclass(frozen=True)
class Conversation:
    """What this process remembers about one user's chat."""

    awaiting_question: str | None = None  # inbox value card on screen
    held_message: str | None = None  # what the user sent when that card was shown
    answering_learned: bool = False  # a "What I learned" card was just shown
    learned_session: str | None = None
    explained_digest: str | None = None
    offered_digest: str | None = None  # the digest whose reels were offered, once
    offer_open: bool = False  # the offer was made in the Guardian's last reply
    undelivered: tuple[str, str] | None = None  # (message, reply) the app timed out on


def local_day(moment: datetime) -> str:
    """The date where the users are. A UTC date would start a new conversation, and
    strand a waiting card, in the middle of the evening in the Americas."""
    name = os.environ.get(TIMEZONE_VAR, "").strip() or DEFAULT_TIMEZONE
    try:
        zone: tzinfo = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    return moment.astimezone(zone).date().isoformat()


def first_word(text: str) -> str:
    words = text.split()
    return words[0].strip(".,!?;:").lower() if words else ""


def is_yes(text: str) -> bool:
    """Whether a reply accepts an offer: it begins with yes, sure, ok and the like."""
    return first_word(text) in YES_WORDS or text.lower().startswith("go ahead")


def about_reels(text: str) -> bool:
    """Whether a message that arrives with a digest is asking about the flagged reels."""
    if _REEL_WORDS.search(text):
        return True
    words = [word.strip(".,!?;:").lower() for word in text.split()]
    return len(words) <= SHORT_MESSAGE_WORDS and bool(POINTING_WORDS & set(words))


def offer_line(count: int) -> str:
    return OFFER_ONE if count == 1 else OFFER_MANY.format(count=count)


def parse_decision(text: str) -> str | None:
    """approve, reject, or None. Exactly one of the four words, in any case."""
    word = text.strip().lower()
    if word in APPROVE_WORDS:
        return "approve"
    return "reject" if word in REJECT_WORDS else None


def canonical_reel_id(raw: str) -> str:
    """The app sends reel UUIDs in upper case; the Judge stores them in lower case."""
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return raw


def usable_goals(stated_goals: str) -> str:
    goals = stated_goals.strip()
    return "" if goals.lower() == APP_DEFAULT_GOAL else goals


def card_text(change: ValueChange) -> str:
    """A value card as plain text for a chat bubble."""
    is_new = change["value_id"] == "new"
    heading = (
        "Proposed value:" if is_new else f"Proposed change to {change['value_id']}:"
    )
    entry = change["new_text"].strip().removeprefix(VALUE_HEADER_PREFIX)
    parts = [f"{heading}\n{entry}"]
    if "\n- suggested wording:" in entry.lower():
        parts.append(SUGGESTED_WORDING_NOTE)
    return "\n\n".join([*parts, CARD_ENDING])


def removal_text(change: ValueChange) -> str:
    """A removal card as one line: "Remove V2: <title>? Reply approve or reject." """
    stored = formats.parse_entry(change["old_text"] or "").entry
    title = f": {stored.title}" if stored else ""
    return f"Remove {change['value_id']}{title}? {CARD_ENDING}"


def pending_text(pending: Interrupt) -> str:
    """The card waiting in the chat, as text."""
    if pending["kind"] == "value_removal":
        return removal_text(pending["value_change"])
    return card_text(pending["value_change"])


def question_text(question: Question) -> str:
    """An inbox value card as plain text."""
    change = question["value_change"]
    body = card_text(change) if change else CARD_ENDING
    return f"{INBOX_LEAD_IN}\n{question['prompt']}\n\n{body}"


def response_text(response: GuardianResponse) -> str:
    """A Guardian response as one chat message: its words, any error, then any card."""
    reply, pending, error = response["reply"], response["pending"], response["error"]
    parts = []
    # The Guardian's fixed note tells the user to edit the card, which the app cannot do.
    if reply and not (pending and reply == chat_flow.SUGGESTED_WORDING_NOTE):
        parts.append(reply)
    if error:
        parts.append(error)
    if pending:
        parts.append(pending_text(pending))
    return "\n\n".join(parts) or NOTHING_TO_SAY


class ChatAdapter:
    """Turns one app chat message into one reply, for any number of users."""

    def __init__(
        self,
        clock: Callable[[], datetime],
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._timer = timer
        self._conversations: dict[str, Conversation] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # --- state ----------------------------------------------------------------------------

    def _lock(self, user_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(user_id, threading.Lock())

    def _state(self, user_id: str) -> Conversation:
        return self._conversations.get(user_id, Conversation())

    def _update(self, user_id: str, **changes: Any) -> Conversation:
        updated = replace(self._state(user_id), **changes)
        self._conversations = {**self._conversations, user_id: updated}
        return updated

    def thread_id(self, user_id: str) -> str:
        """One chat thread per user per local day."""
        return f"{user_id}:{local_day(self._clock())}"

    # --- one turn -------------------------------------------------------------------------

    def reply(
        self, user_id: str, message: str, stated_goals: str, digest: Digest | None
    ) -> str:
        """The text to show for this message. One turn at a time per user.

        If the previous turn took so long that the app gave up on it, and the user
        sends the same message again, they get the reply they missed instead of the
        message being processed twice.
        """
        text = message.strip()
        with self._lock(user_id):
            missed = self._state(user_id).undelivered
            if missed is not None and missed[0] == text:
                self._update(user_id, undelivered=None)
                return missed[1]
            started = self._timer()
            answer = self._turn(user_id, text, usable_goals(stated_goals), digest)
            too_slow = self._timer() - started >= APP_GIVES_UP_AFTER_SECONDS
            self._update(user_id, undelivered=(text, answer) if too_slow else None)
            return answer

    def _turn(self, user_id: str, text: str, goals: str, digest: Digest | None) -> str:
        thread = self.thread_id(user_id)
        decision = parse_decision(text)

        chat_card = api.pending_card(user_id, thread)
        if chat_card is not None:
            if decision is None:
                return pending_text(chat_card)
            answer = Decision(action=decision, edited_text=None)  # type: ignore[typeddict-item]
            return response_text(api.resume(user_id, thread, answer))

        shown = self._question_on_screen(user_id)
        if shown is not None:
            if decision is None:
                return question_text(shown)
            return self._decide(user_id, shown, decision, goals, digest)

        if not self._state(user_id).answering_learned:
            card = self._next_inbox_card(user_id)
            if card is not None and card["kind"] == "learned":
                return self._present_learned(user_id, card, text, goals, digest)
            if card is not None:
                # A value card waits for a decision; the message is answered after it.
                self._update(user_id, held_message=text or None)
                return self._present(user_id, card)
        return self._ask_guardian(user_id, text, goals, digest)

    # --- inbox cards ----------------------------------------------------------------------

    def _next_inbox_card(self, user_id: str) -> Question | None:
        waiting = [q for q in api.get_questions(user_id) if q["kind"] in INBOX_KINDS]
        return waiting[0] if waiting else None

    def _question_on_screen(self, user_id: str) -> Question | None:
        """The inbox value card this user was shown, if it is still waiting."""
        shown = self._state(user_id).awaiting_question
        if shown is None:
            return None
        match = [q for q in api.get_questions(user_id) if q["id"] == shown]
        if not match:  # answered somewhere else, or no longer valid
            self._update(user_id, awaiting_question=None)
            return None
        return match[0]

    def _present(self, user_id: str, card: Question) -> str:
        """Show an inbox value card and wait for approve or reject."""
        self._update(user_id, awaiting_question=card["id"])
        return question_text(card)

    def _present_learned(
        self, user_id: str, card: Question, text: str, goals: str, digest: Digest | None
    ) -> str:
        """Show a "What I learned" card and dismiss it. A message that arrived with it
        is answered in the same reply, so nothing the user says is dropped."""
        api.answer_question(user_id, card["id"], Answer(action="dismiss"))
        self._update(user_id, held_message=None)
        answer = self._ask_guardian(user_id, text, goals, digest) if text else ""
        # The user's next message is taken as their answer to the card's question.
        self._update(
            user_id, answering_learned=True, learned_session=card["session_id"]
        )
        if not answer:
            return f"{LEARNED_LEAD_IN}\n{card['prompt']}"
        if answer.endswith(CARD_ENDING):  # a value card must stay last
            return f"{LEARNED_LEAD_IN}\n{card['prompt']}\n\n{answer}"
        return f"{answer}\n\n{LEARNED_AFTER_ANSWER}\n{card['prompt']}"

    def _decide(
        self,
        user_id: str,
        card: Question,
        decision: str,
        goals: str,
        digest: Digest | None,
    ) -> str:
        """Apply approve or reject to the inbox card on screen, then move on."""
        result = api.answer_question(user_id, card["id"], Answer(action=decision))  # type: ignore[typeddict-item]
        held = self._state(user_id).held_message
        self._update(user_id, awaiting_question=None)
        if result["ok"]:
            parts = [INBOX_APPROVED if decision == "approve" else INBOX_REJECTED]
        else:
            parts = [result["error"] or INBOX_FAILED]

        following = self._next_inbox_card(user_id)
        if following is not None and following["kind"] == "learned":
            shown = self._present_learned(user_id, following, held or "", goals, digest)
            parts.append(shown)
        elif following is not None:
            parts.append(self._present(user_id, following))
        elif held:
            self._update(user_id, held_message=None)
            parts.append(self._ask_guardian(user_id, held, goals, digest))
        return "\n\n".join(parts)

    # --- the Guardian ---------------------------------------------------------------------

    def _ask_guardian(
        self, user_id: str, text: str, goals: str, digest: Digest | None
    ) -> str:
        """Send the message to the Guardian and return its reply as text.

        A new digest is explained in this reply only when the message is empty,
        is about the flagged reels, or accepts an offer to go through them.
        Otherwise the Guardian answers what the user said, and one line offering
        to go through the reels is added, once per digest.
        """
        state = self._state(user_id)
        context: ChatContext | None = None
        explaining: str | None = None
        offering: Digest | None = None
        if state.answering_learned:
            context = ChatContext(entry="learned")
            if state.learned_session:
                context["session_id"] = state.learned_session
            self._update(user_id, answering_learned=False, learned_session=None)
        elif not api.has_values(user_id):
            if goals:
                context = ChatContext(entry="onboarding", stated_goals=goals)
        elif digest and digest.reel_ids and digest.key != state.explained_digest:
            offered = state.offered_digest == digest.key
            accepted = offered and state.offer_open and is_yes(text)
            if not text or accepted or about_reels(text):
                context = ChatContext(
                    entry="drift_notification", reel_ids=list(digest.reel_ids)
                )
                explaining = digest.key
            elif not offered:
                offering = digest

        response = api.chat(user_id, self.thread_id(user_id), text, context)
        answer = response_text(response)
        succeeded = response["error"] is None
        if explaining and succeeded:
            self._update(user_id, explained_digest=explaining, offer_open=False)
        elif offering and succeeded and not response["pending"]:
            self._update(user_id, offered_digest=offering.key, offer_open=True)
            answer = f"{answer}\n\n{offer_line(len(offering.reel_ids))}"
        else:
            self._update(user_id, offer_open=False)
        return answer
