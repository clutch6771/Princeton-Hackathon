"""DataSource protocol, record types and FakeDataSource.

Everything the Guardian and Reflector read about reels and feedback comes through
`DataSource` (docs/GUARDIAN_SPEC_V5.md, "DataSource the Guardian consumes"). The backend
team supplies the real implementation; `FakeDataSource` serves the seed files.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict

SEED_DIR = Path(__file__).resolve().parent / "seed"
UNLISTED_SEED_SESSIONS = frozenset(
    {"s_heldout"}
)  # scoring only; never reviewed or searched

FEEDBACK_LABELS = ("agree", "disagree", "not_for_me", "fine")
FEEDBACK_SOURCES = ("app", "chat", "rerate", "share")
DEFAULT_FEEDBACK_SOURCE = "app"
TOP_COUNT = 5
STOPWORDS = frozenset(
    "a an and are as at be by for from i in is it my of on or that the this to was with you your".split()
)


class Reel(TypedDict):
    id: str
    session_id: str
    start: datetime
    end: datetime
    thumbnail_url: str
    transcript: str  # may be empty for music-only reels
    on_screen_text: str
    description: str
    features: dict[str, Any]  # topics, format, tone, hook, emotional_pull, is_ad
    verdict: Literal["aligned", "neutral", "conflict"]
    confidence: float
    value_ids: list[str]
    reason: str


class Session(TypedDict):
    id: str
    start: datetime
    last_reel_at: datetime
    stopped: bool


class ReelSummary(TypedDict):
    id: str
    one_line: str
    verdict: str
    confidence: float


class Feedback(TypedDict):
    id: str
    reel_id: str | None
    ts: datetime
    label: Literal["agree", "disagree", "not_for_me", "fine"]
    comment: str | None
    source: Literal["app", "chat", "rerate", "share"]
    link: str | None  # source == "share" only


class NewFeedback(TypedDict, total=False):
    reel_id: str
    label: str
    comment: str
    source: str


class FeedbackExample(TypedDict):
    reel: ReelSummary
    label: str
    comment: str | None


class FeedProfile(TypedDict):
    reel_count: int
    conflict_count: int
    top_topics: list[tuple[str, int]]
    top_tones: list[tuple[str, int]]
    top_formats: list[tuple[str, int]]
    top_emotional_pulls: list[tuple[str, int]]


class DataSource(Protocol):
    def get_reels(self, user_id: str, reel_ids: list[str]) -> list[Reel]: ...
    def list_sessions(self, user_id: str) -> list[Session]: ...
    def get_session_reels(self, user_id: str, session_id: str) -> list[Reel]: ...
    def search_reels(self, user_id: str, query: str, k: int) -> list[ReelSummary]: ...
    def find_similar_feedback(
        self, user_id: str, text: str, k: int
    ) -> list[FeedbackExample]: ...
    def get_feedback(self, user_id: str, since: datetime) -> list[Feedback]: ...
    def feed_profile(
        self, user_id: str, session_id: str | None, since: datetime | None
    ) -> FeedProfile: ...
    def record_feedback(self, user_id: str, feedback: NewFeedback) -> str: ...


def parse_time(value: datetime | str) -> datetime:
    """Accept a datetime or an ISO 8601 string; always return an aware datetime."""
    moment = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def to_reel(record: dict[str, Any]) -> Reel:
    """Build a Reel from a stored record, ignoring fields the Guardian does not use."""
    try:
        return Reel(
            id=str(record["id"]),
            session_id=str(record["session_id"]),
            start=parse_time(record["start"]),
            end=parse_time(record["end"]),
            thumbnail_url=str(record.get("thumbnail_url", "")),
            transcript=str(record.get("transcript") or ""),
            on_screen_text=str(record.get("on_screen_text") or ""),
            description=str(record["description"]),
            features=dict(record.get("features") or {}),
            verdict=record["verdict"],
            confidence=float(record["confidence"]),
            value_ids=[str(v) for v in record.get("value_ids") or []],
            reason=str(record.get("reason") or ""),
        )
    except KeyError as missing:
        raise ValueError(
            f"reel record is missing required field {missing}"
        ) from missing


def to_feedback(record: dict[str, Any]) -> Feedback:
    return Feedback(
        id=str(record["id"]),
        reel_id=record.get("reel_id"),
        ts=parse_time(record["ts"]),
        label=record["label"],
        comment=record.get("comment"),
        source=record.get("source", DEFAULT_FEEDBACK_SOURCE),
        link=record.get("link"),
    )


def summarize(reel: Reel) -> ReelSummary:
    return ReelSummary(
        id=reel["id"],
        one_line=reel["description"],
        verdict=reel["verdict"],
        confidence=reel["confidence"],
    )


def tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.lower())) - STOPWORDS


def reel_text(reel: Reel) -> str:
    topics = " ".join(str(topic) for topic in reel["features"].get("topics", []))
    return " ".join(
        (reel["description"], reel["transcript"], reel["on_screen_text"], topics)
    )


def top_counts(values: Iterable[str]) -> list[tuple[str, int]]:
    return Counter(value for value in values if value).most_common(TOP_COUNT)


class FakeDataSource:
    """Serves one user's seed data. Similarity is keyword overlap, not embeddings."""

    def __init__(
        self,
        user_id: str,
        reels: Iterable[Reel],
        feedback: Iterable[Feedback],
        clock: Callable[[], datetime] | None = None,
        unlisted_sessions: frozenset[str] = UNLISTED_SEED_SESSIONS,
        open_sessions: frozenset[str] = frozenset(),
    ) -> None:
        self._user_id = user_id
        self._reels = tuple(sorted(reels, key=lambda reel: reel["start"]))
        self._feedback = tuple(feedback)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._unlisted_sessions = unlisted_sessions
        self._open_sessions = open_sessions

    @classmethod
    def from_seed(
        cls,
        user_id: str,
        seed_dir: Path = SEED_DIR,
        clock: Callable[[], datetime] | None = None,
    ) -> FakeDataSource:
        reels = json.loads((seed_dir / "reels.json").read_text())
        feedback = json.loads((seed_dir / "feedback.json").read_text())
        return cls.from_records(user_id, reels=reels, feedback=feedback, clock=clock)

    @classmethod
    def from_records(
        cls,
        user_id: str,
        reels: Iterable[dict[str, Any]],
        feedback: Iterable[dict[str, Any]],
        clock: Callable[[], datetime] | None = None,
        unlisted_sessions: frozenset[str] = UNLISTED_SEED_SESSIONS,
        open_sessions: frozenset[str] = frozenset(),
    ) -> FakeDataSource:
        """Build from stored records: seed files or real Judge output."""
        return cls(
            user_id,
            reels=[to_reel(record) for record in reels],
            feedback=[to_feedback(record) for record in feedback],
            clock=clock,
            unlisted_sessions=unlisted_sessions,
            open_sessions=open_sessions,
        )

    # --- reads ---------------------------------------------------------------------

    def get_reels(self, user_id: str, reel_ids: list[str]) -> list[Reel]:
        by_id = {reel["id"]: reel for reel in self._all_reels(user_id)}
        return [Reel(**by_id[reel_id]) for reel_id in reel_ids if reel_id in by_id]

    def list_sessions(self, user_id: str) -> list[Session]:
        sessions: dict[str, Session] = {}
        for reel in self._listed_reels(user_id):
            current = sessions.get(reel["session_id"])
            sessions[reel["session_id"]] = Session(
                id=reel["session_id"],
                start=(
                    min(current["start"], reel["start"]) if current else reel["start"]
                ),
                last_reel_at=(
                    max(current["last_reel_at"], reel["end"])
                    if current
                    else reel["end"]
                ),
                stopped=reel["session_id"] not in self._open_sessions,
            )
        return sorted(sessions.values(), key=lambda session: session["start"])

    def get_session_reels(self, user_id: str, session_id: str) -> list[Reel]:
        return [
            Reel(**reel)
            for reel in self._all_reels(user_id)
            if reel["session_id"] == session_id
        ]

    def search_reels(self, user_id: str, query: str, k: int) -> list[ReelSummary]:
        wanted = tokens(query)
        scored = [
            (len(wanted & tokens(reel_text(reel))), reel)
            for reel in self._listed_reels(user_id)
        ]
        ranked = sorted(
            (pair for pair in scored if pair[0] > 0),
            key=lambda pair: (-pair[0], pair[1]["id"]),
        )
        return [summarize(reel) for _, reel in ranked[: max(k, 0)]]

    def find_similar_feedback(
        self, user_id: str, text: str, k: int
    ) -> list[FeedbackExample]:
        wanted = tokens(text)
        by_id = {reel["id"]: reel for reel in self._listed_reels(user_id)}
        scored = []
        for item in self._user_feedback(user_id):
            reel = by_id.get(item["reel_id"] or "")
            if reel is None:
                continue
            score = len(wanted & tokens(f"{reel_text(reel)} {item['comment'] or ''}"))
            if score > 0:
                scored.append((score, item, reel))
        ranked = sorted(scored, key=lambda triple: (-triple[0], triple[1]["id"]))
        return [
            FeedbackExample(
                reel=summarize(reel), label=item["label"], comment=item["comment"]
            )
            for _, item, reel in ranked[: max(k, 0)]
        ]

    def get_feedback(self, user_id: str, since: datetime) -> list[Feedback]:
        return [
            Feedback(**item)
            for item in self._user_feedback(user_id)
            if item["ts"] >= since
        ]

    def feed_profile(
        self, user_id: str, session_id: str | None, since: datetime | None
    ) -> FeedProfile:
        reels = [
            reel
            for reel in self._listed_reels(user_id)
            if (session_id is None or reel["session_id"] == session_id)
            and (since is None or reel["start"] >= since)
        ]
        features = [reel["features"] for reel in reels]
        return FeedProfile(
            reel_count=len(reels),
            conflict_count=len(
                [reel for reel in reels if reel["verdict"] == "conflict"]
            ),
            top_topics=top_counts(
                str(topic) for f in features for topic in f.get("topics", [])
            ),
            top_tones=top_counts(str(f.get("tone", "")) for f in features),
            top_formats=top_counts(str(f.get("format", "")) for f in features),
            top_emotional_pulls=top_counts(
                str(f.get("emotional_pull", "")) for f in features
            ),
        )

    # --- write ---------------------------------------------------------------------

    def record_feedback(self, user_id: str, feedback: NewFeedback) -> str:
        """Store one feedback item and return its ID. Raises ValueError on invalid input."""
        if user_id != self._user_id:
            raise ValueError("unknown user")
        label = feedback.get("label")
        source = feedback.get("source", DEFAULT_FEEDBACK_SOURCE)
        reel_id = feedback.get("reel_id")
        if label not in FEEDBACK_LABELS:
            raise ValueError(f"label must be one of {', '.join(FEEDBACK_LABELS)}")
        if source not in FEEDBACK_SOURCES:
            raise ValueError(f"source must be one of {', '.join(FEEDBACK_SOURCES)}")
        if reel_id is not None and not self.get_reels(user_id, [reel_id]):
            raise ValueError(f"reel {reel_id} does not exist")

        item = Feedback(
            id=self._next_feedback_id(),
            reel_id=reel_id,
            ts=self._clock(),
            label=label,
            comment=feedback.get("comment") or None,
            source=source,
            link=None,
        )
        self._feedback = (*self._feedback, item)
        return item["id"]

    # --- helpers -------------------------------------------------------------------

    def _all_reels(self, user_id: str) -> tuple[Reel, ...]:
        return self._reels if user_id == self._user_id else ()

    def _listed_reels(self, user_id: str) -> list[Reel]:
        return [
            reel
            for reel in self._all_reels(user_id)
            if reel["session_id"] not in self._unlisted_sessions
        ]

    def _user_feedback(self, user_id: str) -> tuple[Feedback, ...]:
        return self._feedback if user_id == self._user_id else ()

    def _next_feedback_id(self) -> str:
        taken = {item["id"] for item in self._feedback}
        number = len(taken) + 1
        while f"fb_{number:03d}" in taken:
            number += 1
        return f"fb_{number:03d}"
