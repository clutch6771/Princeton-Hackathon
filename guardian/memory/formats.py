"""The values.md entry format.

The Judge reads this format on every reel, so it is a contract and is validated
before every write. Parsing is tolerant of spacing, letter case and quote style;
rendering is canonical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

WORDING_OWN = "In my words"
WORDING_AGREED = "Agreed wording"
WORDING_SUGGESTED = "Suggested wording"
WORDING_LABELS = (WORDING_OWN, WORDING_AGREED, WORDING_SUGGESTED)

NEW_HEADER_ID = "New"
ENTRY_LINE_COUNT = 6
QUOTE_CHARACTERS = "\"'“”‘’"

FORMAT_HELP = """A value entry has exactly these six lines:
## V<number>: <title> (strength: low | medium | high)
- Want more: ...
- Want less: ...
- Unless: ... (or "none")
- In my words: "<exact quote of the user>"
- Evidence: ...
For a new value write the header as "## New: <title> (strength: ...)".
Without an exact quote, replace the fifth line with: - Suggested wording: "<your wording>\""""

# Shown to a user whose edit of a card could not be read.
EDIT_HELP = (
    "Keep the card's six lines: the ## header, Want more, Want less, Unless, "
    "the wording line and Evidence. Change only the text after each label."
)

_HEADER = re.compile(
    r"^##\s+(V[1-9]\d*|New):\s*(\S.*?)\s*\(strength:\s*(low|medium|high)\)$",
    re.IGNORECASE,
)
_WORDING = re.compile(
    r"^-\s*(In my words|Agreed wording|Suggested wording):\s*(.*)$", re.IGNORECASE
)
_VALUE_ID = re.compile(r"^V[1-9]\d*$")
_APPROVED_STAMP = re.compile(r"approved \d{4}-\d{2}-\d{2}")
_CANONICAL_LABEL = {label.lower(): label for label in WORDING_LABELS}


@dataclass(frozen=True)
class ValueEntry:
    header_id: str  # "V3", or "New" in a proposal for a new value
    title: str
    strength: str
    want_more: str
    want_less: str
    unless: str
    wording_label: str
    wording_text: str
    evidence: str


@dataclass(frozen=True)
class Parsed:
    entry: ValueEntry | None
    error: str | None  # the reason plus the full format, written for an agent
    reason: str | None = None  # the reason alone

    @property
    def ok(self) -> bool:
        return self.entry is not None


def _fail(reason: str) -> Parsed:
    return Parsed(entry=None, error=f"{reason}\n{FORMAT_HELP}", reason=reason)


def _field(line: str, label: str) -> str | None:
    match = re.match(rf"^-\s*{re.escape(label)}:\s*(.+)$", line, re.IGNORECASE)
    return match.group(1).strip() if match else None


def parse_entry(text: str) -> Parsed:
    """Parse one entry. Returns the entry, or an error message that shows the format."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    wording_lines = [line for line in lines if _WORDING.match(line)]
    if len(lines) > 1 and len(wording_lines) != 1:
        return _fail(
            f"An entry must contain exactly one wording line; this one has {len(wording_lines)}."
        )
    if len(lines) != ENTRY_LINE_COUNT:
        return _fail(
            f"An entry has {ENTRY_LINE_COUNT} lines; this one has {len(lines)}."
        )

    header = _HEADER.match(lines[0])
    if header is None:
        return _fail("The first line is not a valid header.")
    want_more = _field(lines[1], "Want more")
    want_less = _field(lines[2], "Want less")
    unless = _field(lines[3], "Unless")
    wording = _WORDING.match(lines[4])
    evidence = _field(lines[5], "Evidence")
    if (
        want_more is None
        or want_less is None
        or unless is None
        or evidence is None
        or wording is None
    ):
        return _fail("The lines are missing, empty or in the wrong order.")
    wording_text = wording.group(2).strip().strip(QUOTE_CHARACTERS).strip()
    if not wording_text:
        return _fail("The wording line is empty.")

    raw_id = header.group(1)
    return Parsed(
        entry=ValueEntry(
            header_id=NEW_HEADER_ID if raw_id.lower() == "new" else raw_id.upper(),
            title=header.group(2).strip(),
            strength=header.group(3).lower(),
            want_more=want_more,
            want_less=want_less,
            unless=unless,
            wording_label=_CANONICAL_LABEL[wording.group(1).lower()],
            wording_text=wording_text,
            evidence=evidence,
        ),
        error=None,
    )


def render_entry(entry: ValueEntry) -> str:
    """The canonical text of an entry."""
    return "\n".join(
        (
            f"## {entry.header_id}: {entry.title} (strength: {entry.strength})",
            f"- Want more: {entry.want_more}",
            f"- Want less: {entry.want_less}",
            f"- Unless: {entry.unless}",
            f'- {entry.wording_label}: "{entry.wording_text}"',
            f"- Evidence: {entry.evidence}",
        )
    )


def is_value_id(text: str) -> bool:
    return bool(_VALUE_ID.match(text))


def value_number(value_id: str) -> int:
    return int(value_id[1:])


def stamp_approved(evidence: str, day: date) -> str:
    """Append the approval date to an Evidence line, replacing any earlier date.

    An earlier stamp may sit anywhere in the line, since an agent refining a value
    often adds new evidence after the old one.
    """
    parts = [part.strip(" .") for part in _APPROVED_STAMP.sub("", evidence).split(";")]
    kept = [part for part in parts if part]
    return "; ".join([*kept, f"approved {day.isoformat()}"])


def same_text(first: str, second: str) -> bool:
    """Equal when case and spacing are ignored."""
    return " ".join(first.lower().split()) == " ".join(second.lower().split())
