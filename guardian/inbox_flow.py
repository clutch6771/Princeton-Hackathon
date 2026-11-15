"""The flows behind `guardian.api.get_questions` and `guardian.api.answer_question`.

The inbox holds what the Reflector produced: "What I learned" cards and value-change
cards. A value-change card is decided through the same single write path as a card
in chat (`proposals.decide`), so the same checks apply.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from guardian import chat_flow
from guardian.deps import Deps
from guardian.memory import inbox, proposals
from guardian.types import Answer, AnswerResult, Question

ACTION_DISMISS = "dismiss"

ERROR_NO_CARD = "This card does not exist."
ERROR_ANSWERED = "This card was already answered."
ERROR_LEARNED_ACTION = 'A "What I learned" card can only be dismissed.'
ERROR_VALUE_ACTION = "A value card can be answered with approve, edit or reject."
ERROR_RERATE = "Re-rating is not available in this build."


def refused(error: str | None) -> AnswerResult:
    return AnswerResult(ok=False, error=error)


def to_question(deps: Deps, card: inbox.Card) -> Question | None:
    """The card as the app sees it; None if its proposal is no longer waiting."""
    change = None
    if card.kind == inbox.KIND_VALUE_CHANGE:
        proposal = proposals.get_proposal(
            deps.store, card.user_id, card.proposal_id or ""
        )
        if proposal is None or proposal.status != proposals.STATUS_PENDING:
            return None
        change = proposal.to_value_change()
    return Question(
        id=card.id,
        kind=card.kind,  # type: ignore[typeddict-item]
        prompt=card.prompt,
        session_id=card.session_id,
        value_change=change,
        reel_ids=card.reel_ids,
        created=datetime.fromisoformat(card.created),
    )


def get_questions(deps: Deps, user_id: str) -> list[Question]:
    """The user's pending cards, oldest first."""
    if not chat_flow.is_valid_user_id(user_id):
        return []
    pending = inbox.list_cards(deps.store, user_id, status=inbox.STATUS_PENDING)
    questions = [to_question(deps, card) for card in pending]
    return [question for question in questions if question is not None]


def answer_question(
    deps: Deps, user_id: str, question_id: str, answer: Answer
) -> AnswerResult:
    """Apply the user's answer to one card. A refused answer changes nothing."""
    if not chat_flow.is_valid_user_id(user_id):
        return refused(ERROR_NO_CARD)
    card = inbox.get_card(deps.store, user_id, question_id)
    if card is None or card.status == inbox.STATUS_STAGED:
        return refused(ERROR_NO_CARD)
    if card.status != inbox.STATUS_PENDING:
        return refused(ERROR_ANSWERED)

    action = answer.get("action") if isinstance(answer, dict) else None
    if card.kind == inbox.KIND_LEARNED:
        if action != ACTION_DISMISS:
            return refused(ERROR_LEARNED_ACTION)
        inbox.mark_answered(deps.store, card, deps.clock())
        return AnswerResult(ok=True, error=None)
    if card.kind != inbox.KIND_VALUE_CHANGE:
        return refused(ERROR_RERATE)
    if action not in proposals.ACTIONS:
        return refused(ERROR_VALUE_ACTION)
    return decide_value_card(deps, card, action, answer)


def decide_value_card(
    deps: Deps, card: inbox.Card, action: Any, answer: Answer
) -> AnswerResult:
    """Decide a value-change card through the single write path, then close the card
    unless the answer failed a check the user can correct (an invalid edit, the cap)."""
    now = deps.clock()
    result = proposals.decide(
        deps.store,
        card.user_id,
        card.proposal_id or "",
        action=action,
        edited_text=answer.get("edited_text"),
        now=now,
    )
    proposal = proposals.get_proposal(deps.store, card.user_id, card.proposal_id or "")
    if proposal is None or proposal.status != proposals.STATUS_PENDING:
        inbox.mark_answered(deps.store, card, now)
    return AnswerResult(ok=result.ok, error=result.error)
