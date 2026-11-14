# Guardian: progress and findings

A running record for the Guardian and Reflector build (`guardian/`). The build spec is `docs/GUARDIAN_SPEC_V5.md`; this file records what was found and what each step produced, so it is not lost between sessions.

Last updated: Saturday Nov 14, 3:45 pm. Branch: `guardian`.

## Where things stand

| Step (spec priority) | Status | Commit |
| --- | --- | --- |
| 0. Setup and library checks | Done | `2401d3a` |
| 1. Interview and value approval | Done | `e5d897a`, `5fbb82f` |
| 2. Reel explanation and feedback in chat | Done | `b2fc5ea` |
| Integration checkpoint 1 | In progress | |
| 3. Reflector | Not started | |
| 4. Inbox approval (`get_questions`, `answer_question`) | Not started | |
| 5. Learning evaluation | Not started | |
| Optional items | Not started | |

Offline tests: 205 passing, 98% coverage. They use a scripted model and make no API calls.

## Step 0: library findings

Versions: `deepagents` 0.7.38, `langchain` 1.5.0, `langgraph` 1.2.22, `langchain-google-genai` 4.5.0, Python 3.13. Each finding below is pinned by a test in `guardian/tests/test_library_assumptions.py`, so a library upgrade that changes it fails a test.

### Approval (chat agent)

| Finding | What the design does about it |
| --- | --- |
| The built-in `HumanInTheLoopMiddleware` pauses **before** the tool body runs, so the quote and evidence checks could not run first | `propose_value_change` runs its checks, creates the proposal record, then calls `interrupt()` itself. The built-in middleware is not used |
| On resume, the tool body **runs again from the top**, with the same tool-call ID | The tool first looks up a proposal by tool-call ID. If one exists it skips the checks and goes straight to `interrupt()` |
| A pause shows up as `result["__interrupt__"]`; `agent.get_state(config).interrupts` tells whether a thread is paused | `chat` builds the card from the proposal record. `resume` checks the thread is actually paused before resuming it |
| The `runtime: ToolRuntime[...]` argument is hidden from the model's tool schema | Tools take no user ID from the model; it comes from runtime context |

### Middleware

| Finding | Use |
| --- | --- |
| `@dynamic_prompt` can read `request.runtime.context` | Values and the brief are inserted into the Guardian's prompt on every call |
| An `after_model` hook may return `jump_to: "model"`, and may replace or remove messages by ID | The value-citation check retries once, then replaces the reply, and removes the rejected reply and the correction from the thread |
| `ModelCallLimitMiddleware(run_limit=N, exit_behavior="error")` raises `ModelCallLimitExceededError` | Chat is capped at 6 model calls per message; the Reflector will be capped at 25 per run and treat the error as a failed run |
| `@wrap_tool_call` can rewrite a tool's result | The frame-text guard |
| `ModelRetryMiddleware` retries every exception by default | Used with `max_retries=2, on_failure="error"` |

### Reflector (deep agent)

| Finding | Consequence for Step 3 |
| --- | --- |
| **No planning tool by default.** A deep agent offers `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `delete` and `task`, but not `write_todos` | Add LangChain's `TodoListMiddleware` |
| **`task` and `delete` are offered by default** | Remove both before the Reflector sees any reel data. Route: a harness profile with `general_purpose_subagent` disabled and `excluded_tools` (profiles are keyed by provider, e.g. `google_genai`). Not yet tested |
| `StoreBackend(namespace=<callable>, store=...)` stores each file as a record with `content` (one string), `encoding`, `created_at`, `modified_at`. A `CompositeBackend` route strips its prefix, so `/memories/brief.md` is stored under key `/brief.md` | `guardian/memory/reflector_files.py` reads these records from code |
| `StoreBackend.read` and `.write` work outside a run when the namespace callable ignores its argument and the store is passed in | Code can snapshot and restore the Reflector's files |
| `write_file` overwrites an existing store file | The Reflector can rewrite brief.md each run |
| `FilesystemPermission(operations, paths, mode)`: first match wins, default allow, built-in file tools only | Used as a second barrier. The first is structural: approved values are not mounted at all |
| A wrapper backend that returns `WriteResult(error=...)` has its message delivered to the model | Caps on brief.md, hypotheses.md and session summaries can be enforced with a "consolidate first" message |
| A custom tool can call `backend.write("/scratch/...")` on the composite backend; the agent can then `grep` and `read_file` it, and nothing reaches the store | `dump_session`, `dump_feedback` and `dump_chatlog` |
| Skills resolve inside the backend, not on local disk. A `/skills/` route to `FilesystemBackend(root_dir=..., virtual_mode=True)` loads them from a local folder | Skills live in `guardian/skills/` |
| `memory=[...]` preloads files into the system prompt | brief.md and hypotheses.md are in context at the start of a run |

### Gemini on the free tier (probed Nov 14, about 2 pm)

| Model | Result |
| --- | --- |
| `gemini-3.5-flash-lite` | Answered every call, in 0.5 to 2 s |
| `gemini-3.5-flash` | Works, but returned 503 "high demand" on about half of calls, even with retries |
| `gemini-3.6-flash`, `gemini-3.7-flash` | 503 |
| `gemini-3.8-flash` | Timed out twice (20 s and 58 s) |
| `gemini-2.5-flash` | Retired |

Decision: both agents default to `google_genai:gemini-3.5-flash-lite`. The Reflector is judged on it in Step 3. Only if it cannot follow the session-review skill does `GUARDIAN_REFLECTOR_MODEL` switch to `gemini-3.5-flash` with an automatic fallback to Flash-Lite. The chat agent stays on Flash-Lite either way.

## Step 1: interview and value approval

Built: `FakeDataSource` and seed data; the memory package (value format, protected values store, proposal records, quote and evidence checks, chat log, the single write path `proposals.decide`); the chat agent with `propose_value_change`; `configure`, `chat`, `resume`, `get_values`, `has_values`, `get_memory`.

Results: every case in the spec's Step 1 gate has a passing test (approve, edit, reject, invalid edit, replayed decision, stale proposal, wrong user, value-ID assignment and non-reuse, user isolation, `chat` while a card is pending, thread ownership). A test confirms a resumed tool creates no second proposal and runs no second quote check. Another confirms only the decision function calls the value writer.

Live (Flash-Lite): the interview produces a value card and approval stores V1. After the prompt fixes the card quotes the user's exact sentence under `In my words`.

Choices made where the spec was silent, all accepted by the owner:

- A change to an existing value may keep that value's wording line, but only if the line is identical to the stored one.
- Code appends `; approved <date>` to the Evidence line when a value is stored.
- With no context and no approved values, `chat` tells the agent the entry is `onboarding`.
- The value format is included in the Guardian's system prompt.
- Held-out reels can be fetched by ID but are hidden from sessions, search and profiles.
- User IDs may contain only letters, digits, `_` and `-`.
- When a pending card carries `Suggested wording` and the model sent no text, `chat` returns a fixed sentence saying the wording is a suggestion the user can edit.

## Step 2: reel explanation and feedback

Built, in the spec's order: the frame-text guard and its injection test first; then `get_reels`, the drift-notification context, the value-citation check, `record_feedback`, `save_note`, and the other read tools (`find_similar_feedback`, `search_reels`, `feed_profile`, `read_memory`).

Design points:

- **The guard fails closed.** Every tool result is labelled as untrusted data unless the tool is in `TRUSTED_TOOLS` (`propose_value_change`, `record_feedback`, `save_note`, whose results are written by package code). Text inside the block cannot open or close it.
- **Notes** have stable IDs (`N1`, ...), a 40-note cap, and their evidence must be an exact quote of the user.
- **Feedback comments recorded through chat do not count as the user's words.** A live run showed the model paraphrasing the user's reason in the comment; counting it would let an agent's wording be quoted later as the user's own.
- **The CONTEXT block tells the agent to look the reels up** whenever reel IDs are passed.

Live scenarios (Flash-Lite): the injection reel changes nothing; a disagreement is recorded as feedback; a drift notification for three reels is explained one reel at a time with stored value IDs, the user's own words and a closing question. The drift scenario passed 3 of 3 consecutive runs on unchanged code.

Known limits of Flash-Lite seen in live runs: it sometimes proposes a value after one message without follow-up questions; it sometimes paraphrases where it could quote.

## Conventions

- Team lint config (`pyproject.toml`): black, isort, ruff, mypy. `guardian/ruff.toml` extends it and ignores only the line-length rule, for prompt text, user-facing messages and seed data.
- Live tests run only with `pytest -m live`, under any pytest config.
- One commit per step on `guardian`, pushed after each step. Merges to `main` are done by the owner.
- The API key lives in the root `.env`, which is git-ignored. Every commit is scanned for the key before it is made.
