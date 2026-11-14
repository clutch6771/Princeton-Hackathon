"""Shared pytest fixtures for the Judge backend tests."""

from __future__ import annotations

from pathlib import Path

import pytest

WATCHER_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = WATCHER_ROOT / "contracts" / "v1"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"


@pytest.fixture(scope="session")
def contracts_dir() -> Path:
    """Directory holding the exported JSON Schemas (contract version 1)."""
    return CONTRACTS_DIR


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Directory holding the synthetic, platform-neutral contract fixtures."""
    return FIXTURES_DIR
