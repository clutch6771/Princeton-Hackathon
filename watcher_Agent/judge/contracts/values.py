"""Approved values: the Judge's only semantic memory, read-only.

The default source is Guardian's protected per-value records.
``ValuesStoreItem`` normalizes them in memory and also supports the explicit
legacy document layout in ``(user_id, "memories")``, key ``values.md``.
``ApprovedValuesSnapshot`` is the
immutable, parsed view pinned to each judgment.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from judge.contracts.base import CONTRACT_VERSION, Contract, ContractVersion
from judge.errors import ReelErrorCode

MEMORY_NAMESPACE_SUFFIX = "memories"
VALUES_KEY = "values.md"


class ValueStatus(StrEnum):
    """Lifecycle of one approved value entry."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class ValueEntry(Contract):
    """One approved value, keeping the user's own wording verbatim."""

    value_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    title: str = Field(min_length=1)
    strength: str | None = None
    want_more: str | None = None
    want_less: str | None = None
    unless: str | None = Field(
        default=None, description="Exception clause. Never truncated."
    )
    in_my_words: str | None = None
    evidence: str | None = None
    approved_at: str | None = None
    status: ValueStatus = ValueStatus.ACTIVE
    superseded_by: str | None = None
    raw_markdown: str = Field(description="The entry exactly as approved.")


class ApprovedValuesSnapshot(Contract):
    """Immutable snapshot of the approved values used for one judgment."""

    contract_version: ContractVersion = CONTRACT_VERSION
    owner_id: str
    revision: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str
    entries: tuple[ValueEntry, ...]
    approved_at: datetime | None = None
    loaded_at: datetime
    source: str

    @property
    def active_entries(self) -> tuple[ValueEntry, ...]:
        """Entries that are neither superseded nor deleted."""
        return tuple(e for e in self.entries if e.status is ValueStatus.ACTIVE)

    @property
    def active_value_ids(self) -> frozenset[str]:
        """IDs a judgment is allowed to reference."""
        return frozenset(e.value_id for e in self.active_entries)


class ValuesUnavailableReason(StrEnum):
    """Why no approved values could be used. Never treated as neutral."""

    MISSING = "missing"
    EMPTY = "empty"
    INVALID = "invalid"
    HASH_MISMATCH = "hash_mismatch"
    UNAPPROVED = "unapproved"
    DELETED = "deleted"
    OVER_CAP = "over_cap"
    STORE_ERROR = "store_error"


_REEL_CODE_BY_REASON: dict[ValuesUnavailableReason, ReelErrorCode] = {
    ValuesUnavailableReason.MISSING: ReelErrorCode.VALUES_MISSING,
    ValuesUnavailableReason.EMPTY: ReelErrorCode.VALUES_EMPTY,
    ValuesUnavailableReason.INVALID: ReelErrorCode.VALUES_INVALID,
    ValuesUnavailableReason.HASH_MISMATCH: ReelErrorCode.VALUES_HASH_MISMATCH,
    ValuesUnavailableReason.UNAPPROVED: ReelErrorCode.VALUES_UNAPPROVED,
    ValuesUnavailableReason.DELETED: ReelErrorCode.VALUES_DELETED,
    ValuesUnavailableReason.OVER_CAP: ReelErrorCode.VALUES_OVER_CAP,
    ValuesUnavailableReason.STORE_ERROR: ReelErrorCode.VALUES_STORE_ERROR,
}


class ValuesUnavailable(Contract):
    """Explicit 'no usable approved values' result."""

    owner_id: str
    reason: ValuesUnavailableReason

    @property
    def reel_error_code(self) -> ReelErrorCode:
        """Sanitized code stored on reels that could not be judged."""
        return _REEL_CODE_BY_REASON[self.reason]


ValuesReadResult = ApprovedValuesSnapshot | ValuesUnavailable


class _StoreModel(BaseModel):
    """Tolerant of extra keys the memory owner's tooling may add."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class ValuesApprovalMetadata(_StoreModel):
    """Approval evidence written by the approval owner (never by the Judge)."""

    status: str
    approved_at: datetime | None = None


class ValuesLifecycleMetadata(_StoreModel):
    """Document-level lifecycle written by the memory owner."""

    status: Literal["active", "deleted"] = "active"
    superseded_by: str | None = None


class ValuesStoreItem(_StoreModel):
    """Payload of the ``values.md`` Store item.

    ``content`` is the canonical Markdown, either one string or a list of
    lines (the form file-style Store backends write). ``sha256`` is the hex
    SHA-256 of the canonical content encoded as UTF-8.
    """

    content: str | tuple[str, ...]
    revision: int | str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    approval: ValuesApprovalMetadata | None = None
    lifecycle: ValuesLifecycleMetadata | None = None

    def canonical_content(self) -> str:
        """The Markdown text with lines joined by a single newline."""
        if isinstance(self.content, str):
            return self.content
        return "\n".join(self.content)
