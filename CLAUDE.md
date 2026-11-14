
# Project Instructions

## Overview
This project combines:
- Python for machine learning and AI agents
- Swift / SwiftUI for the iOS application
- Xcode for iOS builds and development

## Architecture
- Python components implement machine learning
  and agent functionality.
- Swift components implement the iOS application.
- Maintain clear separation between ML logic
  and iOS user interface code.
- Follow the project's existing integration
  architecture.
- Python and the app communicate over HTTPS: the Judge
  backend (`watcher_Agent/judge`, FastAPI) defines the
  contract in `watcher_Agent/docs/API_CONTRACT.txt` and
  `watcher_Agent/contracts/v1`. Inspect it before
  changing either side.

## Judge backend (watcher_Agent)
- What it does and in what order: `JUDGE_WORKFLOW.txt`.
- What it stores for the Guardian and Reflector:
  `JUDGE_MEMORY_OUTPUTS.txt`.
- Setup, run, docs index: `watcher_Agent/README.txt`.
- Database: Tiger Data PostgreSQL, schema `judge`,
  migrations in `watcher_Agent/migrations`.
- Invariants (also in `.claude/rules/project/judge-backend.md`):
  raw audio, video and extra frames stay in RAM only;
  one model call and one embedding per reel; approved
  values are read-only; a reel that cannot be judged has
  a null verdict, never `neutral`; the service runs as
  one process.
- The Judge never calls the Guardian or Reflector; they
  read its tables. Do not add those agents here.
- Verify with `watcher_Agent/scripts/verify.sh` (set
  `TEST_DATABASE_URL` to include PostgreSQL tests).

## Python Guidelines
- Follow the ECC Python and common rules.
- Use type hints where appropriate.
- Prefer modular, testable agent components.
- Write tests for agent behavior and data
  processing.
- Follow the existing dependency management
  system.
- Avoid hardcoding credentials or API keys.

## Swift / iOS Guidelines
- Follow the ECC Swift and common rules.
- Use the project's established SwiftUI/UIKit
  architecture.
- Keep UI and business logic separated.
- Respect Swift concurrency and actor isolation.
- Write unit tests for application logic.
- Validate changes with the Xcode build system.

## Machine Learning Guidelines
- Document model inputs and outputs.
- Validate input and output data.
- Test agent behavior using reproducible cases.
- Keep secrets, sensitive datasets and private
  training data out of Git.
- Follow the team's model versioning strategy.

## Development Workflow
- Make changes on the assigned feature branch.
- Review existing code before modifying it.
- Plan changes that affect both Python and iOS.
- Prefer small, reviewable changes.
- Run relevant tests before committing.
- Never commit secrets or local credentials.
- Use pull requests to merge into main.

## Tooling
Teammate machine setup: see `SETUP.md`.
- Python: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
  (black, isort, ruff, mypy, bandit, pytest, pytest-cov; config in `pyproject.toml`).
  This also installs the Judge runtime dependencies (`watcher_Agent/requirements.txt`).
- Swift: `brew install swiftformat swiftlint` (config in `.swiftformat`, `.swiftlint.yml`).
- iOS builds require full Xcode selected via `xcode-select`.
- ECC plugin (`ecc@ecc`) is enabled in `.claude/settings.json`; its hooks need Node 18+.
- A PostToolUse hook (`.claude/hooks/post-edit-format-lint.sh`) formats and lints
  edited `.py`, `.ipynb`, and `.swift` files.
