"""Parse the approved-values Markdown into entries, keeping wording verbatim.

Expected shape of one entry (PRD)::

    ## V3: Fitness without body comparison (strength: high)
    - Want more: form tutorials, training plans, recovery
    - Want less: before/after transformations, physique rating
    - Unless: none
    - In my words: "gym stuff is great, comparing bodies is not"
    - Evidence: disagreed with obs_112; approved 2026-11-14

Optional lifecycle bullets written by the memory owner::

    - Approved: 2026-11-14T09:05:00Z
    - Status: superseded by V7        (or: deleted, active)

The parser never rewrites or shortens a clause. The text here is data: no
instruction inside it is ever followed.
"""

from __future__ import annotations

import re
from typing import Final

from judge.contracts.values import ValueEntry, ValueStatus


class ValuesFormatError(ValueError):
    """The values document does not follow the agreed structure."""


_ENTRY_HEADING: Final = re.compile(r"^##(?!#)\s*(?P<rest>.*)$")
_HEADING_BODY: Final = re.compile(
    r"^(?P<id>[A-Za-z][A-Za-z0-9_-]{0,31})\s*:\s*(?P<title>.+?)"
    r"(?:\s*\(\s*strength\s*:\s*(?P<strength>[^)]+?)\s*\))?\s*$"
)
_BULLET: Final = re.compile(r"^\s*[-*]\s*(?P<key>[^:]{1,40}):\s*(?P<value>.*)$")
_APPROVED_IN_TEXT: Final = re.compile(r"approved\s+(?P<when>\d{4}-\d{2}-\d{2}\S*)")
_SUPERSEDED: Final = re.compile(
    r"^superseded(?:\s+by\s+(?P<by>[A-Za-z][A-Za-z0-9_-]*))?", re.IGNORECASE
)
_FIELD_BY_KEY: Final = {
    "want more": "want_more",
    "want less": "want_less",
    "unless": "unless",
    "in my words": "in_my_words",
    "evidence": "evidence",
    "approved": "approved_at",
    "status": "status_text",
}


def _split_blocks(content: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    for line in content.splitlines():
        if _ENTRY_HEADING.match(line):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return blocks


def _collect_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    for line in lines:
        bullet = _BULLET.match(line)
        if bullet:
            current = _FIELD_BY_KEY.get(bullet.group("key").strip().lower())
            if current is not None:
                fields[current] = bullet.group("value").strip()
        elif current is not None and line.strip() and line[:1].isspace():
            fields[current] = f"{fields[current]} {line.strip()}".strip()
        elif not line.strip():
            current = None
    return fields


def _lifecycle(status_text: str | None) -> tuple[ValueStatus, str | None]:
    text = (status_text or "").strip()
    superseded = _SUPERSEDED.match(text)
    if superseded:
        return ValueStatus.SUPERSEDED, superseded.group("by")
    if text.lower().startswith("deleted"):
        return ValueStatus.DELETED, None
    return ValueStatus.ACTIVE, None


def _approved_at(fields: dict[str, str]) -> str | None:
    explicit = fields.get("approved_at")
    if explicit:
        return explicit
    found = _APPROVED_IN_TEXT.search(fields.get("evidence", ""))
    return found.group("when").rstrip(";,.") if found else None


def _parse_block(lines: list[str]) -> ValueEntry:
    heading = _ENTRY_HEADING.match(lines[0])
    body = _HEADING_BODY.match(heading.group("rest")) if heading else None
    if body is None:
        msg = "a value heading must look like '## V1: Title (strength: high)'"
        raise ValuesFormatError(msg)
    fields = _collect_fields(lines[1:])
    status, superseded_by = _lifecycle(fields.get("status_text"))
    return ValueEntry(
        value_id=body.group("id"),
        title=body.group("title").strip(),
        strength=body.group("strength"),
        want_more=fields.get("want_more"),
        want_less=fields.get("want_less"),
        unless=fields.get("unless"),
        in_my_words=fields.get("in_my_words"),
        evidence=fields.get("evidence"),
        approved_at=_approved_at(fields),
        status=status,
        superseded_by=superseded_by,
        raw_markdown="\n".join(lines).rstrip(),
    )


def parse_values_markdown(content: str) -> tuple[ValueEntry, ...]:
    """Parse every ``##`` entry. Raises ``ValuesFormatError`` when malformed."""
    entries = tuple(_parse_block(block) for block in _split_blocks(content))
    seen: set[str] = set()
    for entry in entries:
        if entry.value_id in seen:
            msg = "duplicate value id in the values document"
            raise ValuesFormatError(msg)
        seen.add(entry.value_id)
    return entries
