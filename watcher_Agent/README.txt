Some initial documentation for the Reel Watcher Agent

Make sure to put some stuff here.

Need to set up the repository in line with ECC



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
