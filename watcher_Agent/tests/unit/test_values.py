"""Approved values: parsing, snapshot building, read-only reader, caching.

Missing, invalid, unapproved or over-cap values are an explicit unavailable
state. They never become a neutral judgment and are never truncated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from judge.contracts.examples import (
    EXAMPLE_VALUES_MARKDOWN,
    example_values_store_item,
    values_sha256,
)
from judge.contracts.values import (
    ApprovedValuesSnapshot,
    ValueStatus,
    ValuesUnavailable,
    ValuesUnavailableReason,
)
from judge.devfakes.clock import FakeClock
from judge.values.markdown_parser import ValuesFormatError, parse_values_markdown
from judge.values.reader import (
    CachedValuesReader,
    StoredMemoryItem,
    StoreValuesReader,
    build_snapshot,
)

pytestmark = pytest.mark.unit

OWNER = "demo-user-synthetic"
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = example_values_store_item().model_dump(mode="json")
    return {**payload, **overrides}


def _build(
    payload: dict[str, Any] | None, *, max_bytes: int = 16_384, require: bool = True
) -> ApprovedValuesSnapshot | ValuesUnavailable:
    item = None if payload is None else StoredMemoryItem(value=payload, updated_at=NOW)
    return build_snapshot(
        OWNER,
        item,
        max_bytes=max_bytes,
        require_approval=require,
        now=NOW,
        source="test",
    )


def _reason(result: ApprovedValuesSnapshot | ValuesUnavailable) -> str:
    assert isinstance(result, ValuesUnavailable)
    return result.reason.value


# --- parser -----------------------------------------------------------------


def test_parser_reads_every_field_and_keeps_the_users_wording() -> None:
    entries = parse_values_markdown(EXAMPLE_VALUES_MARKDOWN)

    v3 = next(e for e in entries if e.value_id == "V3")
    assert v3.title == "Fitness without body comparison"
    assert v3.strength == "high"
    assert v3.want_more == "form tutorials, training plans, recovery"
    assert v3.want_less == "before/after transformations, physique rating"
    assert v3.unless == "none"
    assert v3.in_my_words == '"gym stuff is great, comparing bodies is not"'
    assert v3.approved_at == "2026-11-14T09:05:00Z"
    assert v3.status is ValueStatus.ACTIVE
    assert v3.raw_markdown.startswith("## V3: Fitness without body comparison")


def test_parser_keeps_exception_clauses_verbatim() -> None:
    entries = parse_values_markdown(EXAMPLE_VALUES_MARKDOWN)

    v1 = next(e for e in entries if e.value_id == "V1")
    clause = "the challenge is a professional chef demonstrating technique"
    assert v1.unless == clause
    assert f"- Unless: {clause}" in v1.raw_markdown


def test_parser_marks_superseded_and_deleted_entries() -> None:
    content = EXAMPLE_VALUES_MARKDOWN + (
        "\n## V4: Old value (strength: low)\n- Want less: nothing\n- Status: deleted\n"
    )

    by_id = {e.value_id: e for e in parse_values_markdown(content)}

    assert by_id["V2"].status is ValueStatus.SUPERSEDED
    assert by_id["V2"].superseded_by == "V3"
    assert by_id["V4"].status is ValueStatus.DELETED


def test_parser_joins_continuation_lines_into_the_same_field() -> None:
    content = (
        "## V1: Cooking (strength: medium)\n"
        "- Unless: it is a professional chef\n"
        "  demonstrating a technique I asked about\n"
    )

    entry = parse_values_markdown(content)[0]

    assert entry.unless == (
        "it is a professional chef demonstrating a technique I asked about"
    )


def test_parser_takes_the_approval_date_from_evidence_when_not_explicit() -> None:
    content = (
        "## V9: Synthetic (strength: low)\n"
        "- Evidence: disagreed with a synthetic reel; approved 2026-11-12\n"
    )

    assert parse_values_markdown(content)[0].approved_at == "2026-11-12"


@pytest.mark.parametrize(
    "content",
    [
        "## Fitness without an id\n- Want more: x\n",
        "## V1: First\n- Want more: a\n\n## V1: Duplicate\n- Want more: b\n",
        "## 9bad: Starts with a digit\n- Want more: x\n",
    ],
)
def test_parser_rejects_malformed_documents(content: str) -> None:
    with pytest.raises(ValuesFormatError):
        parse_values_markdown(content)


def test_parser_returns_nothing_for_a_document_without_entries() -> None:
    assert parse_values_markdown("# Approved values\n\nNothing yet.\n") == ()


# --- snapshot building ------------------------------------------------------


def test_valid_store_item_becomes_a_pinned_snapshot() -> None:
    snapshot = _build(_payload())

    assert isinstance(snapshot, ApprovedValuesSnapshot)
    assert snapshot.revision == "7"
    assert snapshot.content_sha256 == values_sha256(EXAMPLE_VALUES_MARKDOWN)
    assert snapshot.content == EXAMPLE_VALUES_MARKDOWN
    assert snapshot.active_value_ids == frozenset({"V1", "V3"})
    assert snapshot.loaded_at == NOW
    assert snapshot.approved_at == datetime(2026, 11, 14, 9, 5, tzinfo=UTC)


def test_content_stored_as_a_list_of_lines_is_canonicalised() -> None:
    lines = EXAMPLE_VALUES_MARKDOWN.split("\n")

    snapshot = _build(_payload(content=lines))

    assert isinstance(snapshot, ApprovedValuesSnapshot)
    assert snapshot.content == EXAMPLE_VALUES_MARKDOWN


def test_revision_falls_back_to_the_content_hash() -> None:
    snapshot = _build(_payload(revision=None))

    assert isinstance(snapshot, ApprovedValuesSnapshot)
    assert snapshot.revision == f"sha256:{snapshot.content_sha256[:16]}"


def test_missing_item_is_unavailable_not_neutral() -> None:
    assert _reason(_build(None)) == "missing"


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": "shape"},
        {"content": 42},
        {**_payload(), "sha256": "not-a-hash"},
        {**_payload(), "content": "## broken heading with no id\n- Want more: x\n"},
    ],
)
def test_invalid_payload_or_markdown_is_unavailable(payload: dict[str, Any]) -> None:
    assert _reason(_build(payload)) in {"invalid", "hash_mismatch"}


def test_hash_mismatch_is_detected() -> None:
    assert _reason(_build(_payload(sha256="0" * 64))) == "hash_mismatch"


def test_missing_hash_is_accepted_and_computed_locally() -> None:
    snapshot = _build(_payload(sha256=None))

    assert isinstance(snapshot, ApprovedValuesSnapshot)
    assert snapshot.content_sha256 == values_sha256(EXAMPLE_VALUES_MARKDOWN)


@pytest.mark.parametrize(
    "approval", [None, {"status": "proposed"}, {"status": "rejected"}]
)
def test_unapproved_values_are_never_used(approval: dict[str, Any] | None) -> None:
    assert _reason(_build(_payload(approval=approval))) == "unapproved"


def test_approval_metadata_requirement_can_be_relaxed_by_configuration() -> None:
    snapshot = _build(_payload(approval=None), require=False)

    assert isinstance(snapshot, ApprovedValuesSnapshot)


def test_explicitly_unapproved_values_are_refused_even_when_relaxed() -> None:
    result = _build(_payload(approval={"status": "proposed"}), require=False)

    assert _reason(result) == "unapproved"


def test_deleted_document_is_unavailable() -> None:
    assert _reason(_build(_payload(lifecycle={"status": "deleted"}))) == "deleted"


@pytest.mark.parametrize("content", ["", "   \n\n", "# Approved values\n\nNone yet."])
def test_empty_values_are_unavailable(content: str) -> None:
    payload = _payload(content=content, sha256=None)

    assert _reason(_build(payload)) == "empty"


def test_document_with_only_superseded_or_deleted_entries_is_empty() -> None:
    content = "## V1: Old (strength: low)\n- Want less: x\n- Status: deleted\n"

    assert _reason(_build(_payload(content=content, sha256=None))) == "empty"


def test_values_over_the_cap_are_rejected_whole_never_truncated() -> None:
    result = _build(_payload(), max_bytes=200)

    assert _reason(result) == "over_cap"


def test_cap_is_measured_in_utf8_bytes() -> None:
    content = "## V1: Café culture (strength: low)\n- Want more: " + "é" * 60 + "\n"
    payload = _payload(content=content, sha256=None)

    assert isinstance(_build(payload, max_bytes=200), ApprovedValuesSnapshot)
    assert _reason(_build(payload, max_bytes=120)) == "over_cap"


def test_extra_keys_written_by_the_memory_owner_are_tolerated() -> None:
    payload = _payload(created_at="2026-11-14T09:00:00Z", modified_at="x")

    assert isinstance(_build(payload), ApprovedValuesSnapshot)


# --- reader and cache -------------------------------------------------------


class _Source:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload
        self.calls = 0
        self.fail = False

    async def fetch(self, owner_id: str) -> StoredMemoryItem | None:
        self.calls += 1
        if self.fail:
            raise ConnectionError("synthetic store outage")
        if self.payload is None:
            return None
        return StoredMemoryItem(value=self.payload, updated_at=NOW)


def _reader(source: _Source, clock: FakeClock) -> StoreValuesReader:
    return StoreValuesReader(
        source, max_bytes=16_384, require_approval=True, clock=clock
    )


async def test_reader_returns_a_snapshot_for_the_owner() -> None:
    reader = _reader(_Source(_payload()), FakeClock(NOW))

    snapshot = await reader.read(OWNER)

    assert isinstance(snapshot, ApprovedValuesSnapshot)
    assert snapshot.owner_id == OWNER


async def test_store_failure_is_unavailable_not_an_exception() -> None:
    source = _Source(_payload())
    source.fail = True

    result = await _reader(source, FakeClock(NOW)).read(OWNER)

    assert _reason(result) == "store_error"


async def test_cache_pins_a_snapshot_for_a_short_ttl() -> None:
    clock = FakeClock(NOW)
    source = _Source(_payload())
    cached = CachedValuesReader(_reader(source, clock), ttl_seconds=5, clock=clock)

    first = await cached.read(OWNER)
    second = await cached.read(OWNER)
    clock.advance(5.1)
    third = await cached.read(OWNER)

    assert first is second
    assert third is not first
    assert source.calls == 2


async def test_new_revision_is_picked_up_after_the_ttl() -> None:
    clock = FakeClock(NOW)
    source = _Source(_payload())
    cached = CachedValuesReader(_reader(source, clock), ttl_seconds=5, clock=clock)
    old = await cached.read(OWNER)

    source.payload = _payload(revision=8)
    clock.advance(6)
    new = await cached.read(OWNER)

    assert isinstance(old, ApprovedValuesSnapshot)
    assert isinstance(new, ApprovedValuesSnapshot)
    assert (old.revision, new.revision) == ("7", "8")


async def test_unavailable_results_are_not_cached() -> None:
    clock = FakeClock(NOW)
    source = _Source(None)
    cached = CachedValuesReader(_reader(source, clock), ttl_seconds=5, clock=clock)

    assert _reason(await cached.read(OWNER)) == "missing"
    source.payload = _payload()

    assert isinstance(await cached.read(OWNER), ApprovedValuesSnapshot)


async def test_invalidate_drops_the_cached_snapshot() -> None:
    clock = FakeClock(NOW)
    source = _Source(_payload())
    cached = CachedValuesReader(_reader(source, clock), ttl_seconds=60, clock=clock)
    await cached.read(OWNER)

    cached.invalidate(OWNER)
    await cached.read(OWNER)

    assert source.calls == 2


def test_unavailable_reasons_map_to_reel_error_codes() -> None:
    codes = {
        ValuesUnavailable(owner_id=OWNER, reason=reason).reel_error_code.value
        for reason in ValuesUnavailableReason
    }

    assert codes == {f"values_{reason.value}" for reason in ValuesUnavailableReason}
