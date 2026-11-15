"""Tests for what the Guardian is told about the user's last session.

"Last session" is the most recently ended session that has judged reels. If the
Reflector has not reviewed it yet, the Guardian is told so and given the Judge's
records for it, never an older session's summary.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import ToolMessage

from guardian import api, prompts
from guardian.datasource import SEED_DIR, FakeDataSource
from guardian.memory import reflector_files, sessions
from guardian.tests.conftest import NOW, USER
from guardian.tests.fakes import reply, tool_call

THREAD = f"{USER}:2026-11-14"
OLD_SUMMARY = "# Session s_001\nMostly rehab and three hustle reels.\n\n## What I learned\n- Old. [fb_001]"
NEW_SUMMARY = (
    "# Session s_002\nA short evening session.\n\n## What I learned\n- New. [rl_014]"
)


def write_summary(store, session_id: str, text: str) -> None:
    name = reflector_files.session_summary_name(session_id)
    reflector_files.write_text(store, USER, name, text)


def what_the_guardian_reads(start) -> str:
    """Ask about the last session and return what the read_memory tool gave the model."""
    model = start([tool_call("read_memory", {"name": "last_session"}), reply("ok")])
    api.chat(USER, THREAD, "what did I watch in my last session?")
    return str([m for m in model.calls[1] if isinstance(m, ToolMessage)][-1].content)


def test_an_unreviewed_last_session_is_reported_from_the_judges_records(
    start, with_values
):
    text = what_the_guardian_reads(start)

    assert sessions.NOT_REVIEWED in text
    assert "8 judged reels" in text  # the demo session, s_002, is the latest
    assert "Sat Nov 14, 4:03 pm to 4:07 pm" in text  # Eastern time
    assert "4 conflicted with their values" in text
    assert "Before-and-after body transformation montage" in text  # a flagged reel


def test_an_older_sessions_summary_is_never_used_for_the_last_session(
    start, with_values, store
):
    write_summary(store, "s_001", OLD_SUMMARY)

    text = what_the_guardian_reads(start)

    assert sessions.NOT_REVIEWED in text and "8 judged reels" in text
    assert "Mostly rehab" not in text and "Old." not in text


def test_a_reviewed_last_session_gives_its_summary_with_its_time_and_reel_count(
    start, with_values, store
):
    write_summary(store, "s_001", OLD_SUMMARY)
    write_summary(store, "s_002", NEW_SUMMARY)

    text = what_the_guardian_reads(start)

    assert "A short evening session." in text and "Mostly rehab" not in text
    assert "8 judged reels" in text and "Sat Nov 14" in text
    assert sessions.NOT_REVIEWED not in text


class WithLaterEmptySession:
    """Adds a later finished session that has no judged reels."""

    def __init__(self, inner: FakeDataSource) -> None:
        self._inner = inner

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        recent = NOW - timedelta(hours=1)
        empty = {
            "id": "s_empty",
            "start": recent,
            "last_reel_at": recent,
            "stopped": True,
        }
        return [*self._inner.list_sessions(user_id), empty]

    def get_session_reels(self, user_id: str, session_id: str) -> list[Any]:
        if session_id == "s_empty":
            return []
        return self._inner.get_session_reels(user_id, session_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_a_session_with_no_judged_reels_is_ignored(store, datasource):
    report = sessions.last_session_report(
        store, WithLaterEmptySession(datasource), USER, NOW
    )

    assert "8 judged reels" in report and "s_empty" not in report


def test_a_session_still_in_progress_is_not_the_last_session(store, clock):
    recording = FakeDataSource.from_records(
        USER,
        reels=json.loads((SEED_DIR / "reels.json").read_text()),
        feedback=[],
        clock=clock,
        open_sessions=frozenset({"s_002"}),
    )
    during = datetime(
        2026, 11, 14, 21, 10, tzinfo=UTC
    )  # three minutes after its last reel
    still_open = sessions.last_session_report(store, recording, USER, during)

    assert "12 judged reels" in still_open  # s_001, the last one that has ended


def test_with_no_finished_session_the_guardian_is_told_there_is_none(store, clock):
    nothing = FakeDataSource.from_records(USER, reels=[], feedback=[], clock=clock)

    assert (
        sessions.last_session_report(store, nothing, USER, NOW) == sessions.NO_SESSION
    )


def test_the_prompt_tells_the_guardian_how_to_describe_a_session():
    text = " ".join(prompts.GUARDIAN_PROMPT.split())

    assert "say which session you mean: its time and how many reels" in text
    assert "has not been reviewed yet, say so" in text
