"""Where the package keeps its state: in memory, or in Postgres.

Postgres is selected by a connection string in the environment (or the repository's
.env file): GUARDIAN_DATABASE_URL if set, otherwise DATABASE_URL, the variable the
Judge server uses. With neither, everything is kept in memory.

On Postgres the package uses LangGraph's `PostgresStore` (values, proposals, inbox,
review records, counters, chat log, notes and the Reflector's files) and
`PostgresSaver` (chat threads, including a run paused on a value card), plus
advisory locks. The connection string is a secret: it is never logged or returned.

Create the tables once with a role that may create them:

    python -m guardian.storage setup
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from guardian import config
from guardian.memory import locks

logger = logging.getLogger("guardian")

DATABASE_URL_VAR = "DATABASE_URL"
GUARDIAN_DATABASE_URL_VAR = "GUARDIAN_DATABASE_URL"
MIGRATION_DATABASE_URL_VAR = "MIGRATION_DATABASE_URL"
KIND_MEMORY = "memory"
KIND_POSTGRES = "postgres"
POOL_MIN_CONNECTIONS = 1
POOL_MAX_CONNECTIONS = 10  # a held lock keeps one connection out of the pool


@dataclass(frozen=True)
class Storage:
    kind: str
    store: BaseStore
    checkpointer: BaseCheckpointSaver
    locks: locks.Locks
    close: Callable[[], None]
    pool: Any = None  # the Postgres connection pool; None in memory


def database_url() -> str | None:
    """The connection string to use, or None when the package should stay in memory."""
    config.load_env()
    for name in (GUARDIAN_DATABASE_URL_VAR, DATABASE_URL_VAR):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def open_memory() -> Storage:
    return Storage(
        kind=KIND_MEMORY,
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        locks=locks.LocalLocks(),
        close=lambda: None,
    )


def open_postgres(conn_string: str, create_tables: bool = True) -> Storage:
    """Connect to Postgres. Creates the LangGraph tables if this role is allowed to."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.store.postgres import PostgresStore
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool: Any = ConnectionPool(
        conn_string,
        min_size=POOL_MIN_CONNECTIONS,
        max_size=POOL_MAX_CONNECTIONS,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=True,
    )
    store, checkpointer = PostgresStore(pool), PostgresSaver(pool)
    if create_tables:
        _create_tables(store, checkpointer)
    return Storage(
        kind=KIND_POSTGRES,
        store=store,
        checkpointer=checkpointer,
        locks=locks.PostgresLocks(pool),
        close=pool.close,
        pool=pool,
    )


def _create_tables(store: Any, checkpointer: Any) -> None:
    """Run LangGraph's own migrations. A role without CREATE rights skips this and
    relies on the tables having been created already."""
    from psycopg import errors

    try:
        store.setup()
        checkpointer.setup()
    except errors.InsufficientPrivilege:
        logger.warning(
            "this database role may not create tables; assuming they exist. "
            "If they do not, run `python -m guardian.storage setup` with an owner role."
        )


def open_from_env() -> Storage:
    """Postgres when a connection string is set, otherwise in memory."""
    conn_string = database_url()
    return open_postgres(conn_string) if conn_string else open_memory()


def main(argv: list[str] | None = None) -> int:
    """`python -m guardian.storage setup`: create the tables, using MIGRATION_DATABASE_URL
    if it is set (an owner role), otherwise the runtime connection string."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ["setup"]:
        sys.stderr.write("usage: python -m guardian.storage setup\n")
        return 2
    config.load_env()
    conn_string = (
        os.environ.get(MIGRATION_DATABASE_URL_VAR, "").strip() or database_url()
    )
    if not conn_string:
        sys.stderr.write(
            f"Set {DATABASE_URL_VAR} (or {MIGRATION_DATABASE_URL_VAR}) first.\n"
        )
        return 1
    opened = open_postgres(conn_string)
    opened.close()
    sys.stdout.write("Guardian tables are ready: store, checkpoints.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
