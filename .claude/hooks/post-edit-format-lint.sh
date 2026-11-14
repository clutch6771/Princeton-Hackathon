#!/usr/bin/env bash
# PostToolUse hook: format and lint the file Claude just edited.
# Implements ECC python/hooks.md and swift/hooks.md for this project.
# Exits 2 with findings on stderr so Claude sees and fixes them; exits 0 when clean.
set -uo pipefail

readonly ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
FINDINGS=""

add_finding() { FINDINGS+="$1"$'\n'; }

# Prefer the project venv, fall back to PATH.
find_tool() {
  local venv_bin="$ROOT/.venv/bin/$1"
  if [[ -x "$venv_bin" ]]; then echo "$venv_bin"; else command -v "$1" || true; fi
}

# Run a tool if installed; record its output as a finding when it fails.
run_check() {
  local name="$1"; shift
  local bin; bin="$(find_tool "$name")"
  if [[ -z "$bin" ]]; then
    add_finding "[hook] $name not installed (see requirements-dev.txt / CLAUDE.md Tooling)"
    return
  fi
  local out
  if ! out="$("$bin" "$@" 2>&1)"; then add_finding "[$name] $out"; fi
}

warn_print_statements() {
  local file="$1" advice="$2" hits
  hits="$(grep -nE '^[[:space:]]*print\(' "$file" || true)"
  if [[ -n "$hits" ]]; then add_finding "[print] use $advice instead of print():"$'\n'"$hits"; fi
}

handle_python() {
  local file="$1"
  run_check ruff check --fix --quiet "$file"
  run_check isort --quiet "$file"
  run_check black --quiet "$file"
  run_check mypy --no-error-summary "$file"
  warn_print_statements "$file" "the logging module"
}

handle_notebook() {
  local file="$1"
  run_check ruff format --quiet "$file"
  run_check ruff check --fix --quiet "$file"
}

# SwiftLint needs SourceKit, which ships only with full Xcode (not Command Line Tools).
has_xcode_toolchain() {
  local dev_dir; dev_dir="$(xcode-select -p 2>/dev/null)"
  [[ -d "$dev_dir/Toolchains/XcodeDefault.xctoolchain" ]]
}

handle_swift() {
  local file="$1"
  run_check swiftformat --quiet "$file"
  if has_xcode_toolchain; then
    run_check swiftlint lint --quiet --strict "$file"
  else
    echo "[hook] swiftlint skipped: install Xcode and run 'sudo xcode-select -s /Applications/Xcode.app'" >&2
  fi
  warn_print_statements "$file" "os.Logger"
}

extract_file_path() {
  /usr/bin/python3 -c '
import json, sys
try:
    tool_input = json.load(sys.stdin).get("tool_input") or {}
except ValueError:
    tool_input = {}
print(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
'
}

main() {
  local file; file="$(extract_file_path)"
  [[ -z "$file" || ! -f "$file" ]] && exit 0
  cd "$ROOT" || exit 0
  case "$file" in
    */.venv/*|*/DerivedData/*|*/build/*) exit 0 ;;
    *.py|*.pyi) handle_python "$file" ;;
    *.ipynb) handle_notebook "$file" ;;
    *.swift) handle_swift "$file" ;;
    *) exit 0 ;;
  esac
  if [[ -n "$FINDINGS" ]]; then
    printf 'Issues in %s:\n%s' "$file" "$FINDINGS" >&2
    exit 2
  fi
  exit 0
}

main
