"""Read-only access to the approved values in the shared memory Store.

Logical location: namespace ``(user_id, "memories")``, key ``values.md``.
The Judge never writes there. Missing, invalid, unapproved, deleted or
over-cap values yield an explicit ``ValuesUnavailable``; nothing is truncated
and nothing is invented.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from pydantic import ValidationError

from judge.contracts.values import (
    ApprovedValuesSnapshot,
    ValuesReadResult,
    ValuesStoreItem,
    ValuesUnavailable,
    ValuesUnavailableReason,
)
from judge.runtime.clock import Clock
from judge.values.markdown_parser import ValuesFormatError, parse_values_markdown

logger = logging.getLogger(__name__)

_APPROVED: Final = "approved"
_REVISION_HASH_CHARS: Final = 16


@dataclass(frozen=True, slots=True)
class StoredMemoryItem:
    """One raw Store item as read from the database."""

    value: Mapping[str, Any]
    updated_at: datetime | None = None


class MemoryItemSource(Protocol):
    """Fetch the raw ``values.md`` item for an owner. SELECT only."""

    async def fetch(self, owner_id: str) -> StoredMemoryItem | None:
        """Return the item, ``None`` when absent. Raises on store failure."""
        ...


class ValuesReader(Protocol):
    """Capability: read one immutable approved-values snapshot.

    Never raises for expected conditions; returns ``ValuesUnavailable``.
    Side effects: none (read-only, no TTL refresh).
    """

    async def read(self, owner_id: str) -> ValuesReadResult:
        """Return a pinned snapshot or the reason none is usable."""
        ...


def _unavailable(owner_id: str, reason: ValuesUnavailableReason) -> ValuesUnavailable:
    return ValuesUnavailable(owner_id=owner_id, reason=reason)


def _approval_problem(item: ValuesStoreItem, *, require_approval: bool) -> bool:
    if item.approval is None:
        return require_approval
    return item.approval.status.strip().lower() != _APPROVED


def build_snapshot(
    owner_id: str,
    stored: StoredMemoryItem | None,
    *,
    max_bytes: int,
    require_approval: bool,
    now: datetime,
    source: str,
) -> ValuesReadResult:
    """Validate a raw Store item and pin it as an immutable snapshot."""
    reasons = ValuesUnavailableReason
    if stored is None:
        return _unavailable(owner_id, reasons.MISSING)
    try:
        item = ValuesStoreItem.model_validate(stored.value)
    except ValidationError:
        return _unavailable(owner_id, reasons.INVALID)
    if item.lifecycle is not None and item.lifecycle.status == "deleted":
        return _unavailable(owner_id, reasons.DELETED)
    if _approval_problem(item, require_approval=require_approval):
        return _unavailable(owner_id, reasons.UNAPPROVED)
    content = item.canonical_content()
    if not content.strip():
        return _unavailable(owner_id, reasons.EMPTY)
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        return _unavailable(owner_id, reasons.OVER_CAP)
    digest = hashlib.sha256(encoded).hexdigest()
    if item.sha256 is not None and item.sha256.lower() != digest:
        return _unavailable(owner_id, reasons.HASH_MISMATCH)
    try:
        entries = parse_values_markdown(content)
    except ValuesFormatError:
        return _unavailable(owner_id, reasons.INVALID)
    snapshot = ApprovedValuesSnapshot(
        owner_id=owner_id,
        revision=(
            str(item.revision)
            if item.revision is not None
            else f"sha256:{digest[:_REVISION_HASH_CHARS]}"
        ),
        content_sha256=digest,
        content=content,
        entries=entries,
        approved_at=item.approval.approved_at if item.approval else None,
        loaded_at=now,
        source=source,
    )
    if not snapshot.active_entries:
        return _unavailable(owner_id, reasons.EMPTY)
    return snapshot


class StoreValuesReader:
    """Reads and validates ``values.md`` through a ``MemoryItemSource``."""

    def __init__(
        self,
        source: MemoryItemSource,
        *,
        max_bytes: int,
        require_approval: bool,
        clock: Clock,
        source_name: str = "memory_store",
    ) -> None:
        self._source = source
        self._max_bytes = max_bytes
        self._require_approval = require_approval
        self._clock = clock
        self._source_name = source_name

    async def read(self, owner_id: str) -> ValuesReadResult:
        try:
            stored = await self._source.fetch(owner_id)
        except Exception:
            # Fail closed: an unreachable store is "unavailable", never neutral.
            logger.exception("values store read failed")
            return _unavailable(owner_id, ValuesUnavailableReason.STORE_ERROR)
        return build_snapshot(
            owner_id,
            stored,
            max_bytes=self._max_bytes,
            require_approval=self._require_approval,
            now=self._clock.now(),
            source=self._source_name,
        )


class CachedValuesReader:
    """Short-lived cache so each reel does not cost a database read.

    Only usable snapshots are cached. ``invalidate`` is called by deletion
    hooks; a new revision is otherwise picked up after the TTL.
    """

    def __init__(
        self, inner: ValuesReader, *, ttl_seconds: float, clock: Clock
    ) -> None:
        self._inner = inner
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, ApprovedValuesSnapshot]] = {}

    async def read(self, owner_id: str) -> ValuesReadResult:
        cached = self._cache.get(owner_id)
        now = self._clock.monotonic()
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]
        result = await self._inner.read(owner_id)
        if isinstance(result, ApprovedValuesSnapshot):
            self._cache[owner_id] = (now, result)
        else:
            self._cache.pop(owner_id, None)
        return result

    def invalidate(self, owner_id: str) -> None:
        """Forget any cached snapshot for ``owner_id``."""
        self._cache.pop(owner_id, None)
