"""The chat and resume flows behind `guardian.api.chat` and `guardian.api.resume`."""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import Command

from guardian import config
from guardian.deps import ChatRunContext, Deps
from guardian.memory import formats, proposals, records, values
from guardian.types import ChatContext, Decision, GuardianResponse

logger = logging.getLogger("guardian")

# Shown when a card carries suggested wording and the model sent no text with it, so
# the user is always told the wording is not their own.
SUGGESTED_WORDING_NOTE = (
    "I've suggested wording for this value. Edit the card to put it in your own "
    "words, or approve it as it is."
)

USER_ID = re.compile(r"^[A-Za-z0-9_-]+$")
CONTEXT_ENTRIES = ("onboarding", "chat", "drift_notification", "reel", "learned")
ENTRY_ONBOARDING = "onboarding"

ERROR_INVALID_USER = "The user ID is missing or not valid."
ERROR_WRONG_THREAD = "This conversation does not belong to this user."
ERROR_EMPTY_MESSAGE = "The message is empty."
ERROR_BAD_CONTEXT = "The chat context is not valid."
ERROR_DECIDE_FIRST = "Please approve, edit or reject the value card first."
ERROR_NO_CARD = "No value card is waiting in this conversation."
ERROR_BAD_DECISION = "A value card can be answered with approve, edit or reject."
ERROR_UNAVAILABLE = (
    "The Guardian could not reply just now. Please try again in a moment."
)


def respond(
    reply: str | None = None,
    pending: dict[str, Any] | None = None,
    error: str | None = None,
) -> GuardianResponse:
    return GuardianResponse(reply=reply, pending=pending, error=error)


def is_valid_user_id(user_id: Any) -> bool:
    return isinstance(user_id, str) and bool(USER_ID.match(user_id))


def thread_error(user_id: Any, thread_id: Any) -> str | None:
    """A thread belongs to a user when its ID starts with that user's ID and a colon."""
    if not is_valid_user_id(user_id):
        return ERROR_INVALID_USER
    if not isinstance(thread_id, str) or not thread_id.startswith(f"{user_id}:"):
        return ERROR_WRONG_THREAD
    return None


def context_error(context: Any) -> str | None:
    if context is None:
        return None
    if (
        not isinstance(context, dict)
        or context.get("entry", "chat") not in CONTEXT_ENTRIES
    ):
        return ERROR_BAD_CONTEXT
    reel_ids = context.get("reel_ids", [])
    if not isinstance(reel_ids, list) or not all(
        isinstance(reel_id, str) for reel_id in reel_ids
    ):
        return ERROR_BAD_CONTEXT
    if not isinstance(context.get("session_id", ""), str):
        return ERROR_BAD_CONTEXT
    return None


def context_block(deps: Deps, user_id: str, context: ChatContext | None) -> str:
    """The CONTEXT block placed before the user's message. With no context and no
    approved values, the conversation is treated as onboarding."""
    if context is None:
        if values.has_values(deps.store, user_id):
            return ""
        context = ChatContext(entry=ENTRY_ONBOARDING)
    lines = ["CONTEXT", f"entry: {context.get('entry', 'chat')}"]
    if context.get("reel_ids"):
        lines.append(f"reel_ids: {', '.join(context['reel_ids'])}")
    if context.get("session_id"):
        lines.append(f"session_id: {context['session_id']}")
    return "\n".join([*lines, "END CONTEXT"])


def chat(
    deps: Deps,
    agent: Any,
    user_id: str,
    thread_id: str,
    message: str,
    context: ChatContext | None,
) -> GuardianResponse:
    error = thread_error(user_id, thread_id) or context_error(context)
    if error is None and not (isinstance(message, str) and message.strip()):
        error = ERROR_EMPTY_MESSAGE
    if error:
        return respond(error=error)

    waiting = proposals.pending_for_thread(deps.store, user_id, thread_id)
    if waiting is not None:
        return respond(pending=proposals.card(waiting), error=ERROR_DECIDE_FIRST)

    text = message.strip()
    block = context_block(deps, user_id, context)
    # Recorded before the agent runs, so the quote check can find what the user just said.
    records.append_chat_turn(deps.store, user_id, records.ROLE_USER, text, deps.clock())
    content = f"{block}\n\n{text}" if block else text
    return run(
        deps,
        agent,
        user_id,
        thread_id,
        {"messages": [{"role": "user", "content": content}]},
    )


def resume(
    deps: Deps, agent: Any, user_id: str, thread_id: str, decision: Decision
) -> GuardianResponse:
    error = thread_error(user_id, thread_id)
    if error:
        return respond(error=error)
    waiting = proposals.pending_for_thread(deps.store, user_id, thread_id)
    if waiting is None:
        return respond(error=ERROR_NO_CARD)
    if not isinstance(decision, dict):
        return respond(pending=proposals.card(waiting), error=ERROR_BAD_DECISION)

    # The decision is validated and applied in code first. The paused run is then told
    # only the outcome, so the agent cannot affect what is stored.
    result = proposals.decide(
        deps.store,
        user_id,
        waiting.id,
        action=decision.get("action"),
        edited_text=decision.get("edited_text"),
        now=deps.clock(),
    )
    stale = result.outcome == proposals.OUTCOME_SUPERSEDED
    if not result.ok and not stale:
        return respond(pending=proposals.card(waiting), error=result.error)

    outcome = {"outcome": result.outcome, "value_id": result.value_id}
    response = continue_run(deps, agent, user_id, thread_id, outcome)
    return (
        GuardianResponse(**{**response, "error": result.error}) if stale else response
    )


def continue_run(
    deps: Deps, agent: Any, user_id: str, thread_id: str, outcome: dict[str, Any]
) -> GuardianResponse:
    """Resume the paused run with the outcome; if the run was lost, confirm in plain words."""
    state = agent.get_state({"configurable": {"thread_id": thread_id}})
    if not state.interrupts:
        return respond(reply=plain_confirmation(outcome))
    return run(deps, agent, user_id, thread_id, Command(resume=outcome))


def plain_confirmation(outcome: dict[str, Any]) -> str:
    if outcome["outcome"] in (proposals.OUTCOME_APPROVED, proposals.OUTCOME_EDITED):
        return f"Saved as {outcome['value_id']}."
    if outcome["outcome"] == proposals.OUTCOME_SUPERSEDED:
        return "That value has changed since the card was made, so nothing was saved."
    return "Okay, nothing was changed."


def run(
    deps: Deps, agent: Any, user_id: str, thread_id: str, graph_input: Any
) -> GuardianResponse:
    """Run or resume the agent and turn the result into a response. Never raises for a model failure."""
    try:
        result = agent.invoke(
            graph_input,
            config={"configurable": {"thread_id": thread_id}},
            context=ChatRunContext(user_id=user_id, thread_id=thread_id),
        )
    except (
        Exception
    ) as error:  # noqa: BLE001 - provider, retry and call-limit failures all end here
        detail = config.redact_secrets(f"{type(error).__name__}: {error}")
        logger.error("chat run failed for thread %s: %s", thread_id, detail)
        return respond(error=ERROR_UNAVAILABLE)

    reply = last_reply_text(result)
    waiting = (
        proposals.pending_for_thread(deps.store, user_id, thread_id)
        if result.get("__interrupt__")
        else None
    )
    if waiting is not None and not reply and has_suggested_wording(waiting):
        reply = SUGGESTED_WORDING_NOTE
    if reply:
        records.append_chat_turn(
            deps.store, user_id, records.ROLE_GUARDIAN, reply, deps.clock()
        )
    return respond(reply=reply, pending=proposals.card(waiting) if waiting else None)


def has_suggested_wording(proposal: proposals.Proposal) -> bool:
    entry = formats.parse_entry(proposal.proposed_text).entry
    return entry is not None and entry.wording_label == formats.WORDING_SUGGESTED


def last_reply_text(result: dict[str, Any]) -> str | None:
    """The text of the agent's last message, or None when it wrote none."""
    messages = result.get("messages", [])
    if not messages or not isinstance(messages[-1], AIMessage):
        return None
    content = messages[-1].content
    if isinstance(content, str):
        text = content
    else:
        text = "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return text.strip() or None
