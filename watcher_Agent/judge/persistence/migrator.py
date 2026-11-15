"""Plain-SQL migration runner for the Judge schema on PostgreSQL.

Migrations are ``NNNN_name.up.sql`` / ``NNNN_name.down.sql`` files in
``watcher_Agent/migrations``. Applied versions and checksums are recorded in
``public.judge_schema_migrations``. Run migrations with a dedicated owner
role; the runtime role gets only the privileges granted by
``grant_runtime_role``.

pgvector: migration 0004 creates the vector table and needs the ``vector``
extension. It fails with a clear message when the extension is unavailable,
unless ``without_vector=True`` is passed explicitly, in which case it is
recorded as skipped and embedding indexing must be disabled.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)

MIGRATIONS_DIR: Final = Path(__file__).resolve().parents[2] / "migrations"
VECTOR_MIGRATION_VERSION: Final = 4
_FILE_PATTERN: Final = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.up\.sql$")
_ADVISORY_LOCK_KEY: Final = 7_215_043_001
_LEDGER_DDL: Final = """
CREATE TABLE IF NOT EXISTS public.judge_schema_migrations (
    version    integer PRIMARY KEY,
    name       text NOT NULL,
    checksum   text NOT NULL,
    skipped    boolean NOT NULL DEFAULT false,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""
_RUNTIME_TABLE_PRIVILEGES: Final = "SELECT, INSERT, UPDATE, DELETE"


class MigrationError(RuntimeError):
    """A migration could not be applied safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One versioned schema change."""

    version: int
    name: str
    up_sql: str
    down_sql: str
    checksum: str


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Load every migration file pair, ordered by version."""
    migrations: list[Migration] = []
    for up_path in sorted(directory.glob("*.up.sql")):
        match = _FILE_PATTERN.match(up_path.name)
        if match is None:
            msg = f"unexpected migration file name: {up_path.name}"
            raise MigrationError(msg)
        down_path = up_path.with_name(up_path.name.replace(".up.sql", ".down.sql"))
        if not down_path.exists():
            msg = f"missing rollback file for {up_path.name}"
            raise MigrationError(msg)
        up_sql = up_path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                up_sql=up_sql,
                down_sql=down_path.read_text(encoding="utf-8"),
                checksum=hashlib.sha256(up_sql.encode("utf-8")).hexdigest(),
            )
        )
    return migrations


def vector_extension_available(conninfo: str) -> bool:
    """Whether the ``vector`` extension can be created on this server."""
    query = "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
    with psycopg.connect(conninfo, autocommit=True) as connection:
        return connection.execute(query).fetchone() is not None


def _ledger(connection: psycopg.Connection) -> dict[int, tuple[str, bool]]:
    exists = connection.execute(
        "SELECT to_regclass('public.judge_schema_migrations')"
    ).fetchone()
    if exists is None or exists[0] is None:
        return {}
    rows = connection.execute(
        "SELECT version, checksum, skipped FROM public.judge_schema_migrations"
    ).fetchall()
    return {version: (checksum, skipped) for version, checksum, skipped in rows}


def _record(
    connection: psycopg.Connection, migration: Migration, skipped: bool
) -> None:
    connection.execute(
        """
        INSERT INTO public.judge_schema_migrations (version, name, checksum, skipped)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (version) DO UPDATE
        SET checksum = EXCLUDED.checksum, skipped = EXCLUDED.skipped,
            applied_at = now()
        """,
        (migration.version, migration.name, migration.checksum, skipped),
    )


def _check_vector(connection: psycopg.Connection) -> None:
    available = connection.execute(
        "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
    ).fetchone()
    if available is None:
        msg = (
            "pgvector is not available on this PostgreSQL service, so migration "
            "0004 (embedding vectors) cannot be applied. Enable the 'vector' "
            "extension on the service, or re-run with --without-vector and set "
            "EMBEDDING_INDEXING_ENABLED=false."
        )
        raise MigrationError(msg)


def apply(conninfo: str, *, without_vector: bool = False) -> list[int]:
    """Apply every pending migration in order. Returns applied versions."""
    applied: list[int] = []
    with psycopg.connect(conninfo) as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        connection.execute(_LEDGER_DDL)
        connection.commit()
        ledger = _ledger(connection)
        for migration in discover():
            recorded = ledger.get(migration.version)
            if recorded is not None and recorded[0] != migration.checksum:
                msg = f"migration {migration.version:04d} changed after it was applied"
                raise MigrationError(msg)
            if recorded is not None and not recorded[1]:
                continue
            is_vector = migration.version == VECTOR_MIGRATION_VERSION
            if is_vector and without_vector:
                _record(connection, migration, skipped=True)
                connection.commit()
                continue
            if is_vector:
                _check_vector(connection)
            connection.execute(migration.up_sql)
            _record(connection, migration, skipped=False)
            connection.commit()
            applied.append(migration.version)
            logger.info("applied migration %04d_%s", migration.version, migration.name)
    return applied


def rollback(conninfo: str, *, to_version: int) -> list[int]:
    """Roll back every applied migration above ``to_version``."""
    reverted: list[int] = []
    by_version = {m.version: m for m in discover()}
    with psycopg.connect(conninfo) as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        ledger = _ledger(connection)
        for version in sorted(ledger, reverse=True):
            if version <= to_version:
                continue
            if not ledger[version][1]:
                connection.execute(by_version[version].down_sql)
            connection.execute(
                "DELETE FROM public.judge_schema_migrations WHERE version = %s",
                (version,),
            )
            connection.commit()
            reverted.append(version)
            logger.info("rolled back migration %04d", version)
        if to_version <= 0:
            connection.execute("DROP TABLE IF EXISTS public.judge_schema_migrations")
            connection.commit()
    return reverted


def status(conninfo: str) -> list[tuple[int, str, str]]:
    """Return ``(version, name, state)`` for every known migration."""
    with psycopg.connect(conninfo) as connection:
        ledger = _ledger(connection)
    report: list[tuple[int, str, str]] = []
    for migration in discover():
        recorded = ledger.get(migration.version)
        state = "pending"
        if recorded is not None:
            state = "skipped" if recorded[1] else "applied"
        report.append((migration.version, migration.name, state))
    return report


def grant_runtime_role(
    conninfo: str, role: str, *, store_schema: str = "public"
) -> None:
    """Grant the limited runtime privileges to ``role``.

    The runtime role may read and write the ``judge`` tables but may only
    SELECT the shared memory Store: the Judge can never write approved values.
    """
    role_id = sql.Identifier(role)
    store = sql.Identifier(store_schema, "store")
    privileges = sql.SQL(_RUNTIME_TABLE_PRIVILEGES)
    statements = [
        sql.SQL("GRANT USAGE ON SCHEMA judge TO {}").format(role_id),
        sql.SQL("GRANT {} ON ALL TABLES IN SCHEMA judge TO {}").format(
            privileges, role_id
        ),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA judge GRANT {} ON TABLES TO {}"
        ).format(privileges, role_id),
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(store_schema), role_id
        ),
    ]
    with psycopg.connect(conninfo) as connection:
        for statement in statements:
            connection.execute(statement)
        has_store = connection.execute(
            "SELECT to_regclass(%s)", (f"{store_schema}.store",)
        ).fetchone()
        if has_store is not None and has_store[0] is not None:
            connection.execute(
                sql.SQL("REVOKE ALL ON {} FROM {}").format(store, role_id)
            )
            connection.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(store, role_id)
            )
        connection.commit()
    logger.info("granted runtime privileges")


def setup_memory_store(conninfo: str) -> None:
    """Create the shared LangGraph memory Store tables (owner role only).

    The Judge runtime never calls this. It is the PostgreSQL replacement for
    MongoDBStore setup and belongs to the memory owner's deployment step.
    """
    from langgraph.store.postgres import PostgresStore

    with PostgresStore.from_conn_string(conninfo) as store:
        store.setup()
    logger.info("memory store tables are ready")
