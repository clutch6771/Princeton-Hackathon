# Plan: Guardian V5 (chat agent + Reflector)

**Source spec**: `docs/GUARDIAN_SPEC_V5.md` (authoritative). Context only: `docs/project.prd.md`.
**Scope**: the `guardian/` Python package. Nothing outside it is created or changed (no iOS, Judge, ingest, server, MongoDB).
**Complexity**: Large. About 14 hours to feature freeze; the MVP path is planned at about 11.
**Written**: Saturday Nov 14, about 2:00 pm.

## Summary

Build the MVP demo loop in the spec's five priority steps: interview and value approval, reel explanation and feedback, Reflector review, inbox approval, then the learning evaluation. Each step ends with deterministic tests passing before the next begins. Optional items are not started until step 4 passes.

The centre of the build is the memory module. Approved values sit in a part of the store that neither agent's tools can reach, and one function writes them, only to apply a user's decision on a pending proposal.

## Schedule

| When | Step | Gate |
| --- | --- | --- |
| Sat 2:00 – 2:45 pm | 0. Setup and API spike | Spike script passes |
| Sat 2:45 – 7:30 pm | 1. Interview and value approval | **Checkpoint 1, Sat 9:00 pm** (handover aimed at 8:00 pm) |
| Sat 7:30 – 10:00 pm | 2. Explanation and feedback | Injection test passes before reel text reaches an agent |
| Sat 10:00 pm – Sun 12:30 am | 3. Reflector | Seeded session yields both cards; forced failure leaves nothing behind |
| Sun 12:30 – 1:00 am | 4. Inbox approval | **Checkpoint 2, Sun 1:00 am** |
| Sun 1:00 – 2:30 am | 5. Learning evaluation | Prints three stage results |
| Sun 2:30 – 4:00 am | Notebook, README, buffer | Feature freeze 4:00 am |

Step 4 is short because its write path is built and tested in step 1. If step 3 runs late, the backend owner still gets `get_questions` and `answer_question` at checkpoint 2, working against a seeded card, and the Reflector's real output follows.

## Blockers and decisions needed from the owner

| # | Item | Needed by | Default if no answer |
| --- | --- | --- | --- |
| 1 | **Gemini API key** (`GOOGLE_API_KEY`) and the exact Flash model name for `GUARDIAN_CHAT_MODEL` / `GUARDIAN_REFLECTOR_MODEL`. Neither is set on this machine | Step 0 smoke test; all live tests | Deterministic work proceeds; live tests and the evaluation wait |
| 2 | **`seed/persona.md`**: who writes it | Step 1 | I draft it (three values, typical reactions) and generate the seed files; you review the persona and the 20 held-out labels once |
| 3 | **Ollama is not installed.** The spec names `qwen3:8b` for development | — | Skip it. Tests use a scripted fake model; live checks use Gemini. Installing means a download of several GB on venue Wi-Fi |
| 4 | **Setup call.** The entry points take only user-level arguments, so the store, checkpointer and data source need to be supplied once | Step 1 | Add `guardian.api.configure(store, checkpointer, datasource, clock=None)`. Entry-point signatures stay exactly as in the spec. The backend owner calls it at startup with the MongoDB objects |
| 5 | **`memory.py` size.** The spec lists one file; it will pass the 800-line ceiling | Step 1 | Make it a package, `guardian/memory/`, with the same import path (`guardian.memory`) and one file per concern |

## Verified API facts (checked against current docs, Nov 14)

`deepagents` 0.7.38 requires Python 3.11+ and installs `langchain` 1.5+ and `langchain-google-genai`. This machine has Python 3.13.7 and none of the packages.

| Need | Finding | Consequence |
| --- | --- | --- |
| Approval before the user sees the card | Built-in `HumanInTheLoopMiddleware` pauses **before** the tool body runs | Use the spec's stated alternative: `propose_value_change` runs its checks, creates the proposal record, then calls `interrupt()` itself. The built-in middleware is not used |
| Resuming an in-tool interrupt | The tool body **runs again from the top** on resume | The tool first looks up a proposal by its tool-call ID; if one exists it skips the checks and goes straight to `interrupt()`. Without this, the re-run would re-check against values that have just changed |
| Seeing the pause | `result["__interrupt__"]`; resume with `Command(resume=...)` on the same `thread_id` | `chat` builds `pending` from the proposal record, not from model text |
| Citation retry | `after_model` hooks may return `jump_to: "model"` (declared with `hook_config(can_jump_to=[...])`) | Retry once is possible; replace the reply on the second failure |
| Prompt with values and brief | `@dynamic_prompt`, reading `request.runtime.context` | As the spec describes |
| User ID in tools | `ToolRuntime` from `langchain.tools`; `runtime.context.user_id` | Tools have no user-ID parameter |
| Limits and retry | `ModelCallLimitMiddleware(run_limit=..., exit_behavior=...)`, `ModelRetryMiddleware(max_retries=...)` in `langchain.agents.middleware` | Chat: 6 per message. Reflector: 25 per run |
| Reflector options | `create_deep_agent(... skills, memory, permissions, backend, store, checkpointer, middleware, context_schema)` all exist | As the spec describes |
| Reflector storage | `StoreBackend(namespace=<callable>, store=...)`; `CompositeBackend(default=StateBackend(), routes={...})` | The Reflector is built per run with a namespace fixed to one user |
| Write rules | `FilesystemPermission(operations, paths, mode)`: first match wins, default allow, built-in file tools only | Added as the second barrier. The first is structural: values are not mounted at all |
| Cap enforcement on file writes | The backends page documents a policy wrapper that returns `WriteResult(error=...)` | Wrap the Reflector's store backend to reject over-cap writes with a "consolidate first" message |
| Skills | Paths resolve inside the backend, not on local disk. `SKILL.md` needs `name` (matching its folder) and `description` | Route `/skills/` to a `FilesystemBackend` rooted at `guardian/skills/`; fallback is `backend.upload_files` |

**Still to confirm in step 0** (each has a fallback):

1. How a custom tool writes a file into `/scratch/`. Expected: return a `Command` updating the `files` state with `create_file_data`. Fallback: route `/scratch/` to a per-run store namespace and delete it afterwards.
2. The stored format of a `StoreBackend` file, needed to read brief.md from code. Fallback: read through the backend's own `read`.
3. That `ToolRuntime` exposes the tool-call ID. Fallback: key the proposal on thread ID plus a hash of the arguments.
4. That a `CompositeBackend` route strips its prefix before the `FilesystemBackend` for skills.
5. That Gemini Flash completes a one-tool call through `create_agent` (the spec's smoke test).

## Step 0 results (Sat Nov 14, about 2:15 pm)

All five open confirmations are settled, pinned by `guardian/tests/test_library_assumptions.py` (21 tests, scripted model) and `guardian/tests/test_live_smoke.py` (2 tests, real model).

| # | Question | Result |
| --- | --- | --- |
| 1 | Custom tool writes into `/scratch/` | Works: the tool calls `backend.write("/scratch/...")` on the composite backend; the agent then reads and greps the file. Nothing leaks into the store |
| 2 | Stored format of a `StoreBackend` file | A dict with `content` (one string), `encoding`, `created_at`, `modified_at`. Code can call `StoreBackend.read` and `.write` outside a run. The route prefix is stripped, so `/memories/brief.md` is stored under key `/brief.md` |
| 3 | `ToolRuntime` exposes the tool-call ID | Yes, and it is the same on the re-run after resume |
| 4 | Skills from a local folder | Works through a `/skills/` route to `FilesystemBackend(root_dir=..., virtual_mode=True)` |
| 5 | Gemini one-tool call and in-tool interrupt | Pass on `gemini-3.5-flash-lite` |

Changes this makes to later steps:

- **Model.** Defaults are `google_genai:gemini-3.5-flash-lite` for both agents. On the free tier, `gemini-3.5-flash` returned 503 "high demand" on about half of calls, `3.6` and `3.7` returned 503, `3.8` timed out at 58 s, and `2.5` is retired. Whether Flash-Lite follows the Reflector's eight-step skill is checked in step 3.
- **Planning tool (step 3).** `deepagents` 0.7.38 does not include `write_todos`. Add LangChain's `TodoListMiddleware` to the Reflector.
- **Extra default tools (step 3).** A deep agent offers `task` and `delete` by default. Drop them with a harness profile (`general_purpose_subagent` disabled, `excluded_tools`); if that proves awkward, leave them and deny `delete` through `permissions`.
- **Call limit.** `exit_behavior="error"` raises `ModelCallLimitExceededError`, which the Reflector run treats as a failure.
- **Live tests are opt-in.** `pytest` alone never calls the API; use `-m live`.
- The spike became permanent tests instead of a throwaway `spike/api_spike.py`.

## Patterns to mirror

The repository has no code. The spec's types, package layout, prompt drafts and tool docstrings are used word for word. Conventions set here:

| Category | Convention |
| --- | --- |
| Errors | Entry points never raise for user-caused failures; they return `error` (`chat`, `resume`) or `ok: false` (`answer_question`). Tools return an error string the model can act on |
| Data | Stored records are replaced, never mutated in place; validators are pure functions returning a result |
| Logging | `logging.getLogger("guardian")`; no prints |
| Tests | `guardian/tests/`, pytest, behaviour-named, arrange / act / assert; a scripted fake chat model; an injected clock |

## Files

All paths are under `guardian/`. Nothing else in the repository is touched.

| File | Step | Holds |
| --- | --- | --- |
| `requirements.txt`, `README.md`, `.env.example` | 0, 1 | Pinned versions; setup and `configure()` for teammates |
| `spike/api_spike.py` | 0 | The five confirmations above |
| `datasource.py` | 1 | Types, `DataSource`, `FakeDataSource` |
| `seed/persona.md`, `reels.json`, `feedback.json`, `heldout_labels.json` | 1 | Simulated user and data |
| `memory/formats.py` | 1 | Parse and validate value entries, wording lines, caps |
| `memory/values.py` | 1 | Protected values store, ID counter, `get_values` text |
| `memory/proposals.py` | 1 | Proposal records, fingerprints, quote and evidence checks, the single write path |
| `memory/records.py` | 1 | Notes, chat log (7-day), store namespaces |
| `memory/inbox.py` | 1, 4 | Cards, staging, 5-pending cap, duplicate detection |
| `memory/reviews.py` | 3 | Review records, snapshots, feedback cursor |
| `memory/reflector_files.py` | 3 | Reflector-owned files: policy wrapper, caps, reads from code |
| `prompts.py` | 1, 3 | Both system prompts from the spec |
| `chat_agent.py`, `tools_chat.py` | 1, 2 | `build_chat_agent`, eight tools |
| `middleware.py` | 2 | Frame-text guard, value-citation check, limits and retry setup |
| `reflector.py`, `tools_reflector.py`, `skills/session-review/SKILL.md` | 3 | `build_reflector`, five tools, skill from the spec |
| `api.py` | 1, 3, 4 | The eight entry points and `configure` |
| `eval/learning_eval.py` | 5 | Stand-in Judge, three stages, per-stage cache |
| `notebooks/demo.ipynb` | after 5 | The demo loop on fake data |
| `tests/` | every step | See each step |

## Tasks

### Step 0: Setup and API spike

- **Action**: Create a virtual environment with Python 3.13 and pin `deepagents==0.7.38`, `pytest`, `pytest-cov`. Write `spike/api_spike.py` to settle the five open confirmations with a scripted fake model, then run the one-tool smoke test against Gemini if the key is present. Record what was found at the top of the script.
- **Validate**: `python guardian/spike/api_spike.py` prints a pass or the chosen fallback for each item.

### Step 1: Guardian interview and value approval

Tests are written first for the memory module, since everything else trusts it.

- **1a. Data.** Types and `FakeDataSource` (keyword-overlap similarity, injectable clock, loader that also accepts real Judge output). Draft `persona.md`; generate about 40 reels: a 12-reel feedback session, a 20-reel held-out set and a short demo session, with at least 5 talking-head reels, several low-confidence reels and one injection reel. The feedback session carries 5 `disagree` marks with comments and 3 `not_for_me` marks on one pattern without comments.
- **1b. Formats.** Parse and validate a value entry: header, the four fixed lines, exactly one wording line, `## New:` header for new values, 12-value cap.
- **1c. Protected values and proposals.** Per-user namespaces. A counter that only increases. Proposal records with status and fingerprint. `decide(user_id, proposal_id, decision)` is the only function that writes values, and applies the checks in the spec's table: pending, owner, fingerprint, format, wording, ID, cap. Wording label on approval is decided by code. Replays return an error; stale proposals become `superseded`; failed checks leave the card pending.
- **1d. Quote and evidence checks.** Quote search over the user's chat log and feedback comments, ignoring case, spacing and surrounding punctuation. Evidence IDs must exist for this user.
- **1e. Chat agent.** `create_agent` with the spec's prompt, values and brief inserted on each call (brief inside a labelled block), and `propose_value_change` as described under "Verified API facts". Call limit and retry are attached here, because they are needed as soon as a live model runs.
- **1f. Entry points.** `configure`, `chat`, `resume`, `get_values`, `has_values`, `get_memory`.
  - `chat` rejects a thread ID not starting with `"{user_id}:"`, returns the pending card with an error if one is waiting, records the user's message in the chat log before the agent runs, and prefixes the `CONTEXT` block.
  - `resume` applies the decision in code first, then continues the paused run with only the outcome.
- **Tests**: approve, edit, reject, invalid edit, replayed decision, stale proposal, wrong user, ID assignment and non-reuse, quote found and not found, `Suggested wording` stored as `Agreed wording`, edited wording stored as `In my words`, user isolation, `chat` while pending, bad thread ID.
- **Validate**: `python -m pytest guardian/tests -m "not live" --cov=guardian`
- **Hand over at checkpoint 1**: `chat`, `resume`, `get_values`, `get_memory`, `has_values`, `configure`, the fake data and `README.md` to the backend owner; the appendix Judge prompt to the Judge owner.

### Step 2: Reel explanation and recording feedback

Order matters: the guard comes first.

- **2a. Frame-text guard.** Middleware that wraps descriptions, transcripts and on-screen text in tool results in a labelled untrusted-data block, and neutralises the block's own delimiter if it appears in the content. Test it with the injection reel before `get_reels` exists.
- **2b. Tools.** `get_reels` (transcripts cut to 400 characters), `find_similar_feedback`, `search_reels`, `feed_profile`, `read_memory`, `record_feedback` (`source="chat"`), `save_note` (40-note cap, stable `N` IDs).
- **2c. Value-citation check.** Find `V<n>` in a final reply; if an ID is not in the stored values, retry once with a correction, then replace the reply with a plain "no stored value matches" message.
- **2d. Drift-notification entry.** `context.entry == "drift_notification"` with reel IDs in the `CONTEXT` block.
- **Tests**: guard output; injection reel text never reaches the model unlabelled; citation check with a scripted reply citing `V99`; note cap; feedback recorded with the right source.
- **Live** (run once each): three-turn interview produces a card; a drift notification for three reels is explained with existing value IDs; a talking-head reel is explained from its transcript; the injection reel changes nothing.

### Step 3: Reflector

- **3a. Reflector-owned files.** A store namespace per user holding only brief.md, hypotheses.md and `sessions/`. A policy wrapper rejects over-cap writes (150 words, 10 items, 200 words) with a "consolidate first" message. `permissions` is added as a second barrier. Code reads these files for the chat prompt and `get_memory`.
- **3b. Tools.** `dump_session`, `dump_feedback`, `dump_chatlog` (also copies notes) write read-only copies to `/scratch/`; `feed_profile`; `queue_question` stages a card, runs the quote and evidence checks, refuses duplicates of pending or rejected proposals, and enforces 2 value changes per run and 5 pending in total. The guard from step 2 applies to these results and to file reads.
- **3c. Agent.** `create_deep_agent` built per run for one user, with the spec's prompt, the `session-review` skill, brief and hypotheses preloaded, no subagent tool, a rate limiter, retry, and a 25-call limit that ends the run as failed.
- **3d. `reflect_pending`.** Follows the spec's six rules:
  - picks sessions that are finished (stopped, or 10 idle minutes) and neither `done` nor `running`;
  - returns 0 if a run is in progress; a run marked `running` for over 10 minutes may be retried;
  - snapshots brief.md and hypotheses.md, restores them on failure, and discards staged cards;
  - on success, commits staged cards, builds the "What I learned" card in code from the summary's bullets under one fixed ID per session, and advances the feedback cursor;
  - marks a session `failed` after 3 attempts.
- **Default**: the held-out set is not listed by `list_sessions`, so the Reflector never reviews it.
- **Tests** (scripted model): two calls review each session once; a concurrent call returns 0; a forced mid-run failure leaves both files and the inbox unchanged, and the retry produces no duplicates; file tools cannot reach code-owned records; caps hold across five simulated sessions; a hypothesis's "Sessions seen" equals its distinct session IDs.
- **Live** (once): the seeded feedback session yields a "What I learned" card and a value-change card that cites the feedback and carries `Suggested wording`.

### Step 4: Approval and retrieval of updated values

- **Action**: `get_questions` and `answer_question` over the inbox and the step 1 write path. Valid actions per card kind as in the spec's table; anything else, an answered card, or another user's card returns `ok: false` and changes nothing.
- **Tests**: approving the Reflector's card changes what `get_values` returns; stale card; duplicate card; `dismiss` on a `learned` card; invalid action per kind.
- **Hand over at checkpoint 2**: `reflect_pending`, `get_questions`, `answer_question`.

### Step 5: Learning evaluation

- **Action**: `eval/learning_eval.py`. A text-only stand-in Judge runs the appendix prompt at temperature 0 on the 20 held-out reels in three stages. Between stages 2 and 3, `reflect_pending` runs once on the feedback session and every value-change card is approved unchanged. Outputs are cached per stage. If no proposal is queued, stage 3 reports "no proposal". The printed result states that a difference of one or two reels is within noise.
- **Validate**: `python -m guardian.eval.learning_eval` prints three lines: matches out of 20, and matches on the in-pattern reels.
- **Cost**: 60 Judge calls and one Reflector run, behind the rate limiter.

### After step 5

- `notebooks/demo.ipynb`: the demo loop end to end on `FakeDataSource`.
- Final pass over the MVP acceptance criteria in the spec, each mapped to a test or a notebook cell.

### Optional (only if step 4 passed and time remains before 4:00 am)

In the spec's order: re-rate cards; evidence checker; `delete_memory_item`; model fallback; decision trace and summarization.

## Validation

```bash
python3.13 -m venv guardian/.venv && source guardian/.venv/bin/activate
pip install -r guardian/requirements.txt
python guardian/spike/api_spike.py
python -m pytest guardian/tests -m "not live" --cov=guardian --cov-report=term-missing
python -m pytest guardian/tests -m live -x        # needs GOOGLE_API_KEY; run sparingly
python -m guardian.eval.learning_eval
```

Coverage target: 80% on `guardian/`, excluding `spike/`, `eval/` and the notebook.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Free-tier Gemini limits stall live tests, the evaluation or the demo | High | Rate limiter below the limit; retry middleware; live tests run one at a time, once each; the evaluation caches per stage; deterministic tests need no quota |
| Step 3 overruns and squeezes checkpoint 2 | Medium | Step 4's write path already exists; hand over the inbox functions on a seeded card; the evaluation and notebook absorb the delay, not the loop |
| Gemini Flash does not follow the eight-step skill reliably within 25 calls | Medium | Success is judged in code (summary exists with 1–3 bullets); a failed run restores state and retries up to 3 times; tighten the skill wording after the first live run |
| A step 0 confirmation fails | Medium | Each has a named fallback; none changes a public interface |
| The Reflector queues no value change in the evaluation | Medium | Reported as "no proposal", as the spec requires; never written by hand |
| Teammates' reel records differ from the spec's `Reel` type | Medium | The loader tolerates extra fields; agree the record at checkpoint 1 using the seed file as the example |
| The model proposes two values in one turn | Low | One pending chat proposal per thread; the second call gets an error telling it to wait |

## Notes on the spec

- It refers to the PRD as `claude/PRD.md`; the file is `docs/project.prd.md`.
- The PRD lists a fallback provider as a rate-limit mitigation; V5 makes it Optional. V5 is followed.
- The middleware table lists built-in human-in-the-loop for `propose_value_change`. The in-tool interrupt replaces it, as the spec's Guardian section allows.

## Acceptance

- [ ] Steps 1 to 5 complete, each with its deterministic tests passing
- [ ] Every MVP acceptance criterion in the spec maps to a passing test or notebook cell
- [ ] Public interfaces match the spec; the only addition is `configure`
- [ ] Nothing outside `guardian/` is created or changed
