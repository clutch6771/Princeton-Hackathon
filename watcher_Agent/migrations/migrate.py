"""Migration CLI for the Judge schema (run with the migration/owner role).

The connection string is read from MIGRATION_DATABASE_URL, falling back to
DATABASE_URL. It is never printed.

Usage, from the repository root:
    .venv/bin/python watcher_Agent/migrations/migrate.py status
    .venv/bin/python watcher_Agent/migrations/migrate.py up [--without-vector]
    .venv/bin/python watcher_Agent/migrations/migrate.py down --to 0
    .venv/bin/python watcher_Agent/migrations/migrate.py setup-store
    .venv/bin/python watcher_Agent/migrations/migrate.py grant --runtime-role NAME
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from judge.persistence import migrator  # noqa: E402

logger = logging.getLogger("migrate")


def _conninfo() -> str:
    url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        msg = "set MIGRATION_DATABASE_URL (or DATABASE_URL) to the owner-role URL"
        raise SystemExit(msg)
    return url


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show applied, skipped and pending versions")
    up = commands.add_parser("up", help="apply pending migrations")
    up.add_argument(
        "--without-vector",
        action="store_true",
        help="skip 0004 when pgvector is unavailable (disable embedding indexing)",
    )
    down = commands.add_parser("down", help="roll back above a version")
    down.add_argument("--to", type=int, required=True, help="version to keep (0=all)")
    commands.add_parser("setup-store", help="create the shared memory Store tables")
    grant = commands.add_parser("grant", help="grant limited runtime privileges")
    grant.add_argument("--runtime-role", required=True)
    grant.add_argument("--store-schema", default="public")
    return parser


def main() -> int:
    """Entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser().parse_args()
    conninfo = _conninfo()
    try:
        if args.command == "status":
            for version, name, state in migrator.status(conninfo):
                logger.info("%04d  %-22s %s", version, name, state)
        elif args.command == "up":
            applied = migrator.apply(conninfo, without_vector=args.without_vector)
            logger.info("applied: %s", applied or "nothing pending")
        elif args.command == "down":
            logger.info(
                "rolled back: %s", migrator.rollback(conninfo, to_version=args.to)
            )
        elif args.command == "setup-store":
            migrator.setup_memory_store(conninfo)
        elif args.command == "grant":
            migrator.grant_runtime_role(
                conninfo, args.runtime_role, store_schema=args.store_schema
            )
    except migrator.MigrationError as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
