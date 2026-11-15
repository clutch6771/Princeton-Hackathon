"""Tests for guardian.memory.formats: the values.md entry format, which is a contract."""

from __future__ import annotations

from datetime import date

import pytest

from guardian.memory import formats

ENTRY = """## V3: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: disagreed with rl_112; approved 2026-11-14"""


def lines_without(prefix: str) -> str:
    return "\n".join(line for line in ENTRY.splitlines() if not line.startswith(prefix))


def test_parse_reads_every_field_of_a_valid_entry():
    parsed = formats.parse_entry(ENTRY)

    assert parsed.ok and parsed.error is None
    entry = parsed.entry
    assert (entry.header_id, entry.title, entry.strength) == (
        "V3",
        "Fitness without body comparison",
        "high",
    )
    assert entry.want_more == "form tutorials, training plans, recovery"
    assert entry.want_less == "before/after transformations, physique rating"
    assert entry.unless == "none"
    assert (entry.wording_label, entry.wording_text) == (
        "In my words",
        "gym stuff is great, comparing bodies is not",
    )
    assert entry.evidence == "disagreed with rl_112; approved 2026-11-14"


def test_render_reproduces_the_canonical_entry():
    assert formats.render_entry(formats.parse_entry(ENTRY).entry) == ENTRY


def test_parse_accepts_a_new_value_header():
    parsed = formats.parse_entry(ENTRY.replace("## V3:", "## New:"))

    assert parsed.ok
    assert parsed.entry.header_id == "New"


@pytest.mark.parametrize(
    "label", ["In my words", "Agreed wording", "Suggested wording"]
)
def test_parse_accepts_each_wording_label(label: str):
    parsed = formats.parse_entry(ENTRY.replace("In my words", label))

    assert parsed.ok
    assert parsed.entry.wording_label == label


def test_parse_tolerates_blank_lines_trailing_spaces_and_curly_or_missing_quotes():
    messy = ENTRY.replace("\n", "  \n\n").replace(
        '"gym stuff is great, comparing bodies is not"',
        "“gym stuff is great, comparing bodies is not”",
    )
    unquoted = ENTRY.replace(
        '"gym stuff is great, comparing bodies is not"',
        "gym stuff is great, comparing bodies is not",
    )

    assert formats.render_entry(formats.parse_entry(messy).entry) == ENTRY
    assert formats.render_entry(formats.parse_entry(unquoted).entry) == ENTRY


@pytest.mark.parametrize(
    "text",
    [
        lines_without("- Unless"),
        lines_without("- Evidence"),
        lines_without("- In my words"),
        ENTRY.replace("(strength: high)", "(strength: enormous)"),
        ENTRY.replace("## V3:", "## 3:"),
        ENTRY.replace("## V3: Fitness without body comparison", "## V3:"),
        ENTRY.replace(
            "- Want more: form tutorials, training plans, recovery", "- Want more:"
        ),
        ENTRY.replace(
            '- In my words: "gym stuff is great, comparing bodies is not"',
            '- In my words: ""',
        ),
        "",
        "just a sentence",
    ],
)
def test_parse_rejects_malformed_entries_with_a_message(text: str):
    parsed = formats.parse_entry(text)

    assert not parsed.ok
    assert parsed.entry is None
    assert parsed.error


def test_parse_rejects_lines_in_the_wrong_order():
    lines = ENTRY.splitlines()
    swapped = "\n".join([lines[0], lines[2], lines[1], *lines[3:]])

    assert not formats.parse_entry(swapped).ok


def test_parse_rejects_two_wording_lines_and_says_so():
    doubled = ENTRY.replace(
        "- Evidence:", '- Suggested wording: "another"\n- Evidence:'
    )

    parsed = formats.parse_entry(doubled)

    assert not parsed.ok
    assert "exactly one" in parsed.error


def test_value_id_helpers():
    assert formats.is_value_id("V3") and formats.is_value_id("V12")
    assert (
        not formats.is_value_id("V0")
        and not formats.is_value_id("new")
        and not formats.is_value_id("v3")
    )
    assert formats.value_number("V12") == 12


def test_stamp_approved_appends_the_date_once():
    day = date(2026, 11, 14)

    assert (
        formats.stamp_approved("said at onboarding", day)
        == "said at onboarding; approved 2026-11-14"
    )
    assert (
        formats.stamp_approved("fb_001; approved 2026-11-01", day)
        == "fb_001; approved 2026-11-14"
    )
    assert formats.stamp_approved("User cites transformation videos.", day) == (
        "User cites transformation videos; approved 2026-11-14"
    )


def test_stamp_approved_removes_an_earlier_stamp_anywhere_in_the_line():
    day = date(2026, 11, 15)
    carried = "said at onboarding; approved 2026-11-14; fb_001, fb_003"

    assert formats.stamp_approved(carried, day) == (
        "said at onboarding; fb_001, fb_003; approved 2026-11-15"
    )
    assert formats.stamp_approved("approved 2026-11-14", day) == "approved 2026-11-15"


def test_same_text_ignores_case_and_spacing():
    assert formats.same_text("Gym  stuff\nis GREAT", "gym stuff is great")
    assert not formats.same_text("gym stuff", "gym things")
