#!/usr/bin/env bash
# Fast inner-loop check: auto-format, lint, type-check, then run tests.
# Usage: watcher_Agent/scripts/dev_check.sh [pytest args...]
# (verify.sh is the full, non-mutating gate; this one rewrites files.)
set -uo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly BIN="$ROOT/.venv/bin"
readonly PKG="watcher_Agent"
cd "$ROOT" || exit 1

"$BIN/ruff" check --fix --quiet "$PKG"
"$BIN/isort" --quiet "$PKG"
"$BIN/black" --quiet "$PKG"
"$BIN/ruff" check --quiet "$PKG" || echo "-- ruff findings above"
"$BIN/mypy" "$PKG/judge" "$PKG/tests" | tail -15
if (( $# > 0 )); then
  "$BIN/pytest" -q -p no:cacheprovider "$@" | tail -25
else
  "$BIN/pytest" -q -p no:cacheprovider "$PKG/tests/unit" | tail -25
fi
