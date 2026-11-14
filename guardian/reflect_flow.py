"""The flow behind `guardian.api.reflect_pending`.

Reviews every finished session that has not been reviewed, one at a time. Each run is
all or nothing: on success its staged cards become visible and the "What I learned"
card is added; on any failure the Reflector's files are restored and nothing it
staged is kept.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from guardian import chat_flow, config
from guardian.deps import Deps, ReflectorRun
from guardian.memory import checks, inbox, proposals, reflector_files, reviews, values
from guardian.reflector import build_reflector, run_input

logger = logging.getLogger("guardian")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def user_lock(user_id: str) -> threading.Lock:
    """One lock per user, so this process runs at most one review for a user at a time."""
    with _locks_guard:
        return _locks.setdefault(user_id, threading.Lock())


def reflect_pending(deps: Deps, user_id: str, build_model: Callable[[], Any]) -> int:
    """Review the user's finished, unreviewed sessions. Returns how many were reviewed.

    Safe to call at any time and from several places: if a review is already under
    way it returns 0, and a session is never reviewed twice.
    """
    if not chat_flow.is_valid_user_id(user_id):
        return 0
    lock = user_lock(user_id)
    if not lock.acquire(blocking=False):
        return 0
    try:
        now = deps.clock()
        if reviews.run_in_progress(deps.store, user_id, now):
            return 0
        reviews.recover_crashed(deps.store, user_id, now)
        waiting = reviews.sessions_to_review(deps.store, deps.datasource, user_id, now)
        if not waiting:
            return 0
        model = build_model()
        outcomes = [
            review_session(deps, model, user_id, session["id"]) for session in waiting
        ]
        return len([done for done in outcomes if done])
    finally:
        lock.release()


def review_session(deps: Deps, model: Any, user_id: str, session_id: str) -> bool:
    """Run the Reflector on one session. Returns whether the review was committed."""
    started = deps.clock()
    run = ReflectorRun(
        user_id=user_id,
        session_id=session_id,
        run_id=f"run_{uuid.uuid4().hex[:12]}",
        since=reviews.feedback_cursor(deps.store, user_id),
    )
    reviews.begin(deps.store, user_id, session_id, run.run_id, started)
    try:
        agent = build_reflector(deps, model, run)
        opening = run_input(run, values.render_values(deps.store, user_id))
        agent.invoke({"messages": [{"role": "user", "content": opening}]})
        summary = read_summary(deps, run)
    except Exception as error:  # noqa: BLE001 - any failure rolls the run back
        detail = config.redact_secrets(f"{type(error).__name__}: {error}")
        logger.error(
            "review of session %s failed and was rolled back: %s", session_id, detail
        )
        reviews.fail(deps.store, user_id, session_id)
        return False
    commit(deps, run, summary, started)
    return True


def read_summary(deps: Deps, run: ReflectorRun) -> reviews.Summary:
    """The run's session summary, checked. Raises ReviewError if it is missing or invalid."""
    name = reflector_files.session_summary_name(run.session_id)
    text = reflector_files.read_file(deps.store, run.user_id, name)
    if not text.strip():
        raise reviews.ReviewError("the run wrote no session summary")

    def missing_ids(bullet: str) -> list[str]:
        return checks.missing_evidence_ids(deps.datasource, run.user_id, bullet)

    return reviews.parse_summary(text, missing_ids)


def commit(
    deps: Deps, run: ReflectorRun, summary: reviews.Summary, started: datetime
) -> None:
    """Make a successful run's output visible and mark the session done."""
    store, user_id = deps.store, run.user_id
    hypotheses = reflector_files.read_file(store, user_id, reflector_files.HYPOTHESES)
    normalized = reviews.normalize_hypotheses(hypotheses)
    if normalized != hypotheses:
        reflector_files.write_text(
            store, user_id, reflector_files.HYPOTHESES, normalized
        )

    proposals.commit_staged(store, user_id, run.run_id)
    inbox.commit_run(store, user_id, run.run_id)
    learned_id = inbox.learned_card_id(run.session_id)
    if inbox.get_card(store, user_id, learned_id) is None:  # one card per session, ever
        card = inbox.new_card(
            user_id,
            inbox.KIND_LEARNED,
            reviews.learned_prompt(summary),
            deps.clock(),
            card_id=learned_id,
            session_id=run.session_id,
            run_id=run.run_id,
        )
        inbox.add_card(store, card)
    reviews.succeed(store, user_id, run.session_id, started)
