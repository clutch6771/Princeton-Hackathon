# Developer Setup

How to get this project running on your own machine (macOS).
You only do this once per machine.

## 1. Get the code

```bash
git clone <repo-url>
cd Princeton-Hackathon
git checkout Reel-Watcher-Agent   # or: git checkout main (after the PR is merged)
```

## 2. Install prerequisites

| Tool | Why | Install |
|------|-----|---------|
| Xcode (full app, not just Command Line Tools) | iOS builds, SwiftLint | App Store, then the command below |
| Homebrew | Package manager | https://brew.sh |
| SwiftFormat, SwiftLint | Swift format/lint | `brew install swiftformat swiftlint` |
| Node.js 18+ | Needed by the ECC plugin's hooks | `brew install node` (skip if `node --version` shows 18+) |
| Python 3.11+ | ML / agent code | `brew install python` (skip if `python3 --version` is fine) |
| Claude Code | AI assistant | https://claude.com/claude-code |

After installing Xcode, open it once to accept the license, then point the
developer tools at it:

```bash
sudo xcode-select -s /Applications/Xcode.app
```

## 3. Set up the Python environment

From the project root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

This installs black, isort, ruff, mypy, bandit, pytest and pytest-cov.
`.venv/` is git-ignored, so each person has their own.

## 4. Start Claude Code

From the project root:

```bash
claude
```

- When asked, **trust the folder**.
- Approve installing the **ECC plugin** (`ecc@ecc`, from the `affaan-m/ECC`
  marketplace). It's enabled in `.claude/settings.json`.
- **Restart Claude Code** after the plugin installs so its agents, skills and
  hooks load.

You don't configure anything else: the rules (`.claude/rules/`), the hook
(`.claude/hooks/`), the settings and the linter configs (`pyproject.toml`,
`.swiftformat`, `.swiftlint.yml`) all come from the repo.

## 5. Verify

Run these and check that each one prints a path or version:

```bash
xcode-select -p                 # should be /Applications/Xcode.app/Contents/Developer
node --version                  # v18 or higher
.venv/bin/ruff --version
.venv/bin/pytest --version
swiftformat --version
swiftlint version
```

Or ask Claude Code: *"confirm the project setup works"*.

When Claude edits a `.py`, `.ipynb` or `.swift` file, the hook formats and lints
it automatically. Any problems it finds are shown to Claude to fix.

## Troubleshooting

- **"swiftlint skipped: install Xcode..."**: full Xcode isn't selected. Rerun the
  `xcode-select` command in step 2.
- **"[hook] ruff not installed"** (or another tool): step 3 wasn't run, or `.venv`
  is missing.
- **The ECC "Fact-Forcing Gate" blocks a command or file edit**: this is expected.
  Claude states what the action is for and retries. To turn it off, start Claude
  with `ECC_GATEGUARD=off claude`.
- **ECC agents or skills missing**: restart Claude Code, then check `/plugin`.

## Rules for everyone

- Never commit secrets, API keys, `.env` files or private datasets.
- Work on your feature branch and merge into `main` through a pull request.
- Use the commit format `<type>: <description>`, where type is one of
  feat, fix, refactor, docs, test, chore, perf, ci.
