---
paths:
  - "**/*.py"
  - "**/*.pyi"
  - "**/*.ipynb"
---
# Python Hooks

> This file extends [common/hooks.md](../common/hooks.md) with Python specific content.

## PostToolUse Hooks

Configured in the project `.claude/settings.json` (script: `.claude/hooks/post-edit-format-lint.sh`):

- **black/ruff**: Auto-format `.py` files after edit
- **mypy/pyright**: Run type checking after editing `.py` files

## Warnings

- Warn about `print()` statements in edited files (use `logging` module instead)
