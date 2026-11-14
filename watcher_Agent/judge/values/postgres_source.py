"""Read the ``values.md`` item from the shared LangGraph Postgres Store.

Guardian stores each approved entry in ``(user_id, "guardian", "values")``.
Only its protected approval path writes this namespace. The Judge renders
these entries without importing or modifying Guardian. The old document
layout remains an explicit compatibility option; there is no stale fallback.

Why a direct SELECT instead of ``AsyncPostgresStore.aget``: the library's read
statement contains a TTL-refresh ``UPDATE`` in a CTE, and PostgreSQL checks
the UPDATE privilege even when no row is refreshed. The Judge's runtime role
has SELECT only on this table, so it reads with one parameterized SELECT and
can never write or refresh anything. Setup, migrations and seeding of the
Store use the library with a separate owner role.
"""

from __future__ import annotations

import re
from typing import Any

from psycopg import AsyncConnection, sql
from psycopg_pool import AsyncConnectionPool

from judge.contracts.values import MEMORY_NAMESPACE_SUFFIX, VALUES_KEY
from judge.values.reader import StoredMemoryItem


def memory_prefix(owner_id: str) -> str:
    """The Store ``prefix`` for an owner's memory namespace."""
    return f"{owner_id}.{MEMORY_NAMESPACE_SUFFIX}"


class PostgresMemoryItemSource:
    """SELECT-only reader of the approved-values Store item."""

    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]],
        *,
        schema: str = "public",
        layout: str = "guardian",
    ) -> None:
        self._pool = pool
        self._layout = layout
        self._query = sql.SQL(
            "SELECT key, value, updated_at FROM {} "
            "WHERE prefix = %s "
            "AND (expires_at IS NULL OR expires_at > now())"
        ).format(sql.Identifier(schema, "store"))

    async def fetch(self, owner_id: str) -> StoredMemoryItem | None:
        async with self._pool.connection() as connection:
            prefix = (
                f"{owner_id}.guardian.values"
                if self._layout == "guardian"
                else memory_prefix(owner_id)
            )
            cursor = await connection.execute(self._query, (prefix,))
            rows = await cursor.fetchall()
        if self._layout != "guardian":
            row = next((r for r in rows if r["key"] == VALUES_KEY), None)
            return (
                StoredMemoryItem(value=row["value"], updated_at=row["updated_at"])
                if row
                else None
            )
        if not rows:
            return None
        if any(
            re.fullmatch(r"V[1-9][0-9]*", r["key"]) is None
            or not isinstance(r["value"].get("text"), str)
            or not r["value"]["text"].startswith(f"## {r['key']}:")
            for r in rows
        ):
            return StoredMemoryItem(value={})  # fail closed on malformed records
        rows.sort(key=lambda r: int(r["key"][1:]))
        return StoredMemoryItem(
            value={
                "content": "\n\n".join(r["value"]["text"] for r in rows),
                "approval": {"status": "approved"},
            },
            updated_at=max(r["updated_at"] for r in rows),
        )
