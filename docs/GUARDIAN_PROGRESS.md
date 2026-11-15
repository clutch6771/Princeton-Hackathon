# Guardian: progress and findings

A running record for the Guardian and Reflector build (`guardian/`). The build spec is `docs/GUARDIAN_SPEC_V5.md`; this file records what was found and what each step produced, so it is not lost between sessions.

Last updated: Sunday Nov 15, 12:15 am. Branch: `guardian`.

## Where things stand

| Step (spec priority) | Status | Commit |
| --- | --- | --- |
| 0. Setup and library checks | Done | `2401d3a` |
| 1. Interview and value approval | Done | `e5d897a`, `5fbb82f` |
| 2. Reel explanation and feedback in chat | Done | `b2fc5ea` |
| Integration checkpoint 1 | Ready on the Guardian side; waiting for the owner to confirm the chat from the app or the dev server | `86e0a5f` and the dev-server commit |
| 3. Reflector | Done | `5caef44` |
| 4. Inbox approval (`get_questions`, `answer_question`) | Done; integration checkpoint 2 waiting on the owner | this commit |
| Backend brief Part A: survey of the app and the teammate's backend | Done | |
| Backend brief Part B: storage on Postgres | Done; tables created in the team database | `d28f583` |
| Backend brief Part C: Guardian router (`guardian_router/`) | Done | `f6c5e46` |
| Integration: Guardian wired into the Judge server, team database migrated, run page, reset command | Done | `ce62e33`, `6d5e642`, `fbdf1a0` |
| 4b. Reflection addendum (`docs/GUARDIAN_REFLECTION_ADDENDUM.md`) and the new onboarding interview | Done | `3df767e` |
| 5. Learning evaluation | Done; results in `guardian/eval/REPORT.md` | `7542141`, `6053bd3`, this commit |
| Optional items | Not started | |

Offline tests: 482 passing in memory (`guardian/` and `guardian_router/`). The Judge's own suite: 499 passing. They use a scripted model and make no API calls.

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

## Integration checkpoint 1

What exists for trying the chat before the Reflector is built:

- `python -m guardian.demo_chat`: a terminal chat on seed data (interview, `/approve`, `/edit`, `/reject`, `/drift`, `/values`, `/memory`, `/seed`). Tested with a scripted model and run once against Gemini.
- `docs/GUARDIAN_BACKEND_HANDOVER.md`: exact request and response JSON for `/chat`, `/chat/resume`, `/status` and `/memory`, generated from the real functions.
- `dev_server/`: a temporary FastAPI server with those four routes, because the team's backend was not ready. Replaced in Part C by `guardian_router/` and deleted.

Results: the dev server answered all four routes against the live model (status, a drift explanation, an error body, memory) and returns 422 for a malformed body.

Also changed at this checkpoint: an invalid edit of a card now returns a short message written for the user. Before, it returned the format guidance written for the agent.

Gate before Step 3: the owner confirms the chat works from the app or the dev server.

## Step 3: Reflector

Built: the Reflector deep agent (`reflector.py`), its five tools (`tools_reflector.py`), the `session-review` skill, the inbox with staged cards (`memory/inbox.py`), review records with snapshot and restore (`memory/reviews.py`), the guarded backend for the Reflector's own files (`memory/reflector_files.py`), and `reflect_pending` (`reflect_flow.py`).

Order followed: the tool restrictions were built and tested first, before any test or run gave the Reflector reel data.

- **Tool set.** `ToolAllowlist` middleware hides every tool not on a fixed list and refuses a call to one anyway, so the default `task` and `delete` tools are gone and a tool added by a library upgrade stays hidden. `TodoListMiddleware` supplies `write_todos`.
- **Files.** `/memories/` holds only brief.md, hypotheses.md and `sessions/<id>.md` for one user, with caps enforced on write and edit (150 words, 10 hypotheses, 200 words). Approved values, notes, the chat log and the inbox are stored elsewhere and are not mounted. `permissions` is a second barrier.
- **Reel data.** `dump_session`, `dump_feedback` and `dump_chatlog` write read-only copies to `/scratch/`. Anything read back from files or searches is labelled as untrusted data by the frame-text guard.
- **Cards.** `queue_question` stages a value-change card with the same quote, evidence and duplicate checks as chat proposals. Limits: 2 per run, 5 open in the inbox. Re-rate cards are refused (optional feature not built).
- **All or nothing.** Before a run the three files are saved. On any failure they are restored and staged cards and proposals are dropped. On success staged cards become pending, the "What I learned" card is added under one fixed ID per session, and the feedback cursor moves to when the run began.
- **One run at a time.** A per-user lock in the process plus the `running` review record; a run left running for 10 minutes is rolled back and retried; a session is skipped after 3 failed attempts.
- **What a run must produce.** A session summary with 1 to 3 "What I learned" bullets, each citing evidence that exists. Otherwise the run fails and is rolled back. More than 3 bullets are cut to 3. The closing question (from the reflection addendum) is asked for by the skill but is optional: if it is missing, the run is accepted and the card is added without a question.
- **Sessions seen** in hypotheses.md is recomputed in code from the distinct session IDs in each hypothesis's evidence.

Results: 25 tests on the agent and its tools, 25 on `reflect_pending` (success, repeat calls, failure and restore, retry limit, crashed runs, concurrent calls, unfinished sessions, caps across five sessions).

Live (Flash-Lite), the seeded feedback session, two runs of about 85 seconds each, both committed on the first attempt:

- Run 1 queued a refinement of V1 with an "Unless" line for injury rehab and a "What I learned" card ending with a question. It did not propose a value for the three "not for me" marks.
- The skill's step 5 was made explicit about counting marks per group. Run 2 queued both cards: the V1 refinement, and a new value for hustle content with `Suggested wording`, citing the three marks and telling the user the wording is a suggestion.

Decision: the Reflector stays on `gemini-3.5-flash-lite`. It followed the session-review skill.

### Caveat for the learning evaluation: the skill was adjusted after a run on the feedback session

The learning evaluation runs the Reflector on the feedback session (`s_001`). The skill was changed after seeing the Reflector's output on that same session, so the evaluation is not a blind test of the skill. Say so when presenting the result. What changed, in order:

1. **After live run 1** (which proposed the V1 refinement but nothing for the three "not for me" marks), step 5 was reworded to say: group the disagree and not_for_me marks by what the reels have in common, count each group, and give every group with three or more marks its own proposal, up to two; a not_for_me mark counts even with no comment. The wording is general. It names no topic, value or ID from the seed data.
2. **A leak found and removed before the evaluation.** The format examples at the bottom of the skill, written before any live run, used IDs that happened to match the seed data: `V1` with evidence `fb_001, fb_003, fb_005` (three of the rehab disagreements) and a summary bullet citing `fb_002, fb_004` (two of the hustle marks). They now use IDs that do not exist in the seed (`V7`, `fb_101`, `s_020`), with a line saying they are made up. Live runs 1 and 2 were made with the old examples; run 3 was made after the change and again produced both cards.
3. The two example sentences in step 8 are the spec's and addendum's own text ("gym-comparison reels", "comparison reels hard to scroll past") and are unchanged.

The held-out reels and their labels were never shown to the Reflector or used to adjust anything.

## Step 4: inbox approval

Built: `get_questions` and `answer_question` (`inbox_flow.py`).

- `get_questions` returns the user's pending cards, oldest first, in the spec's `Question` shape. Staged and answered cards, another user's cards, and value cards whose proposal is no longer pending are left out. A card waiting in chat is not in the inbox.
- `answer_question` checks the action against the card kind: `approve`, `edit` or `reject` for a value card; `dismiss` for a "What I learned" card. Anything else, a card already answered, or another user's card returns `ok: false` and changes nothing.
- A value card is decided through the same single write path as a chat card, so the same checks apply. An invalid edit or a full value list leaves the card pending. A stale card (its target value changed since the card was made) is refused and closed.
- Re-rate cards are refused: that optional feature is not built.

Results: 25 tests. Live (Flash-Lite): the Reflector reviewed the feedback session and queued two value cards; both were approved through `answer_question`, and `get_values` then returned the refined V1 and a new V4 stored as `Agreed wording`.

Also fixed: an Evidence line could end up with two "approved" stamps when an agent added new evidence after the old stamp. An earlier stamp is now removed wherever it sits.

The terminal chat gained `/reflect`, `/inbox` and `/answer`, so the whole loop can be run by hand.

## Backend brief (docs/BACKEND_BRIEF.md)

### Part A: what the survey found

- **Two versions of the iOS app.** `main` calls `agent1/ingest`, `agent2/checkpoint`, `agent2/chat`, `agent2/feedback` and polls `agent2/pending-intervention` every 8 seconds, with no user ID. The teammate's branch `Reel-Watcher-Agent` sends an `X-Demo-User-Id` header, builds the checkpoint digest in the app from `GET timeline`, posts `feedback` and `session/end`, and no longer polls the server. The owner chose to match the teammate's branch.
- **The app has no value-card, inbox or values screen**, and never calls a resume route. Its chat expects a `ChatMessage` (`id`, `sender: "coachAgent"`, `text`, `timestamp`). Its "goal" is free text kept on the phone and sent as `stated_goals`.
- **The app notifies every N reels** (default 15, setting 3 to 50), whatever the number of conflicts. There is no conflict threshold or cooldown.
- **A teammate has built the Judge server** (`watcher_Agent/` on `Reel-Watcher-Agent`): FastAPI, PostgreSQL, Grok by default. It serves upload, timeline, feedback and session end, and includes `JudgeDataSource`, an implementation of Guardian's `DataSource`. It has no chat, inbox, memory or status routes and no Reflector trigger. It reads approved values with a direct SELECT on LangGraph's Postgres store.

Decisions that followed: Postgres instead of MongoDB; no second backend or Judge; a Guardian router that the teammate's server includes; a text fallback for value cards.

### Part B: storage on Postgres

Built: `guardian/storage.py` (in memory or Postgres, chosen from the environment), `guardian/memory/locks.py` (in-process locks, or Postgres advisory locks), and `python -m guardian.storage setup`.

- **Selection.** `GUARDIAN_DATABASE_URL` if set, otherwise `DATABASE_URL`; with neither, in memory. `api.configure(datasource=...)` picks storage itself; passing a store and checkpointer still works.
- **What persists.** Everything: LangGraph's `PostgresStore` holds values, proposals, inbox cards, review records, counters, chat log, notes and the Reflector's files; `PostgresSaver` holds chat threads, including a run paused on a value card.
- **Guarantees across processes.** Creating and deciding a proposal take a per-user advisory lock, so a card is decided once and value IDs stay unique under concurrent approvals. Saving a note takes one too. A Reflector run takes a non-blocking per-user lock, on top of the `running` review record. Restore-on-failure works through the store as before.
- **Per-user isolation.** Every listing now filters on the exact namespace, so a user ID containing `_` (a wildcard in SQL `LIKE`) cannot match another user's records.
- **Secrets.** Connection strings and their passwords are redacted from anything logged. Commits are scanned for them as well as for the API key.
- **The Judge's values query finds what Guardian stores.** Pinned by a test that runs the Judge's exact SELECT. The layout is written up as a compatibility contract in `docs/GUARDIAN_BACKEND_HANDOVER.md`.

Results: every existing storage test runs on both backends through one parametrized fixture: 521 tests pass with `TEST_DATABASE_URL` set (a local Postgres 16 in Docker), 316 in memory without it. Six Postgres-only tests cover locks across two connections, the Judge's SELECT, wildcard isolation, decide-once from two processes, concurrent value IDs, and a restart (an approved value and a pending card are both still there after everything is rebuilt, and the card can then be approved).

State of the team database: a Tiger Data instance (Postgres 18.6) created by the Guardian owner on Nov 14. `python -m guardian.storage setup` created Guardian's six tables in `public`. A check with a throwaway user passed: a value approved through `chat` and `resume` came back from `get_values`, the Judge's SELECT returned the same text, and the user's rows were then deleted. The teammate points their server at this database and runs their own migrations; one role for the hackathon.

### Part C: the Guardian router

Built: `guardian_router/`, outside `guardian/`, replacing `dev_server/`. A FastAPI router the Judge server includes with one line, a standalone app on seed data, and `review_sessions(user_id)` for the session-end hook. During this part the owner merged the teammate's Judge server into `guardian` (PR #7), so the router could be tested inside it.

- **JSON routes:** `/chat`, `/chat/resume`, `/status`, `/memory`, `/questions`, `/questions/{id}/answer`. One Guardian function each, result unchanged. The user comes from `X-Demo-User-Id`, default `demo-user`.
- **`POST /agent2/chat`** returns the `ChatMessage` the app decodes. The adapter turns the Guardian's structured responses into chat text:
  - A pending value card is plain text ending "Reply approve or reject." The decision is parsed in code (exactly approve / yes / reject / no, any case) and applied through `resume` or `answer_question`. Anything else shows the card again.
  - Inbox cards are presented at the start of a turn, one at a time, oldest first. A "What I learned" card is shown and dismissed; the next message goes to the Guardian with `entry="learned"`. A value card waits for approve or reject.
  - The digest becomes a drift-notification entry with the IDs in `flagged_reels`.
  - `profile.stated_goals` is passed as background during onboarding.
- **Configuration is lazy.** The router reads `app.state.guardian_datasource` on the first request and calls `api.configure(datasource=...)`. Every route is a plain `def`, so the Judge's data source is always called from a worker thread.
- **Reviews** start in a background thread on each `/agent2/chat` and `GET /questions`, and through `review_sessions`. One per user at a time in the process, on top of the package's own lock.

What the survey of the app's code settled:

| Question | Finding | What the router does |
| --- | --- | --- |
| Are the digest's `flagged_reels` IDs the reel IDs `JudgeDataSource` knows? | Yes. Both are the Judge's reel record ID: the app copies `id` from `GET /timeline`. But Swift encodes a UUID in upper case and the Judge's records are lower case | Lower-cases UUIDs. Confirmed by a test inside the real Judge app |
| When does the app attach the digest? | To every message after a notification, not only the first | Each digest is explained once |
| What is `stated_goals` when the user typed nothing? | The app's default, "Low-Stress Feed" | Ignores that string |
| How long does the app wait for a reply? | 12 seconds, then "Coach unavailable. Please try again." | If the user sends the same message again, returns the reply they missed. Asked the teammate to raise the timeout |

Changes inside `guardian/` that Part C needed:

- `api.pending_card(user_id, thread_id)`: a read-only look at the card waiting on a thread. An addition to the spec's entry points.
- `ChatContext` gained an optional `stated_goals`. The goals are put in the CONTEXT block and recorded once with the user's chat messages, so a quote from them passes the quote check.
- **Feedback comments from the app no longer count as the user's words** (`source="app"`), because the app sends a preset correction. The session-review skill says so. The seed feedback has source "app", so in the seed data only chat messages can be quoted now.
- **Rate limiting.** The Reflector has its own limiter at half the chat rate, so a review cannot make a chat reply wait. Both limiters now start full: LangChain's starts empty, which made the first call wait 5 seconds.
- `guardian/judge_prompt.md` says a line reading "none" has nothing to match.

Choices made where the brief was silent:

- The message that arrives when an inbox value card is shown is held, and answered by the Guardian once the cards are decided, so it is not lost.
- After a "What I learned" card, the next message goes to the Guardian before any further inbox card, because that card ends with a question.
- A card with suggested wording says "reply reject and tell me how you would say it", since the fallback has no editing.
- No review is started for a user with no approved value.
- The standalone app also serves `GET /health` and `POST /session/end`. It has no timeline or feedback route.
- What has been shown to whom is kept in the process only. After a restart a card is shown again before it can be decided.

Results: 60 router tests, including 3 that run the router inside the real Judge app on its fakes; 381 offline tests in all, 597 with Postgres.

Live run through HTTP on seed data (`python -m guardian_router.live_run`, Flash-Lite), run twice, 8 of 8 checks both times:

| Step | Run 2 (final code) |
| --- | --- |
| Onboarding from the stated goal | The Guardian opened from the goal and proposed a card on the second message, quoting the goal under `In my words` |
| Approve by text | "Approve" stored V1; `/status` turned true |
| Digest explanation | Both flagged reels explained, citing V1 and quoting each transcript |
| Disagreement | Recorded; the Guardian asked what made the reel fine |
| Review | Started by the chat turn; cards were in the inbox 150 seconds after `/session/end` |
| Inbox through chat | A refinement of V1 (an "Unless" line for injury rehab), then a new hustle value with suggested wording, then the "What I learned" card ending with a question |
| Approve, values changed | V1 updated; V2 stored with `Agreed wording` |

Chat replies took 0.7 to 6.9 seconds in run 2. In run 1, before the limiters started full, they took 4.5 to 15.1 seconds, and two would have passed the app's 12-second timeout. Run 1's review queued one value card (the V1 refinement) and not the hustle value; run 2's queued both.

Seen in these runs and left for 4b: the Reflector's V1 refinement also rewrote the title, the strength and the "Want more" line, not only "Unless".

Found in Part C and fixed during integration: the evidence check and empty sessions (below). Their Judge prompt and the dropped `ocr_text` are covered in `docs/GUARDIAN_FOR_BACKEND_OWNER.md`.

### Integration (after Part C)

The teammates handed integration to the Guardian owner, who allowed edits to `watcher_Agent/` for the wiring only.

- **Wiring, three small edits in `watcher_Agent/`.** `judge/api/app_api.py` includes `guardian_router`. `POST /session/end` runs `review_sessions(owner_id)` as a background task. The Judge prompt gained one rule: conflict when a reel matches a "Want less" line and no "Unless" applies; aligned when it matches "Want more"; a line that says "none" matches nothing.
  - The router is included with `include_in_schema=False`. One of the Judge's tests pins its OpenAPI document to the Judge-owned routes and asserts `/chat` is absent; this keeps that test unchanged.
  - `review_sessions` does nothing until the router has configured the Guardian (on its first request). Otherwise the Judge's own tests, and a session that ends before any Guardian request, would run it unconfigured.
  - The project rule `.claude/rules/project/judge-backend.md` says the Judge never invokes the Guardian or Reflector. The session-end hook now does, in the HTTP layer only, at the owner's instruction. The rule text has not been changed.
- **Team database.** pgvector 0.8.7 is available. `migrate.py up` applied 0001 to 0006. `grant` and `seed_dev_values.py` were not run.
- **`docs/RUN_DEMO.md`:** the `.env` lines, the one command, the tunnel, a curl smoke test and the likely failures. The curl block was run as printed against a local in-memory server.
- **Reset command:** `python -m guardian_router.reset_demo --user NAME [--yes]`. Without `--yes` it lists row counts and deletes nothing. It matches the user on exact text, never a LIKE pattern. Tested on the test container only (11 tests), never run on the team database.
- **Smoke test on the team database, 8 of 8.** The real combined server with the run page's settings, except two: `PROVIDER_MODE=fake`, because `.env` has no Judge provider key (neither `XAI_API_KEY` nor `GEMINI_API_KEY`), and a throwaway user `smoke-test-user` instead of `demo-user`, so the demo user stays empty. Onboarding and approval through `/agent2/chat` (real Gemini), a reel through `/agent1/ingest`, the reel in `/timeline` judged as a conflict citing V1 (the Judge read the value Guardian had just stored), and the digest explanation in chat. Chat replies took 1.0 to 1.7 seconds. That user's rows are still in the team database.
- **Two gaps fixed.**
  - *UUID evidence IDs.* The evidence check now recognises UUIDs as well as seed-style IDs, so a cited reel, feedback or session ID from the real Judge must exist. "Sessions seen" counts UUID sessions too.
  - *Empty sessions.* A finished session with no judged reels is marked `skipped` and never run.

Tests: Guardian and router 400 in memory, 625 with Postgres; the Judge's suite 499, unchanged. Four of the Judge's Postgres-only tests fail on the local test container, before and after the wiring: they log in as a role without a password, which the container's password authentication rejects.

## Step 4b: reflection, an active Guardian, and the new interview

Built from `docs/GUARDIAN_REFLECTION_ADDENDUM.md` plus the owner's changes of Nov 14. No entry-point signature changed.

- **The Guardian speaks first.** `chat` with an empty message sends the model a CONTEXT block saying the user has just opened the chat. Nothing is added to the chat log. The Guardian's last reply on a thread is kept while it is unanswered and returned again on the next empty message, with no model call, unless the context names different reels or a different session card.
- **Prompt.** New item 0 (open the conversation) and item 4 (reflect); three new rules (respond before asking, ask and do not advise, one reflection question per reply). With nothing to follow up on, the opener names one value and asks whether it still fits.
- **Onboarding interview.** "At most three questions" is replaced: one question at a time until the Guardian knows what the user wants less of, a concrete recent example, the exception, and why it matters. Three to five questions, never more than six; straight to the card if the user says they are done. Notes are saved along the way. After an approval it asks whether there is anything else, for up to three values. It does not ask what the user wants more of.
- **Honest entries.** "none" on any line the evidence does not support, in the prompt and the skill. A refinement changes only the lines the evidence supports.
- **Three checks added in code,** because the live runs showed Flash-Lite breaking the rule in each case:

| Seen in a live run | Check |
| --- | --- |
| The Reflector's refinement of V1 rewrote the title and the strength | A Reflector refinement that changes the title or strength is refused |
| A refinement's Evidence line said "updated 2026-11-18", a date nobody gave | An agent may not write a date in an Evidence line; the approval date is written by code |
| The user said a value still fits, and a card arrived that only added their sentence to the Evidence line | A proposal that changes nothing but the Evidence line is refused. The prompt also says to ask before proposing after a reflection answer |

Results: 457 offline tests (700 with Postgres). Live through `/agent2/chat` on seed data (`python -m guardian_router.live_reflection`, Flash-Lite):

| Behaviour | What the live run showed |
| --- | --- |
| Speaks first | Onboarding: a greeting and a first question. With values: "How has V2 (News without outrage) been holding up in your feed lately?" |
| Interview | Four questions, one per message: what less, a recent example, the exception, why it matters. Then the card |
| "none" and "want less" | The card had "Want more: none"; the exception the user gave went on the "Unless" line; "In my words" was an exact quote |
| After approval | "Is there anything else you've noticed on your feed that you'd rather see less of?" |
| Notes as it goes | Four notes during onboarding, each with the user's exact sentence |
| Responds first, one reflection question | After the explanation and "Yes, that matches": one open question about what made the reels hard to scroll past. The answer was saved as a note in the user's words |
| Opens about a value, proposes nothing if it fits | The user said it still fits; no card |
| Refinement in chat | After a disagreement about a rehab reel: V1 with only "Unless" changed |
| Refinement by the Reflector | V1 with only "Unless" changed, the feedback IDs added to Evidence. Title, strength and the other lines as stored |
| Reopening | An empty message returned the last reply in 0.0 seconds |

Limits seen, not fixed:

- The turn that records a disagreement and proposes a refinement makes four or five model calls. Run back to back with other turns it took 20 seconds, because the chat rate limiter (12 calls a minute) had been used up. Paced like a person, with 8 seconds between messages, the same turn took 2.7 seconds and every reply took 0.6 to 2.7 seconds. `LIVE_PAUSE_SECONDS` sets the pace of the live script.
- Notes are saved for almost every onboarding answer, including ones that state the value itself, and a note's summary sometimes adds a word the user did not use ("draining"). The quoted evidence is always exact.
- One review queued only the V1 refinement and not a value for the three hustle marks; earlier runs queued both.

## Main merged in, and the redesigned app

`main` (the redesigned app under `AttentionAgent/`, with the backend connection restored) was merged into `guardian` on GitHub as PR #10. `ios-reconnect.patch` was removed in a commit of its own (`963857b`), because the merge commit already existed.

The app's calls to the server are the same five routes. Two things changed and are matched in `guardian_router/` (`6eb5fc7`):

- Its chat request now carries `conversation_memory`, the app's own saved messages. The router ignores it: the Guardian keeps the conversation itself, and text from the client must not be treated as what was said.
- Its timeout is 30 seconds, up from 12. A reply counts as missed only after 29 seconds.

The chat screen now opens empty and does not yet send the empty message that makes the Guardian speak first.

## Step 5: the learning evaluation

Built: `guardian/eval/learning_eval.py`, committed before its first live run. Full results, every run and the caveats: `guardian/eval/REPORT.md`.

| Stage | Matches | In-pattern |
| --- | --- | --- |
| 1. Baseline (initial values) | 16 of 20 | 4 of 8 |
| 2. With up to three feedback examples | 17 of 20 | 5 of 8 |
| 3. With the Reflector's two approved changes | 19 of 20 | 8 of 8 |

- With 20 reels a difference of one or two is within noise. Stage 3 is three reels above the baseline overall and four of eight in-pattern.
- **Stage 2 was run twice.** Run 1 gave 12 of 20 with 7 invalid verdicts: shown "The person marked it: disagree", the stand-in Judge answered with the feedback labels. The script's example format was changed to state the verdict each reaction was to, and run 2 gave 17 of 20. That change was made after seeing held-out results.
- **The Reflector step was run three times.** Run 1 hit the 25-call limit and was rolled back, queuing nothing. A traced diagnostic run completed in 14 calls and queued both cards. The retry in the script completed and queued the V1 refinement (only the "Unless" line changed) and a new hustle value; both were approved unchanged and stage 3 uses them.
- One stage-3 reply had malformed JSON and counts as no match; its verdict field would have matched.
- Not a blind test of the skill: see the caveat under Step 3 and in the report.

## Conventions

- Team lint config (`pyproject.toml`): black, isort, ruff, mypy. `guardian/ruff.toml` extends it and ignores only the line-length rule, for prompt text, user-facing messages and seed data.
- Live tests run only with `pytest -m live`, under any pytest config.
- One commit per step on `guardian`, pushed after each step. Merges to `main` are done by the owner.
- The API key lives in the root `.env`, which is git-ignored. Every commit is scanned for the key before it is made.
