"""PostgreSQL (Tiger Data) storage: pool, transactions, health, TLS rules.

Tiger Data is plain PostgreSQL, so this adapter uses standard tables and the
psycopg 3 driver. Hypertables and pgvectorscale are optional and unused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from judge.config import Settings
from judge.contracts.storage import StorageHealth
from judge.errors import ConfigurationError, StorageUnavailableError
from judge.persistence.pg_aux import (
    PgEmbeddings,
    PgFeedback,
)
from judge.persistence.pg_core import (
    Conn,
    PgReceipts,
    PgReels,
    PgSessions,
    PgThumbnails,
    fetch_one,
)

_TLS_MODES: Final = frozenset({"require", "verify-ca", "verify-full"})
_DEFAULT_STATEMENT_TIMEOUT_MS: Final = 5000
_VECTOR_TABLE: Final = "judge.reel_embedding_vectors"


def resolve_conninfo(
    database_url: str, *, production: bool, statement_timeout_ms: int
) -> str:
    """Build the connection string, enforcing TLS outside development.

    In production a missing ``sslmode`` becomes ``require`` and a weaker
    explicit mode is refused. The value is never logged.
    """
    params: dict[str, Any] = dict(conninfo_to_dict(database_url))
    mode = params.get("sslmode")
    if production:
        if mode is None:
            params["sslmode"] = "require"
        elif mode not in _TLS_MODES:
            msg = (
                "DATABASE_URL must use TLS in production "
                "(sslmode=require, verify-ca or verify-full)"
            )
            raise ConfigurationError(msg)
    timeout = f"-c statement_timeout={statement_timeout_ms}"
    params["options"] = f"{params.get('options', '')} {timeout}".strip()
    return make_conninfo(**params)


class PgTx:
    """Repositories bound to one open PostgreSQL transaction."""

    def __init__(self, conn: Conn, *, vector_enabled: bool) -> None:
        self.sessions = PgSessions(conn)
        self.receipts = PgReceipts(conn)
        self.reels = PgReels(conn)
        self.thumbnails = PgThumbnails(conn)
        self.embeddings = PgEmbeddings(conn, vector_enabled=vector_enabled)
        self.feedback = PgFeedback(conn)


class PostgresStorage:
    """The persistent application database."""

    def __init__(
        self, pool: AsyncConnectionPool[Conn], *, vector_enabled: bool
    ) -> None:
        self._pool = pool
        self.vector_enabled = vector_enabled

    @property
    def pool(self) -> AsyncConnectionPool[Conn]:
        """The connection pool (shared with the values reader)."""
        return self._pool

    @classmethod
    async def connect(
        cls,
        conninfo: str,
        *,
        vector_enabled: bool | None = None,
        min_size: int = 1,
        max_size: int = 4,
    ) -> PostgresStorage:
        """Open a pool. ``vector_enabled=None`` detects the vector table."""
        pool: AsyncConnectionPool[Conn] = AsyncConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )
        try:
            await pool.open(wait=True, timeout=10.0)
        except (PoolTimeout, psycopg.OperationalError) as exc:
            await pool.close()
            msg = "the database could not be reached"
            raise StorageUnavailableError(msg) from exc
        storage = cls(pool, vector_enabled=bool(vector_enabled))
        if vector_enabled is None:
            storage.vector_enabled = await storage._has_vector_table()
        return storage

    @classmethod
    async def from_settings(cls, settings: Settings) -> PostgresStorage:
        """Open the pool described by the runtime settings."""
        if settings.database_url is None:
            msg = "DATABASE_URL is required when STORAGE_MODE=postgres"
            raise ConfigurationError(msg)
        conninfo = resolve_conninfo(
            settings.database_url.get_secret_value(),
            production=settings.is_production,
            statement_timeout_ms=settings.db_statement_timeout_ms,
        )
        return await cls.connect(
            conninfo,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
        )

    async def _has_vector_table(self) -> bool:
        async with self._pool.connection() as conn:
            row = await fetch_one(
                conn, "SELECT to_regclass(%s) AS oid", (_VECTOR_TABLE,)
            )
        return row is not None and row["oid"] is not None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[PgTx]:
        try:
            async with self._pool.connection() as conn, conn.transaction():
                yield PgTx(conn, vector_enabled=self.vector_enabled)
        except (PoolTimeout, psycopg.OperationalError) as exc:
            msg = "the database could not be reached"
            raise StorageUnavailableError(msg) from exc

    async def health(self) -> StorageHealth:
        try:
            async with self._pool.connection() as conn:
                await fetch_one(conn, "SELECT 1 AS ok")
                extension = await fetch_one(
                    conn, "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                )
                column = await fetch_one(
                    conn,
                    """
                    SELECT atttypmod FROM pg_attribute
                    WHERE attrelid = to_regclass(%s) AND attname = 'embedding'
                    """,
                    (_VECTOR_TABLE,),
                )
        except (PoolTimeout, psycopg.Error):
            return StorageHealth(ok=False, backend="postgres", detail="unreachable")
        return StorageHealth(
            ok=True,
            backend="postgres",
            vector_available=extension is not None and column is not None,
            vector_version=extension["extversion"] if extension else None,
            vector_dimension=column["atttypmod"] if column else None,
        )

    async def close(self) -> None:
        await self._pool.close()
