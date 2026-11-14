"""Read the ``values.md`` item from the shared LangGraph Postgres Store.

Logical location: namespace ``(user_id, "memories")``, key ``values.md``. The
Store table keeps the namespace as dot-joined text in ``prefix``.

Why a direct SELECT instead of ``AsyncPostgresStore.aget``: the library's read
statement contains a TTL-refresh ``UPDATE`` in a CTE, and PostgreSQL checks
the UPDATE privilege even when no row is refreshed. The Judge's runtime role
has SELECT only on this table, so it reads with one parameterized SELECT and
can never write or refresh anything. Setup, migrations and seeding of the
Store use the library with a separate owner role.
"""

from __future__ import annotations

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
    ) -> None:
        self._pool = pool
        self._query = sql.SQL(
            "SELECT value, updated_at FROM {} "
            "WHERE prefix = %s AND key = %s "
            "AND (expires_at IS NULL OR expires_at > now())"
        ).format(sql.Identifier(schema, "store"))

    async def fetch(self, owner_id: str) -> StoredMemoryItem | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                self._query, (memory_prefix(owner_id), VALUES_KEY)
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return StoredMemoryItem(value=row["value"], updated_at=row["updated_at"])
