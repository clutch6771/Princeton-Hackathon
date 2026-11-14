#!/usr/bin/env bash
# Verification loop for the Judge backend (ECC verification-loop phases):
# lint, format, imports, types, security, contract drift, tests + coverage.
#
# Usage, from anywhere:  watcher_Agent/scripts/verify.sh
# Integration tests run when TEST_DATABASE_URL is set; otherwise they skip
# with an explicit reason. Live provider tests never run here.
set -uo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly BIN="$ROOT/.venv/bin"
readonly PKG="watcher_Agent"
FAILED=()

step() {
  local name="$1"; shift
  echo "== $name"
  if ! "$@"; then FAILED+=("$name"); echo "-- FAILED: $name"; fi
}

cd "$ROOT" || exit 1

step "ruff"     "$BIN/ruff" check "$PKG"
step "black"    "$BIN/black" --check --quiet "$PKG"
step "isort"    "$BIN/isort" --check-only --quiet "$PKG"
step "mypy"     "$BIN/mypy" "$PKG/judge" "$PKG/tests"
step "mypy-scripts" "$BIN/mypy" "$PKG/scripts" "$PKG/migrations"
step "bandit"   "$BIN/bandit" -q -c pyproject.toml -r "$PKG/judge" "$PKG/scripts" "$PKG/migrations"
step "schemas"  "$BIN/python" "$PKG/scripts/export_contract_schemas.py" --check
step "pytest"   "$BIN/pytest" "$PKG/tests" -m "not live" -q \
                --cov=judge --cov-report=term-missing

if (( ${#FAILED[@]} > 0 )); then
  echo "VERIFY FAILED: ${FAILED[*]}"
  exit 1
fi
echo "VERIFY OK"
