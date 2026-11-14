"""Review records: which sessions the Reflector has reviewed, and safe retry.

docs/GUARDIAN_SPEC_V5.md, "Retrying reflect_pending safely". Each session has a record
(not started, running, done or failed). Before a run, the Reflector's files are saved;
if the run fails or crashes they are put back and its staged cards are dropped, so a
retry starts from the same state and cannot produce partial or duplicate output.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta

from langgraph.store.base import BaseStore

from guardian.datasource import DataSource, Session
from guardian.memory import checks, inbox, keys, proposals, reflector_files

STATUS_NOT_STARTED = "not started"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

MAX_ATTEMPTS = 3
RUN_TIMEOUT = timedelta(minutes=10)  # a run still "running" after this has crashed
SESSION_IDLE = timedelta(minutes=10)  # no new reels for this long ends a session
MAX_LEARNED_BULLETS = 3
CURSOR_KEY = "feedback_cursor"

_SESSION_ID = re.compile(r"\bs_[A-Za-z0-9]+\b")
_SESSIONS_SEEN = re.compile(r"Sessions seen:\s*\d+")
_CITATION = re.compile(r"\[[^\]]+\]")


class ReviewError(Exception):
    """A Reflector run did not produce a valid review."""


@dataclass(frozen=True)
class Review:
    session_id: str
    status: str = STATUS_NOT_STARTED
    attempts: int = 0
    started: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class Summary:
    bullets: tuple[str, ...]  # "What I learned", one to three
    question: str | None  # the closing question for the user, if the run wrote one


# --- Records -----------------------------------------------------------------------


def get_review(store: BaseStore, user_id: str, session_id: str) -> Review:
    item = store.get(keys.namespace(user_id, keys.REVIEWS), session_id)
    return Review(**item.value) if item else Review(session_id=session_id)


def _save(store: BaseStore, user_id: str, review: Review) -> None:
    store.put(keys.namespace(user_id, keys.REVIEWS), review.session_id, asdict(review))


def reset(store: BaseStore, user_id: str, session_id: str) -> None:
    """Forget a session's review, so it is reviewed again. For tests and re-runs on demand."""
    store.delete(keys.namespace(user_id, keys.REVIEWS), session_id)


def _is_stale(review: Review, now: datetime) -> bool:
    started = datetime.fromisoformat(review.started) if review.started else None
    return started is None or now - started >= RUN_TIMEOUT


def _running(store: BaseStore, user_id: str) -> list[Review]:
    items = keys.items(store, keys.namespace(user_id, keys.REVIEWS))
    reviews = [Review(**item.value) for item in items]
    return [review for review in reviews if review.status == STATUS_RUNNING]


def run_in_progress(store: BaseStore, user_id: str, now: datetime) -> bool:
    """Whether a run for this user is under way (and has not timed out)."""
    return any(not _is_stale(review, now) for review in _running(store, user_id))


def is_finished(session: Session, now: datetime) -> bool:
    """A session ends when the broadcast stopped or it had no new reels for 10 minutes."""
    return session["stopped"] or now - session["last_reel_at"] >= SESSION_IDLE


def sessions_to_review(
    store: BaseStore, datasource: DataSource, user_id: str, now: datetime
) -> list[Session]:
    """Finished sessions that still need a review, oldest first."""
    waiting = []
    for session in datasource.list_sessions(user_id):
        status = get_review(store, user_id, session["id"]).status
        if is_finished(session, now) and status == STATUS_NOT_STARTED:
            waiting.append(session)
    return sorted(waiting, key=lambda session: session["start"])


# --- Begin, fail, succeed -------------------------------------------------------------


def _snapshot_names(session_id: str) -> list[str]:
    return [
        reflector_files.BRIEF,
        reflector_files.HYPOTHESES,
        reflector_files.session_summary_name(session_id),
    ]


def begin(
    store: BaseStore, user_id: str, session_id: str, run_id: str, now: datetime
) -> Review:
    """Mark the session as running and save the Reflector's files as they are now."""
    saved = reflector_files.snapshot(store, user_id, _snapshot_names(session_id))
    store.put(
        keys.namespace(user_id, keys.REVIEW_SNAPSHOTS), session_id, {"files": saved}
    )
    previous = get_review(store, user_id, session_id)
    review = replace(
        previous,
        status=STATUS_RUNNING,
        attempts=previous.attempts + 1,
        started=now.isoformat(),
        run_id=run_id,
    )
    _save(store, user_id, review)
    return review


def _roll_back(store: BaseStore, user_id: str, session_id: str) -> None:
    """Undo a run: restore the saved files and drop everything it staged."""
    namespace = keys.namespace(user_id, keys.REVIEW_SNAPSHOTS)
    saved = store.get(namespace, session_id)
    if saved is not None:
        reflector_files.restore(store, user_id, saved.value["files"])
        store.delete(namespace, session_id)
    inbox.discard_staged(store, user_id)
    proposals.discard_staged(store, user_id)


def fail(store: BaseStore, user_id: str, session_id: str) -> Review:
    """Roll the run back. The session stays eligible until it has failed three times."""
    _roll_back(store, user_id, session_id)
    review = get_review(store, user_id, session_id)
    status = STATUS_FAILED if review.attempts >= MAX_ATTEMPTS else STATUS_NOT_STARTED
    failed = replace(review, status=status, started=None, run_id=None)
    _save(store, user_id, failed)
    return failed


def recover_crashed(store: BaseStore, user_id: str, now: datetime) -> None:
    """Roll back any run that was left running past the timeout."""
    for review in _running(store, user_id):
        if _is_stale(review, now):
            fail(store, user_id, review.session_id)


def succeed(
    store: BaseStore, user_id: str, session_id: str, run_started: datetime
) -> None:
    """Mark the session done and move the feedback cursor to when the run began."""
    store.delete(keys.namespace(user_id, keys.REVIEW_SNAPSHOTS), session_id)
    review = get_review(store, user_id, session_id)
    _save(store, user_id, replace(review, status=STATUS_DONE))
    store.put(
        keys.namespace(user_id, keys.META),
        CURSOR_KEY,
        {"since": run_started.isoformat()},
    )


def feedback_cursor(store: BaseStore, user_id: str) -> datetime:
    """Collect feedback and chat from here. It moves forward only when a run succeeds."""
    item = store.get(keys.namespace(user_id, keys.META), CURSOR_KEY)
    return (
        datetime.fromisoformat(item.value["since"])
        if item
        else checks.BEGINNING_OF_TIME
    )


# --- What a run must produce ------------------------------------------------------------


def parse_summary(text: str, missing_ids: Callable[[str], list[str]]) -> Summary:
    """Read the "What I learned" bullets and the closing question from a session summary.

    Raises ReviewError unless there is at least one bullet and every bullet cites
    evidence that exists. The closing question is optional.
    """
    bullets: list[str] = []
    question: str | None = None
    section: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            heading = line.lower()
            if "what i learned" in heading:
                section = "learned"
            else:
                section = "question" if "question" in heading else None
        elif section == "learned" and line[:1] in ("-", "*"):
            bullets.append(line[1:].strip())
        elif section == "question" and line and question is None:
            question = line

    kept = tuple(bullets[:MAX_LEARNED_BULLETS])
    if not kept:
        raise ReviewError('the session summary has no "What I learned" bullets')
    for bullet in kept:
        if not _CITATION.search(bullet):
            raise ReviewError(f"a bullet cites no evidence in brackets: {bullet!r}")
        missing = missing_ids(bullet)
        if missing:
            raise ReviewError(f"a bullet cites evidence that does not exist: {missing}")
    # A missing closing question does not fail the run: the card is added without one.
    closing = question if question and question.endswith("?") else None
    return Summary(bullets=kept, question=closing)


def learned_prompt(summary: Summary) -> str:
    """The text of a "What I learned" card: the bullets, then the closing question if any."""
    listed = "\n".join(f"- {bullet}" for bullet in summary.bullets)
    return f"{listed}\n\n{summary.question}" if summary.question else listed


def normalize_hypotheses(text: str) -> str:
    """Set each hypothesis's "Sessions seen" to the number of distinct session IDs in its
    evidence, so a count can never be inflated by reviewing a session twice."""
    lines = []
    sessions: set[str] = set()
    for line in text.splitlines():
        if line.startswith(reflector_files.HYPOTHESIS_HEADER):
            sessions = set()
        elif line.strip().lower().startswith("- evidence:"):
            sessions = set(_SESSION_ID.findall(line))
        elif sessions and _SESSIONS_SEEN.search(line):
            line = _SESSIONS_SEEN.sub(f"Sessions seen: {len(sessions)}", line)
        lines.append(line)
    return "\n".join(lines)
