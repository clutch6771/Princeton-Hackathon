"""Tools of the Reflector (docs/GUARDIAN_SPEC_V5.md, "Reflector").

Every tool is built for one run, so it is bound to that run's user and session; no
tool takes a user ID. Code-owned records reach the agent only as read-only copies
written to /scratch/.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from langchain.tools import tool

from guardian.datasource import Feedback, Reel, parse_time
from guardian.deps import Deps, ReflectorRun
from guardian.memory import inbox, notes, proposals, records
from guardian.tools_chat import REFUSED_PREFIX, excerpt, format_profile

SESSION_FILE = "/scratch/session.jsonl"
FEEDBACK_FILE = "/scratch/feedback.jsonl"
CHATS_FILE = "/scratch/chats.md"
NOTES_FILE = "/scratch/notes.md"
MAX_VALUE_CHANGES_PER_RUN = 2
VALUE_CHANGE_FIELDS = ("value_id", "new_text", "evidence")


def session_line(reel: Reel, feedback_by_reel: dict[str, Feedback]) -> str:
    """One reel as a JSON line, with the user's feedback on it if they gave any."""
    given = feedback_by_reel.get(reel["id"])
    record = {
        "id": reel["id"],
        "description": reel["description"],
        "transcript": excerpt(reel["transcript"]),
        "on_screen_text": reel["on_screen_text"],
        "features": reel["features"],
        "verdict": reel["verdict"],
        "confidence": reel["confidence"],
        "value_ids": reel["value_ids"],
        "user_feedback": given["label"] if given else None,
    }
    return json.dumps(record, ensure_ascii=False)


def feedback_line(item: Feedback, reels_by_id: dict[str, Reel]) -> str:
    reel = reels_by_id.get(item["reel_id"] or "")
    record = {
        "id": item["id"],
        "reel_id": item["reel_id"],
        "label": item["label"],
        "comment": item["comment"],
        "source": item["source"],
        "ts": item["ts"].isoformat(),
        "reel": reel["description"] if reel else None,
        "topics": reel["features"].get("topics", []) if reel else [],
    }
    return json.dumps(record, ensure_ascii=False)


def since_or(text: str, fallback: datetime) -> datetime:
    """Parse the `since` the model passed; fall back to the run's own start point."""
    try:
        return parse_time(text)
    except (ValueError, TypeError):
        return fallback


def _dump_session_tool(deps: Deps, backend: Any, run: ReflectorRun) -> Any:
    @tool
    def dump_session(session_id: str) -> str:
        """Write every reel from the session to /scratch/session.jsonl, one per line
        (description, transcript excerpt, features, verdict, confidence). Returns the file
        path and the number of reels. Search the file with grep rather than reading it all.
        """
        if session_id != run.session_id:
            return f"{REFUSED_PREFIX}this run reviews only session {run.session_id}."
        reels = deps.datasource.get_session_reels(run.user_id, run.session_id)
        feedback = deps.datasource.get_feedback(
            run.user_id, parse_time("2000-01-01T00:00:00Z")
        )
        by_reel = {item["reel_id"]: item for item in feedback if item["reel_id"]}
        lines = [session_line(reel, by_reel) for reel in reels]
        outcome = backend.write(SESSION_FILE, "\n".join(lines) + "\n")
        if outcome.error:
            return f"Could not write {SESSION_FILE}: {outcome.error}"
        return f"Wrote {len(reels)} reels to {SESSION_FILE}. Search it with grep."

    return dump_session


def _dump_feedback_tool(deps: Deps, backend: Any, run: ReflectorRun) -> Any:
    @tool
    def dump_feedback(since: str) -> str:
        """Write the user's feedback since `since` (ISO time) to /scratch/feedback.jsonl.
        Feedback is the strongest evidence of what the user wants."""
        items = deps.datasource.get_feedback(run.user_id, since_or(since, run.since))
        reel_ids = [item["reel_id"] for item in items if item["reel_id"]]
        reels = {
            reel["id"]: reel
            for reel in deps.datasource.get_reels(run.user_id, reel_ids)
        }
        lines = [feedback_line(item, reels) for item in items]
        outcome = backend.write(FEEDBACK_FILE, "\n".join(lines) + "\n")
        if outcome.error:
            return f"Could not write {FEEDBACK_FILE}: {outcome.error}"
        return f"Wrote {len(items)} feedback items to {FEEDBACK_FILE}."

    return dump_feedback


def _dump_chatlog_tool(deps: Deps, backend: Any, run: ReflectorRun) -> Any:
    @tool
    def dump_chatlog(since: str) -> str:
        """Write the user's chat messages since `since` to /scratch/chats.md and their saved
        notes to /scratch/notes.md. These are the only places to find the user's exact words.
        """
        turns = records.chat_turns_since(
            deps.store, run.user_id, since_or(since, run.since)
        )
        said = [turn for turn in turns if turn["role"] == records.ROLE_USER]
        chats = "\n".join(f"- [{turn['ts']}] {turn['text']}" for turn in said)
        saved = notes.render_notes(deps.store, run.user_id)
        for path, text in ((CHATS_FILE, chats), (NOTES_FILE, saved)):
            outcome = backend.write(path, (text or "(none)") + "\n")
            if outcome.error:
                return f"Could not write {path}: {outcome.error}"
        count = len(notes.list_notes(deps.store, run.user_id))
        return (
            f"Wrote {len(said)} chat messages to {CHATS_FILE} "
            f"and {count} notes to {NOTES_FILE}."
        )

    return dump_chatlog


def _feed_profile_tool(deps: Deps, run: ReflectorRun) -> Any:
    @tool
    def feed_profile(session_id: str) -> str:
        """Counts for this session: topics, tones, formats, emotional pulls, conflicts."""
        if session_id != run.session_id:
            return f"{REFUSED_PREFIX}this run reviews only session {run.session_id}."
        profile = deps.datasource.feed_profile(run.user_id, run.session_id, None)
        return f"Session {run.session_id}\n{format_profile(profile)}"

    return feed_profile


def _queue_question_tool(deps: Deps, run: ReflectorRun) -> Any:
    @tool
    def queue_question(
        kind: Literal["value_change", "rerate"],
        prompt: str,
        value_change: dict | None = None,
        reel_ids: list[str] | None = None,
    ) -> str:
        """Queue a question for the user to answer next time they open the app. Limits per
        run: 2 value changes, 1 re-rate. Returns an error when a limit is reached, when an
        "In my words" quote is not something the user wrote, when cited evidence does not
        exist, or when the same change is already pending or was rejected."""
        if kind != inbox.KIND_VALUE_CHANGE:
            return f"{REFUSED_PREFIX}re-rating is not enabled in this build."
        return stage_value_change(deps, run, prompt, value_change)

    return queue_question


def stage_value_change(
    deps: Deps, run: ReflectorRun, prompt: str, value_change: dict[str, Any] | None
) -> str:
    """Check a proposed value change and hold it as a staged card for this run."""
    if not (prompt or "").strip():
        return f"{REFUSED_PREFIX}the card needs a prompt telling the user what you noticed."
    change = value_change or {}
    if not all(isinstance(change.get(field), str) for field in VALUE_CHANGE_FIELDS):
        return (
            f"{REFUSED_PREFIX}value_change must be an object with value_id, new_text "
            "and evidence."
        )
    staged = inbox.list_cards(deps.store, run.user_id, status=inbox.STATUS_STAGED)
    queued = len([card for card in staged if card.run_id == run.run_id])
    if queued >= MAX_VALUE_CHANGES_PER_RUN:
        return f"{REFUSED_PREFIX}at most {MAX_VALUE_CHANGES_PER_RUN} value changes per run."
    if inbox.count_open(deps.store, run.user_id) >= inbox.MAX_PENDING:
        return (
            f"{REFUSED_PREFIX}the user's inbox already holds {inbox.MAX_PENDING} cards, "
            "the maximum. Queue nothing more this run."
        )

    now = deps.clock()
    created = proposals.create_proposal(
        deps.store,
        deps.datasource,
        run.user_id,
        value_id=change["value_id"],
        new_text=change["new_text"],
        evidence=change["evidence"],
        source=proposals.reflector_source(run.session_id, run.run_id),
        now=now,
        status=proposals.STATUS_STAGED,
    )
    if created.proposal is None:
        return f"{REFUSED_PREFIX}{created.error}"
    card = inbox.new_card(
        run.user_id,
        inbox.KIND_VALUE_CHANGE,
        prompt.strip(),
        now,
        status=inbox.STATUS_STAGED,
        session_id=run.session_id,
        proposal_id=created.proposal.id,
        run_id=run.run_id,
    )
    inbox.add_card(deps.store, card)
    return f"Queued value-change card {queued + 1} of {MAX_VALUE_CHANGES_PER_RUN} for this run."


def build_reflector_tools(deps: Deps, backend: Any, run: ReflectorRun) -> list[Any]:
    """Create the Reflector's five tools for one run."""
    return [
        _dump_session_tool(deps, backend, run),
        _dump_feedback_tool(deps, backend, run),
        _dump_chatlog_tool(deps, backend, run),
        _feed_profile_tool(deps, run),
        _queue_question_tool(deps, run),
    ]
