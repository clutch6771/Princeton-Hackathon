"""Which session is the user's "last session", and what the Guardian is told about it.

The last session is the most recently ended session that has judged reels. If the
Reflector has reviewed it, the Guardian gets its summary. If not, the Guardian is told
so and given the Judge's own records for that session, never an older summary. Either
way the session's time and reel count come first, so the user can tell which one is meant.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph.store.base import BaseStore

from guardian.datasource import DataSource, Reel, Session
from guardian.memory import reflector_files, reviews

TIMEZONE_VAR = (
    "GUARDIAN_TIMEZONE"  # an IANA name; the time zone sessions are described in
)
DEFAULT_TIMEZONE = "America/New_York"
MAX_REELS_LISTED = 5
NO_SESSION = "No finished session with judged reels yet."
NOT_REVIEWED = "This session has not been reviewed yet, so there is no summary of it."
USE_THE_RECORDS = "Tell the user that, then answer from the Judge's records below."


def local_zone() -> tzinfo:
    name = os.environ.get(TIMEZONE_VAR, "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def clock_time(moment: datetime) -> str:
    """A time as people say it: "4:03 pm"."""
    return moment.strftime("%I:%M %p").lstrip("0").lower()


def when(session: Session) -> str:
    """The session's day and its start and end, in local time."""
    zone = local_zone()
    start = session["start"].astimezone(zone)
    end = session["last_reel_at"].astimezone(zone)
    day = f"{start.strftime('%a %b')} {start.day}"
    return f"{day}, {clock_time(start)} to {clock_time(end)}"


def last_ended_session(
    datasource: DataSource, user_id: str, now: datetime
) -> tuple[Session, list[Reel]] | None:
    """The most recently ended session that has judged reels, with those reels."""
    ended = [
        s for s in datasource.list_sessions(user_id) if reviews.is_finished(s, now)
    ]
    for session in sorted(ended, key=lambda s: s["last_reel_at"], reverse=True):
        reels = datasource.get_session_reels(user_id, session["id"])
        if reels:  # a session with no judged reels is ignored
            return session, reels
    return None


def _listed(reels: list[Reel]) -> list[str]:
    lines = []
    for reel in reels[:MAX_REELS_LISTED]:
        touched = f" ({', '.join(reel['value_ids'])})" if reel["value_ids"] else ""
        lines.append(f"- {reel['description']}{touched}")
    return lines


def judge_records(reels: list[Reel]) -> list[str]:
    """What the Judge recorded for a session, in a few lines."""
    conflicts = [reel for reel in reels if reel["verdict"] == "conflict"]
    aligned = [reel for reel in reels if reel["verdict"] == "aligned"]
    others = [reel for reel in reels if reel["verdict"] != "conflict"]
    lines = [
        f"Of the {len(reels)} reels, {len(conflicts)} conflicted with their values, "
        f"{len(aligned)} matched them and {len(reels) - len(conflicts) - len(aligned)} "
        "were neutral."
    ]
    if conflicts:
        lines += ["Flagged as conflicts:", *_listed(conflicts)]
    if others:
        lines += ["Also watched:", *_listed(others)]
    return lines


def last_session_report(
    store: BaseStore, datasource: DataSource, user_id: str, now: datetime
) -> str:
    """What the Guardian reads when it looks up the user's last session."""
    found = last_ended_session(datasource, user_id, now)
    if found is None:
        return NO_SESSION
    session, reels = found
    header = f"The user's last session: {when(session)}. {len(reels)} judged reels."
    name = reflector_files.session_summary_name(session["id"])
    summary = reflector_files.read_file(store, user_id, name).strip()
    if summary:
        return f"{header}\n\n{summary}"
    return "\n".join(
        [header, f"{NOT_REVIEWED} {USE_THE_RECORDS}", *judge_records(reels)]
    )
