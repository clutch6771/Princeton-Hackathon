"""Write (or check) the contract schemas and synthetic fixtures.

Output: ``watcher_Agent/contracts/v1/*.schema.json`` and
``watcher_Agent/contracts/v1/fixtures/*``. Both are generated from the typed
models so they cannot drift from the code.

Usage, from the repository root:
    .venv/bin/python watcher_Agent/scripts/export_contract_schemas.py
    .venv/bin/python watcher_Agent/scripts/export_contract_schemas.py --check
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

WATCHER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WATCHER_ROOT))

from judge.contracts.examples import (  # noqa: E402
    EXAMPLE_VALUES_MARKDOWN,
    build_examples,
)
from judge.contracts.schema_export import build_schemas  # noqa: E402

logger = logging.getLogger("export_contract_schemas")
OUT_DIR = WATCHER_ROOT / "contracts" / "v1"
FIXTURES = "fixtures"
VALUES_FIXTURE = "approved_values.md"


def _render(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def expected_files() -> dict[str, str]:
    """Return ``{path relative to contracts/v1: file content}``."""
    files = {name: _render(schema) for name, schema in build_schemas().items()}
    for name, example in build_examples().items():
        files[f"{FIXTURES}/{name}"] = _render(example.model_dump(mode="json"))
    files[f"{FIXTURES}/{VALUES_FIXTURE}"] = EXAMPLE_VALUES_MARKDOWN
    return files


def _committed_files() -> dict[str, str]:
    paths = [*OUT_DIR.glob("*.schema.json"), *(OUT_DIR / FIXTURES).glob("*")]
    return {
        path.relative_to(OUT_DIR).as_posix(): path.read_text(encoding="utf-8")
        for path in paths
        if path.is_file()
    }


def check() -> int:
    """Return 1 when a committed file is missing, stale or unexpected."""
    expected = expected_files()
    committed = _committed_files()
    drifted = sorted(
        name
        for name in expected.keys() | committed.keys()
        if expected.get(name) != committed.get(name)
    )
    for name in drifted:
        logger.error("contract drift: %s", name)
    return 1 if drifted else 0


def write() -> int:
    """Write every generated file; report files that no longer belong."""
    expected = expected_files()
    for name, content in expected.items():
        path = OUT_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    logger.info("wrote %d contract files to %s", len(expected), OUT_DIR)
    for name in sorted(_committed_files().keys() - expected.keys()):
        logger.warning("unexpected file (remove it): %s", name)
    return 0


def main() -> int:
    """Entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on drift")
    args = parser.parse_args()
    return check() if args.check else write()


if __name__ == "__main__":
    raise SystemExit(main())
