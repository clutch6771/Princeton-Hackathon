REEL WATCHER AGENT: JUDGE AND REEL TRANSCRIBER
==============================================

1. SCOPE
--------
This folder holds the Judge: a platform-neutral Python (FastAPI) backend that
  - receives sampled JPEG frames and app-audio AAC chunks,
  - drops anything that is not privacy-certified Instagram Reels media,
  - splits the stream into observed reel exposures,
  - makes ONE Gemini call per reel that transcribes, describes and judges it
    against the user's approved values (aligned | neutral | conflict),
  - stores the result in Tiger Data PostgreSQL,
  - returns a warning through ingest responses when 3 of the last 15 judged
    reels conflict (600-second cooldown).
Not here: Guardian/chat agent, Reflector, onboarding, value approval, GUI,
native iOS/Android code, Share Extension, feed blocking or re-ranking.

Start with (repository root):
  JUDGE_WORKFLOW.txt          how the Judge works, step by step
  JUDGE_MEMORY_OUTPUTS.txt    what it writes and who reads it


2. ARCHITECTURE
---------------
  judge/api            FastAPI routes (thin), identity, upload parsing
  judge/ingest         admission, clock mapping, volatile session state
  judge/privacy        backend eligibility gate
  judge/segmentation   frame signatures, reel boundaries, frame reservoir
  judge/audio          AAC decode (PyAV), timestamp-based PCM assembly, WAV
  judge/transcriber    audio preparation, transcript status and validation
  judge/values         read-only approved values (parser, reader, PG source)
  judge/feedback       feedback storage, example retrieval, timeline, deletion
  judge/understanding  prompt, Gemini and local adapters, output validation
  judge/embedding      asynchronous embedding indexer
  judge/notifications  policy, ordered batch accounting, outbox
  judge/pipeline       finalizer, judge pipeline, completion, lifecycle
  judge/persistence    repository interfaces, PostgreSQL adapter, migrator
  judge/contracts      typed, versioned data contracts
  judge/devfakes       explicit development fakes (never used in production)
  judge/bootstrap.py   wiring from injected capabilities
  judge/runtime_factory.py  real adapters chosen by configuration
  migrations/          SQL migrations + migrate.py
  contracts/v1/        exported JSON Schemas + synthetic fixtures
  scripts/             verify.sh, dev_check.sh, schema export, dev seeding
  docs/                the documents listed in section 6
  tests/               unit/, storage/ (also runs on PostgreSQL), support/
Raw media lives only in this process's RAM. Run exactly ONE process.


3. SETUP
--------
  python3 -m venv .venv
  .venv/bin/pip install -r requirements-dev.txt      (includes runtime deps)
  cp watcher_Agent/.env.example watcher_Agent/.env   (then fill it in)
Database: follow docs/DATABASE_INTEGRATION.txt (provision, TLS URL, pgvector,
migrations, runtime role, seeding).


4. CONFIGURATION AND EXECUTION
------------------------------
All settings: docs/MEMORY_PARAMETERS.txt. Minimum for production:
  DEMO_USER_ID, DATABASE_URL (TLS), GEMINI_API_KEY
Run:
  .venv/bin/uvicorn judge.api.app_api:create_app --factory \
      --app-dir watcher_Agent --host 0.0.0.0 --port 8000 --workers 1
Development without Gemini or a database (synthetic judgments, nothing kept):
  APP_ENV=development PRIVACY_MODE=dev_permissive PROVIDER_MODE=fake \
  STORAGE_MODE=memory DEMO_USER_ID=demo-user  (same uvicorn command)
Production refuses fake providers, memory storage, the permissive privacy
gate and enabled SDK tracing. API reference: docs/API_CONTRACT.txt, /docs.


5. VERIFICATION
---------------
  watcher_Agent/scripts/verify.sh
runs ruff, black, isort, mypy, bandit, the contract-drift check and pytest
with coverage (floor 80%). Add TEST_DATABASE_URL=postgresql://... to include
the PostgreSQL integration tests; GEMINI_API_KEY=... with "-m live" for the
opt-in provider smoke tests.
Last run (2026-11-14): 553 passed, 2 skipped (pgvector not installed),
coverage 96%, against in-memory storage and local PostgreSQL 17. Not run:
live Gemini tests, pgvector SQL, Tiger Data cloud, any physical device.
See docs/PRD_TRACEABILITY.txt for what is and is not verified.


6. DOCUMENTS
------------
  ../JUDGE_WORKFLOW.txt            workflow of the Judge
  ../JUDGE_MEMORY_OUTPUTS.txt      memory outputs for Guardian and Reflector
  docs/MEMORY_PARAMETERS.txt       authoritative memory/configuration registry
  docs/API_CONTRACT.txt            HTTP contract, payloads, errors, delivery
  docs/PLATFORM_BOUNDARY.txt       iOS/Android adapter contract, spike list
  docs/PRIVACY.txt                 eligibility, audio, retention, tracing
  docs/DATABASE_INTEGRATION.txt    Tiger Data tutorial (12 steps)
  docs/PRD_TRACEABILITY.txt        requirement -> module -> tests


7. ECC IN THIS PACKAGE
----------------------
ECC is development tooling, not a runtime dependency.
  - Routers are named *_api.py so the ECC FastAPI rule applies to them.
  - .claude/rules/project/judge-backend.md holds the Judge invariants that
    reviewers and future sessions must keep.
  - pyproject.toml carries the pytest, coverage (80%) and mypy settings.
  - scripts/verify.sh is the verification loop.
  - The plan is in .claude/plans/judge-reel-transcriber.plan.md.



-Packages Used/ Necessary Packages and Extentions

  Claude Code plugin
    - ecc@ecc 2.2.3 (marketplace: https://github.com/affaan-m/ECC.git)
    - ECC npm runtime deps (js-yaml, ajv, zod, @iarna/toml, sql.js), installed in
      ~/.claude/plugins/cache/ecc/ecc/2.2.3/node_modules via `npm ci --omit=dev`
    - Node.js >= 18 (installed: v24)

  Python (project .venv, pinned in requirements-dev.txt)
    black 26.10.1, isort 9.0.2, ruff 0.17.5, mypy 2.4.1, bandit 1.9.4,
    pytest 9.2.0, pytest-cov 7.1.0, python-dotenv 1.2.4
    Setup: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

  Swift / iOS (Homebrew)
    swiftformat 0.64.5, swiftlint 0.65.1
    Setup: brew install swiftformat swiftlint
    Xcode (App Store) - NOT INSTALLED YET. Required for iOS builds and for
    swiftlint. After installing: sudo xcode-select -s /Applications/Xcode.app


-ECC Configuration Change Log (2026-11-14)

  Plugin installation (user-level, outside the repo)
    1. Removed duplicate ECC install scoped to the home folder (/Users/gwyi)
       -> ~/.claude/plugins/installed_plugins.json
    2. Removed ECC marketplace + enable from user settings so ECC is
       configured only by this repo -> ~/.claude/settings.json
    3. Installed ECC's missing npm packages so its hooks can run
       -> ~/.claude/plugins/cache/ecc/ecc/2.2.3/node_modules

  Project settings
    4. Marketplace source aligned to the registered git URL form
       -> .claude/settings.json (extraKnownMarketplaces.ecc)
    5. Commit attribution set to match ECC git rule: includeCoAuthoredBy=false
       -> .claude/settings.json
    6. Added PostToolUse format/lint hook for .py/.ipynb/.swift
       -> .claude/settings.json (hooks) + .claude/hooks/post-edit-format-lint.sh
       (ruff/isort/black/mypy for Python, ruff for notebooks, swiftformat and
       swiftlint for Swift; swiftlint skipped until Xcode is installed)

  Rules
    7. Python rules now also apply to Jupyter notebooks ("**/*.ipynb")
       -> .claude/rules/ecc/python/{coding-style,patterns,security,testing,hooks}.md
    8. FastAPI rule no longer triggers on every "app/" folder
       -> .claude/rules/ecc/python/fastapi.md (removed "**/app/**/*.py")
    9. Swift security rule now covers .xcconfig, Info.plist, .entitlements
       -> .claude/rules/ecc/swift/security.md
   10. Hook rules point at the project hook instead of ~/.claude/settings.json
       -> .claude/rules/ecc/python/hooks.md, .claude/rules/ecc/swift/hooks.md
   11. Attribution note corrected to match the project setting
       -> .claude/rules/ecc/common/git-workflow.md
   12. Agents are suggested, not auto-spawned without asking
       -> .claude/rules/ecc/common/agents.md
   13. New ML rule (ECC has none): points to mle-workflow / pytorch-patterns
       skills and ecc:mle-reviewer -> .claude/rules/project/machine-learning.md
   NOTE: items 7-12 edit vendored ECC rules; re-apply them after updating ECC.

  Tooling config + docs
   14. Tool config -> pyproject.toml, .swiftlint.yml, .swiftformat
   15. Pinned Python dev deps -> requirements-dev.txt
   16. ML artifacts, tool caches, Xcode build/ ignored -> .gitignore
   17. Tooling section added -> CLAUDE.md

  Still to do manually
    - Install Xcode, then: sudo xcode-select -s /Applications/Xcode.app
    - Restart Claude Code so the plugin, rules and hook reload; check with /plugin
