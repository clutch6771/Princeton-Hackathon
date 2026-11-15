"""Tools of the chat agent (docs/GUARDIAN_SPEC_V5.md, "Guardian (chat agent)").

The user always comes from runtime context, never from the model. Every result is
passed through the frame-text guard unless its tool is listed in TRUSTED_TOOLS.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Any, Literal

from langchain.tools import ToolRuntime, tool
from langgraph.types import interrupt

from guardian.datasource import FeedProfile, NewFeedback, Reel, ReelSummary
from guardian.deps import ChatRunContext, Deps
from guardian.memory import notes, proposals, records, sessions

# Words by which a user asks for a saved note to be deleted. forget_note checks the
# user's latest message for one of them, so the rule is not left to the prompt alone.
_FORGET_REQUEST = re.compile(
    r"\b(forget|delete|remove|erase|drop|scrap|wipe|clear|get rid"
    r"|don'?t (?:keep|save|remember))",
    re.IGNORECASE,
)
# The request may be a few messages back: "delete that note", "which one?", "the exam one".
FORGET_REQUEST_WINDOW = 3
REFUSED_PREFIX = "Refused: "
TRANSCRIPT_EXCERPT_CHARACTERS = 400
MAX_REELS_PER_CALL = 10
MAX_RESULTS = 10
WEEK = timedelta(days=7)
FEEDBACK_SOURCE_CHAT = "chat"

# Tools whose results are written entirely by package code. Everything else may carry
# reel text or other people's words and is labelled as untrusted data by the guard.
TRUSTED_TOOLS = frozenset(
    {"propose_value_change", "propose_value_removal", "record_feedback", "save_note"}
)


# --- Formatting --------------------------------------------------------------------


def excerpt(text: str, limit: int = TRANSCRIPT_EXCERPT_CHARACTERS) -> str:
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else f"{cleaned[:limit].rstrip()}…"


def format_reel(reel: Reel) -> str:
    cited = ", ".join(reel["value_ids"]) or "none"
    return "\n".join(
        (
            f"Reel {reel['id']} (session {reel['session_id']})",
            f"Description: {reel['description']}",
            f"Transcript (excerpt): {excerpt(reel['transcript']) or '(no speech)'}",
            f"On-screen text: {reel['on_screen_text'] or '(none)'}",
            f"Verdict: {reel['verdict']}, confidence {reel['confidence']:.2f}, values: {cited}",
            f"Reason given: {reel['reason']}",
        )
    )


def format_summary(summary: ReelSummary) -> str:
    return (
        f"{summary['id']} ({summary['verdict']}, confidence {summary['confidence']:.2f}): "
        f"{summary['one_line']}"
    )


def format_counts(pairs: list[tuple[str, int]]) -> str:
    return ", ".join(f"{name} ({count})" for name, count in pairs) or "none"


def format_profile(profile: FeedProfile) -> str:
    return "\n".join(
        (
            f"Reels: {profile['reel_count']} | Conflicts: {profile['conflict_count']}",
            f"Top topics: {format_counts(profile['top_topics'])}",
            f"Top tones: {format_counts(profile['top_tones'])}",
            f"Top formats: {format_counts(profile['top_formats'])}",
            f"Top emotional pulls: {format_counts(profile['top_emotional_pulls'])}",
        )
    )


def describe_outcome(decision: dict[str, Any]) -> str:
    """What the agent is told after the user decided a card. It cannot change what was stored."""
    outcome = decision.get("outcome")
    value_id = decision.get("value_id")
    if outcome == proposals.OUTCOME_APPROVED:
        return f"The user approved it. It is stored as {value_id}."
    if outcome == proposals.OUTCOME_EDITED:
        return f"The user edited the text and approved it. It is stored as {value_id}."
    if outcome == proposals.OUTCOME_REMOVED:
        return f"The user approved the removal. {value_id} has been deleted."
    if outcome == proposals.OUTCOME_SUPERSEDED:
        return (
            "The value changed after this proposal was made, so the proposal no longer "
            "applies. Nothing was stored."
        )
    if outcome == proposals.OUTCOME_SET_ASIDE:
        return (
            "The user did not decide the card. It has been set aside: nothing was "
            "stored, and the app has told them so. Instead of deciding, they wrote: "
            f"{json.dumps(decision.get('message', ''))}. Reply to that message now, "
            "kindly and with real content. If it asks about the card, answer the "
            "question. Propose the card again, changed as they ask, only if they "
            "want it."
        )
    return "The user rejected the proposal. Nothing was stored. Do not propose the same change again."


def clamp(count: int) -> int:
    return max(1, min(count, MAX_RESULTS))


# --- Read tools --------------------------------------------------------------------


def _get_reels_tool(deps: Deps) -> Any:
    @tool
    def get_reels(reel_ids: list[str], runtime: ToolRuntime[ChatRunContext]) -> str:
        """Look up reels the user saw: what each showed, what was said in it (transcript
        excerpt), its verdict, and the value IDs behind the verdict. Call this before
        explaining or discussing any reel. Never describe a reel without calling it."""
        wanted = list(dict.fromkeys(reel_ids))[:MAX_REELS_PER_CALL]
        found = deps.datasource.get_reels(runtime.context.user_id, wanted)
        found_ids = {reel["id"] for reel in found}
        parts = [format_reel(reel) for reel in found]
        missing = [reel_id for reel_id in wanted if reel_id not in found_ids]
        if missing:
            parts.append(f"Not found: {', '.join(missing)}")
        return "\n\n".join(parts) or "No reel IDs were given."

    return get_reels


def _find_similar_feedback_tool(deps: Deps) -> Any:
    @tool
    def find_similar_feedback(
        text: str, runtime: ToolRuntime[ChatRunContext], k: int = 3
    ) -> str:
        """Find reels similar to `text` that the user already gave feedback on, with their
        label and comment. Call this when the user disagrees with a verdict, to see whether
        they have reacted the same way before."""
        examples = deps.datasource.find_similar_feedback(
            runtime.context.user_id, text, clamp(k)
        )
        lines = [
            f"- {format_summary(example['reel'])} | user's label: {example['label']} "
            f"| user's comment: {example['comment'] or '(none)'}"
            for example in examples
        ]
        return "\n".join(lines) or "No similar feedback found."

    return find_similar_feedback


def _search_reels_tool(deps: Deps) -> Any:
    @tool
    def search_reels(
        query: str, runtime: ToolRuntime[ChatRunContext], k: int = 5
    ) -> str:
        """Search everything the user has watched by meaning, e.g. "cooking" or "people
        arguing about politics". Call this when the user asks what they have been seeing.
        """
        results = deps.datasource.search_reels(runtime.context.user_id, query, clamp(k))
        return (
            "\n".join(f"- {format_summary(r)}" for r in results) or "No matching reels."
        )

    return search_reels


def _feed_profile_tool(deps: Deps) -> Any:
    @tool
    def feed_profile(
        window: Literal["session", "week"], runtime: ToolRuntime[ChatRunContext]
    ) -> str:
        """Counts of what the feed showed in the latest session or past week: top topics,
        tones, formats, emotional pulls, and how many reels conflicted with their values.
        Call this for questions about overall patterns, not single reels."""
        user_id = runtime.context.user_id
        if window == "week":
            profile = deps.datasource.feed_profile(user_id, None, deps.clock() - WEEK)
            return f"Past week\n{format_profile(profile)}"
        sessions = deps.datasource.list_sessions(user_id)
        if not sessions:
            return "No sessions recorded yet."
        latest = max(sessions, key=lambda session: session["start"])
        profile = deps.datasource.feed_profile(user_id, latest["id"], None)
        return f"Latest session ({latest['id']})\n{format_profile(profile)}"

    return feed_profile


def _read_memory_tool(deps: Deps) -> Any:
    @tool
    def read_memory(
        name: Literal["notes", "last_session"], runtime: ToolRuntime[ChatRunContext]
    ) -> str:
        """Read the user's saved notes (things they told you about their life and
        feelings) or their last session: its time and number of reels, then its
        summary or, if it has not been reviewed yet, the Judge's records for it. Call
        this when their earlier words would help, for example before explaining a
        flag, and whenever they ask about their last session."""
        user_id = runtime.context.user_id
        if name == "notes":
            return notes.render_notes(deps.store, user_id) or "No notes saved yet."
        return sessions.last_session_report(
            deps.store, deps.datasource, user_id, deps.clock()
        )

    return read_memory


# --- Write tools (each validated in code) ---------------------------------------------


def _record_feedback_tool(deps: Deps) -> Any:
    @tool
    def record_feedback(
        reel_id: str,
        label: Literal["agree", "disagree", "not_for_me", "fine"],
        runtime: ToolRuntime[ChatRunContext],
        comment: str | None = None,
    ) -> str:
        """Save the user's reaction to one reel, with their reason if they gave one. Call
        this whenever the user agrees or disagrees with a verdict."""
        feedback = NewFeedback(
            reel_id=reel_id, label=label, source=FEEDBACK_SOURCE_CHAT
        )
        if comment and comment.strip():
            feedback["comment"] = comment.strip()
        try:
            feedback_id = deps.datasource.record_feedback(
                runtime.context.user_id, feedback
            )
        except ValueError as error:
            return f"Could not record: {error}"
        return f"Recorded as {feedback_id}."

    return record_feedback


def _save_note_tool(deps: Deps) -> Any:
    @tool
    def save_note(
        text: str, evidence: str, runtime: ToolRuntime[ChatRunContext]
    ) -> str:
        """Save something the user said about their life, habits or feelings, in their
        words, e.g. "I scroll when I'm stressed before exams". Not for values; use
        propose_value_change for those. `evidence` is the exact quote."""
        result = notes.save_note(
            deps.store,
            deps.datasource,
            runtime.context.user_id,
            text,
            evidence,
            deps.clock(),
        )
        if result.note_id is None:
            return f"{REFUSED_PREFIX}{result.error}"
        return f"Saved as {result.note_id}."

    return save_note


def _propose_value_change_tool(deps: Deps) -> Any:
    @tool
    def propose_value_change(
        value_id: str,
        new_text: str,
        evidence: str,
        runtime: ToolRuntime[ChatRunContext],
    ) -> str:
        """Propose adding a value (value_id="new") or changing one (e.g. "V3"). The user
        sees a card and approves, edits or rejects it; nothing changes until they approve.
        `new_text` must be a full entry in the values.md format. Use an "In my words"
        line only with an exact quote of something the user wrote; it is checked and
        refused if not found. With no exact quote, use a "Suggested wording" line and
        tell the user it is your suggestion."""
        context = runtime.context
        call_id = runtime.tool_call_id or _key_from_arguments(value_id, new_text)
        # When the user decides the card, this body runs again from the top. The proposal
        # made by this tool call already exists then, so the checks are not repeated.
        proposal = proposals.find_by_tool_call(
            deps.store, context.user_id, context.thread_id, call_id
        )
        if proposal is None:
            created = proposals.create_proposal(
                deps.store,
                deps.datasource,
                context.user_id,
                value_id=value_id,
                new_text=new_text,
                evidence=evidence,
                source=proposals.chat_source(context.thread_id, call_id),
                now=deps.clock(),
            )
            if created.proposal is None:
                return f"{REFUSED_PREFIX}{created.error}"
            proposal = created.proposal
        decision = interrupt({"proposal_id": proposal.id})
        return describe_outcome(decision)

    return propose_value_change


def _propose_value_removal_tool(deps: Deps) -> Any:
    @tool
    def propose_value_removal(
        value_id: str, evidence: str, runtime: ToolRuntime[ChatRunContext]
    ) -> str:
        """Propose deleting one of the user's values (e.g. "V2"). Call this only when
        the user asks to delete or drop a value. They see a card and approve or reject
        it; nothing is deleted until they approve. `evidence` is what they said. To
        change a value instead of deleting it, use propose_value_change."""
        context = runtime.context
        call_id = runtime.tool_call_id or _key_from_arguments(value_id, "removal")
        # As with propose_value_change, this body runs again when the user decides.
        proposal = proposals.find_by_tool_call(
            deps.store, context.user_id, context.thread_id, call_id
        )
        if proposal is None:
            created = proposals.create_removal(
                deps.store,
                deps.datasource,
                context.user_id,
                value_id=value_id,
                evidence=evidence,
                source=proposals.chat_source(context.thread_id, call_id),
                now=deps.clock(),
            )
            if created.proposal is None:
                return f"{REFUSED_PREFIX}{created.error}"
            proposal = created.proposal
        decision = interrupt({"proposal_id": proposal.id})
        return describe_outcome(decision)

    return propose_value_removal


def _forget_note_tool(deps: Deps) -> Any:
    @tool
    def forget_note(note_id: str, runtime: ToolRuntime[ChatRunContext]) -> str:
        """Delete one saved note (e.g. "N4"). Call this only when the user explicitly
        asks you to forget something you saved about them; find its ID with
        read_memory first. Then tell the user which note was removed."""
        user_id = runtime.context.user_id
        said = records.user_chat_messages(deps.store, user_id)
        recent = said[-FORGET_REQUEST_WINDOW:]
        if not any(_FORGET_REQUEST.search(message) for message in recent):
            return (
                f"{REFUSED_PREFIX}the user has not explicitly asked you to forget, "
                "delete or remove a note in their recent messages, so nothing was "
                "deleted. Do not tell them you refused: ask, kindly, which note they "
                "would like removed."
            )
        removed = notes.forget_note(deps.store, user_id, note_id)
        if removed is None:
            return f"{REFUSED_PREFIX}the user has no note {note_id!r}. Nothing was deleted."
        return (
            f'Removed note {removed.id} from the saved notes: "{removed.text}". '
            "Tell the user which note was removed."
        )

    return forget_note


def _key_from_arguments(value_id: str, new_text: str) -> str:
    """A stable stand-in for a tool-call ID, should a provider not supply one."""
    digest = hashlib.sha256(f"{value_id}\n{new_text}".encode()).hexdigest()
    return f"args_{digest[:16]}"


def build_chat_tools(deps: Deps) -> list[Any]:
    """Create the chat agent's ten tools, bound to the configured store and data source."""
    return [
        _get_reels_tool(deps),
        _find_similar_feedback_tool(deps),
        _search_reels_tool(deps),
        _feed_profile_tool(deps),
        _read_memory_tool(deps),
        _record_feedback_tool(deps),
        _save_note_tool(deps),
        _propose_value_change_tool(deps),
        _propose_value_removal_tool(deps),
        _forget_note_tool(deps),
    ]
