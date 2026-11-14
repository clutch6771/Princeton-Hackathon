"""Seed SYNTHETIC approved values into the shared memory Store (development).

Run with the Store owner role, never with the Judge runtime role (which has
SELECT only). This stands in for the approval owner (the Guardian) during
development. The values written here are the invented sample in
``judge.contracts.examples``; never seed real user values with this script.

Usage, from the repository root:
    MIGRATION_DATABASE_URL=... DEMO_USER_ID=demo-user \
        .venv/bin/python watcher_Agent/scripts/seed_dev_values.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.store.postgres import PostgresStore  # noqa: E402

from judge.contracts.examples import example_values_store_item  # noqa: E402
from judge.values.markdown_parser import parse_values_markdown  # noqa: E402

logger = logging.getLogger("seed_dev_values")


def main() -> int:
    """Write the synthetic ``values.md`` item for the demo user."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    owner_id = os.environ.get("DEMO_USER_ID", "").strip()
    if not url or not owner_id:
        logger.error("set MIGRATION_DATABASE_URL (owner role) and DEMO_USER_ID")
        return 1
    with PostgresStore.from_conn_string(url) as store:
        store.setup()
        content = example_values_store_item().canonical_content()
        for entry in parse_values_markdown(content):
            store.put(
                (owner_id, "guardian", "values"),
                entry.value_id,
                {"text": entry.raw_markdown, "updated": "2026-11-14T12:00:00+00:00"},
            )
    logger.info("seeded synthetic approved values (Guardian layout) for the demo user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
