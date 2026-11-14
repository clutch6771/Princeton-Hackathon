"""Tools of the chat agent. The user always comes from runtime context, never from the model."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime, tool
from langgraph.types import interrupt

from guardian.deps import ChatRunContext, Deps
from guardian.memory import proposals

REFUSED_PREFIX = "Refused: "


def describe_outcome(decision: dict[str, Any]) -> str:
    """What the agent is told after the user decided a card. It cannot change what was stored."""
    outcome = decision.get("outcome")
    value_id = decision.get("value_id")
    if outcome == proposals.OUTCOME_APPROVED:
        return f"The user approved it. It is stored as {value_id}."
    if outcome == proposals.OUTCOME_EDITED:
        return f"The user edited the text and approved it. It is stored as {value_id}."
    if outcome == proposals.OUTCOME_SUPERSEDED:
        return "The value changed after this proposal was made, so the proposal no longer applies. Nothing was stored."
    return "The user rejected the proposal. Nothing was stored. Do not propose the same change again."


def build_chat_tools(deps: Deps) -> list[Any]:
    """Create the chat agent's tools, bound to the configured store and data source."""

    @tool
    def propose_value_change(
        value_id: str, new_text: str, evidence: str, runtime: ToolRuntime[ChatRunContext]
    ) -> str:
        """Propose adding a value (value_id="new") or changing one (e.g. "V3"). The user
        sees a card and approves, edits or rejects it; nothing changes until they approve.
        `new_text` must be a full entry in the values.md format. Use an "In my words"
        line only with an exact quote of something the user wrote; it is checked and
        refused if not found. With no exact quote, use a "Suggested wording" line and
        tell the user it is your suggestion."""
        context = runtime.context
        # When the user decides the card, this body runs again from the top. The proposal
        # made by this tool call already exists then, so the checks are not repeated.
        proposal = proposals.find_by_tool_call(
            deps.store, context.user_id, context.thread_id, runtime.tool_call_id
        )
        if proposal is None:
            created = proposals.create_proposal(
                deps.store,
                deps.datasource,
                context.user_id,
                value_id=value_id,
                new_text=new_text,
                evidence=evidence,
                source=proposals.chat_source(context.thread_id, runtime.tool_call_id),
                now=deps.clock(),
            )
            if created.proposal is None:
                return f"{REFUSED_PREFIX}{created.error}"
            proposal = created.proposal
        decision = interrupt({"proposal_id": proposal.id})
        return describe_outcome(decision)

    return [propose_value_change]
