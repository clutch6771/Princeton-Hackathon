"""The quote check and the evidence check, enforced in code for both agents.

Quote check: an "In my words" quote must appear as a continuous passage in something
this user wrote (a chat message or a feedback comment), ignoring case, spacing and
surrounding punctuation. Evidence check: every reel, feedback or session ID that is
cited must exist for this user.
"""

from __future__ import annotations

import re
import string
from collections.abc import Iterable
from datetime import UTC, datetime

from langgraph.store.base import BaseStore

from guardian.datasource import DataSource
from guardian.memory import records

BEGINNING_OF_TIME = datetime(2000, 1, 1, tzinfo=UTC)
# Feedback comments from these sources are not the user's typed words: "chat" comments
# are written by the chat agent's tool, and "app" comments are preset correction
# strings the user tapped.
NOT_TYPED_SOURCES = frozenset({"chat", "app"})
_STRAIGHT_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
# An evidence ID is either a seed-style ID (rl_001, fb_001, s_001) or a UUID, which is
# what the Judge gives its reels, sessions and feedback.
_HEX = "[0-9a-fA-F]"
_UUID_PATTERN = f"{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}}"
_UUID = re.compile(rf"\b{_UUID_PATTERN}\b")
_EVIDENCE_ID = re.compile(rf"\b(?:(?:rl|fb|s)_[A-Za-z0-9]+|{_UUID_PATTERN})\b")
_SEED_REEL_PREFIX = "rl_"


def normalize(text: str) -> str:
    """Lower-case, straighten quotes and collapse whitespace."""
    return " ".join(text.lower().translate(_STRAIGHT_QUOTES).split())


def quote_found(quote: str, sources: Iterable[str]) -> bool:
    """Whether the quote appears word for word in one of the sources.

    The quote must begin and end on word boundaries: "an" is not a quote of
    "I want less", though the letters are there.
    """
    wanted = normalize(quote).strip(string.punctuation + " ")
    if not wanted:
        return False
    whole_words = re.compile(rf"(?<!\w){re.escape(wanted)}(?!\w)")
    return any(whole_words.search(normalize(source)) for source in sources)


def user_statements(
    store: BaseStore, datasource: DataSource, user_id: str
) -> list[str]:
    """Everything this user wrote: chat messages and feedback comments they typed.

    Comments on feedback recorded through chat are left out. The chat agent writes
    those, often as a paraphrase, and the user's real words are already in the chat
    log. Comments on feedback sent by the app are left out too: the app sends a
    preset correction, not typed text. Counting either would let wording the user
    never wrote pass as their own.
    """
    comments = [
        item["comment"]
        for item in datasource.get_feedback(user_id, BEGINNING_OF_TIME)
        if item["comment"] and item["source"] not in NOT_TYPED_SOURCES
    ]
    return [*records.user_chat_messages(store, user_id), *comments]


def is_uuid(identifier: str) -> bool:
    return bool(_UUID.fullmatch(identifier))


def uuids_in(text: str) -> list[str]:
    """UUIDs mentioned in the text, lower-cased as the Judge stores them."""
    return [match.group(0).lower() for match in _UUID.finditer(text)]


def cited_ids(text: str) -> list[str]:
    """Reel, feedback and session IDs mentioned in the text, in order, without repeats.

    UUIDs are lower-cased, which is how the Judge stores them.
    """
    found = (match.group(0) for match in _EVIDENCE_ID.finditer(text))
    return list(dict.fromkeys(i.lower() if is_uuid(i) else i for i in found))


def missing_evidence_ids(datasource: DataSource, user_id: str, text: str) -> list[str]:
    """Cited IDs that do not exist for this user."""
    cited = cited_ids(text)
    if not cited:
        return []
    # A UUID does not say what it names, so it may be a reel, feedback or a session.
    reel_ids = [i for i in cited if i.startswith(_SEED_REEL_PREFIX) or is_uuid(i)]
    known = {
        *(reel["id"] for reel in datasource.get_reels(user_id, reel_ids)),
        *(item["id"] for item in datasource.get_feedback(user_id, BEGINNING_OF_TIME)),
        *(session["id"] for session in datasource.list_sessions(user_id)),
    }
    return [identifier for identifier in cited if identifier not in known]
