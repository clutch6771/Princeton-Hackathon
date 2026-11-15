"""Clear one user's data, so a demo starts clean.

    python -m guardian_router.reset_demo --user demo-user          # lists what would go
    python -m guardian_router.reset_demo --user demo-user --yes    # deletes it

For the named user only, it deletes:

- every Guardian record: approved values, proposals, inbox cards, notes, the chat
  log, review records, the Reflector's files and the chat threads;
- every Judge row: sessions and, through them, reels, thumbnails, feedback and
  embeddings.

Nothing can be recovered. The database is the one in DATABASE_URL (or
GUARDIAN_DATABASE_URL); `--database-env NAME` reads another variable. The connection
string is never taken from the command line or printed.

Stop the server first, or restart it afterwards: it keeps open sessions and what it
has shown each user in memory.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from guardian import chat_flow, config, storage

EXIT_DELETED = 0
EXIT_NOT_CONFIRMED = 1
EXIT_REFUSED = 2

STORE_TABLE = "store"
THREAD_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
JUDGE_SCHEMA = "judge"
JUDGE_SESSIONS = "sessions"  # deleting a session removes its reels and their rows
OWNER_COLUMN = "owner_id"
BAD_USER = "The user ID may contain only letters, digits, _ and -."

Target = tuple[str, sql.Composed, tuple[Any, ...]]  # label, WHERE clause, parameters


def _exists(connection: psycopg.Connection[Any], name: str) -> bool:
    row = connection.execute("SELECT to_regclass(%s) AS found", (name,)).fetchone()
    return row is not None and row["found"] is not None


def _judge_tables(connection: psycopg.Connection[Any]) -> list[str]:
    """Every Judge table that records an owner, sessions first."""
    rows = connection.execute(
        "SELECT table_name FROM information_schema.columns"
        " WHERE table_schema = %s AND column_name = %s ORDER BY table_name",
        (JUDGE_SCHEMA, OWNER_COLUMN),
    ).fetchall()
    names = [row["table_name"] for row in rows]
    first = [name for name in names if name == JUDGE_SESSIONS]
    return [*first, *(name for name in names if name != JUDGE_SESSIONS)]


def targets(connection: psycopg.Connection[Any], user_id: str) -> list[Target]:
    """What belongs to this user, as (label, table and condition, parameters).

    The user's Guardian records are the store rows whose namespace starts with the
    user ID and the chat threads named "<user>:<day>". The match is on the exact
    text, never a LIKE pattern, so "_" in an ID cannot match another user.
    """
    found: list[Target] = []
    if _exists(connection, f"public.{STORE_TABLE}"):
        clause = sql.SQL("{} WHERE prefix = %s OR starts_with(prefix, %s)").format(
            sql.Identifier("public", STORE_TABLE)
        )
        found.append((STORE_TABLE, clause, (user_id, f"{user_id}.")))
    for table in THREAD_TABLES:
        if _exists(connection, f"public.{table}"):
            clause = sql.SQL("{} WHERE starts_with(thread_id, %s)").format(
                sql.Identifier("public", table)
            )
            found.append((table, clause, (f"{user_id}:",)))
    for table in _judge_tables(connection):
        clause = sql.SQL("{} WHERE {} = %s").format(
            sql.Identifier(JUDGE_SCHEMA, table), sql.Identifier(OWNER_COLUMN)
        )
        found.append((f"{JUDGE_SCHEMA}.{table}", clause, (user_id,)))
    return found


def count_rows(connection: psycopg.Connection[Any], user_id: str) -> dict[str, int]:
    counts = {}
    for label, clause, parameters in targets(connection, user_id):
        query = sql.SQL("SELECT count(*) AS n FROM ") + clause
        row = connection.execute(query, parameters).fetchone()
        counts[label] = int(row["n"]) if row else 0
    return counts


def delete_rows(connection: psycopg.Connection[Any], user_id: str) -> None:
    for _, clause, parameters in targets(connection, user_id):
        connection.execute(sql.SQL("DELETE FROM ") + clause, parameters)


def reset(conn_string: str, user_id: str, confirmed: bool) -> dict[str, int]:
    """Count this user's rows and, if confirmed, delete them in one transaction."""
    with psycopg.connect(conn_string, row_factory=dict_row) as connection:
        counts = count_rows(connection, user_id)
        if confirmed:
            delete_rows(connection, user_id)
    return counts


def _conn_string(variable: str | None) -> str | None:
    config.load_env()
    if variable is None:
        return storage.database_url()
    return os.environ.get(variable, "").strip() or None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m guardian_router.reset_demo",
        description="Clear one user's Guardian records and Judge rows.",
    )
    parser.add_argument("--user", required=True, help="the user ID to clear")
    parser.add_argument(
        "--yes", action="store_true", help="delete; without it nothing is deleted"
    )
    parser.add_argument(
        "--database-env",
        metavar="NAME",
        help="read the connection string from this variable instead of DATABASE_URL",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    user_id = arguments.user
    if not chat_flow.is_valid_user_id(user_id):
        sys.stderr.write(f"{BAD_USER}\n")
        return EXIT_REFUSED
    variable = arguments.database_env
    conn_string = _conn_string(variable)
    if not conn_string:
        sys.stderr.write(f"{variable or storage.DATABASE_URL_VAR} is not set.\n")
        return EXIT_REFUSED
    try:
        counts = reset(conn_string, user_id, confirmed=arguments.yes)
    except psycopg.Error as error:
        detail = config.redact_secrets(f"{type(error).__name__}: {error}")
        sys.stderr.write(f"The reset failed and nothing was deleted: {detail}\n")
        return EXIT_REFUSED

    source = variable or "DATABASE_URL (or GUARDIAN_DATABASE_URL)"
    sys.stdout.write(f"User {user_id!r}, database from {source}:\n")
    for label, count in counts.items():
        sys.stdout.write(f"  {label}: {count} rows\n")
    if not arguments.yes:
        sys.stdout.write("Nothing was deleted. Add --yes to delete these rows.\n")
        return EXIT_NOT_CONFIRMED
    sys.stdout.write("Deleted. Restart the server before the next demo.\n")
    return EXIT_DELETED


if __name__ == "__main__":
    raise SystemExit(main())
