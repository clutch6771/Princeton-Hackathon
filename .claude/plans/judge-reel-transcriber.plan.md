# Plan: Judge and Reel Transcriber (Python backend, Tiger Data PostgreSQL)

**Source**: Claude Code prompt "Judge and Reel Transcriber" (PRD baseline: Values-Aligned
Attention Companion, 2026-11-14). No `*.prd.md` exists in the repo, so this is an ECC
`/ecc:plan` conversational-mode plan saved here for durability.
**ECC**: plugin `ecc@ecc` 2.2.3 (project scope, already installed; not upgraded, not duplicated).
**Branch**: `Reel-Watcher-Agent`
**Complexity**: Large (about 45 source modules, 9 tables, 7 docs, about 150 tests)
**Status**: WAITING FOR CONFIRMATION. No application source is written until approved.

All examples in this plan are synthetic. No real values, media, transcripts, feedback,
identifiers or secrets appear here or in any committed fixture.

---

## 0. What was verified in the checkout (2026-11-14)

| Fact | Finding |
|---|---|
| Application code | None. `watcher_Agent/` holds only `README.txt`. No Python package, no tests, no MongoDB code to replace. |
| AGENTS.md / `*.prd.md` | None in the repo. |
| Tooling | `pyproject.toml` is tool config only (black, isort, ruff E/F/I/B/UP/S, mypy, pytest markers `unit`/`integration`, bandit). Pinned dev deps in `requirements-dev.txt`. `.venv` is Python 3.14.4. |
| Hooks | `.claude/hooks/post-edit-format-lint.sh` runs ruff, isort, black, mypy and a `print()` check on every edited `.py`. ECC GateGuard is active. |
| ECC commands | `/ecc:plan` exists and runs inline. Skills present: `tdd-workflow`, `python-testing`, `verification-loop`, `postgres-patterns`, `database-migrations`, `fastapi-patterns`, `security-review`. There is no `/ecc:tdd` command. Reviewer agents present: `python-reviewer`, `database-reviewer`, `fastapi-reviewer`, `security-reviewer`, `code-reviewer`, `silent-failure-hunter`. |
| Dependencies | A pip dry-run on Python 3.14 resolves everything below as wheels, including PyAV 19.0.2 (bundles FFmpeg, so no system ffmpeg is needed). |
| Local PostgreSQL | Homebrew `postgresql@17` installed, not running, **pgvector not installed**. No Docker. |
| PRD | The PDF in Downloads is an older snapshot (frames only, MongoDB default, 3 conflicts in 5 reels, 2-minute cooldown, `/observe`). The living artifact it links could not be opened from this account. The prompt's "latest PRD" statements therefore take precedence wherever they differ, as the prompt instructs. |
| Models (docs search, to be confirmed by a live call) | `gemini-3.5-flash-lite` is a stable ID that lists text, image, audio input and structured output. `gemini-embedding-001` supports `task_type` and `output_dimensionality` (768 / 1536 / 3072). `gemini-embedding-2` naming is inconsistent in the docs, so it is not the default. |

---

## 1. Requirements

### In scope
1. FastAPI backend under `watcher_Agent/`: ingest of sampled JPEG frames and app-audio AAC chunks, privacy gating, reel segmentation, safe audio assembly, one joint Gemini understand + transcribe + judge call per reel, validation, persistence, asynchronous embedding, ordered batch notification policy, outbox delivery through ingest responses, feedback storage, timeline and thumbnail reads, policy config, deletion hooks, health and readiness.
2. Real Gemini adapters and Tiger Data (PostgreSQL) adapters, explicit development fakes, SQL migrations.
3. Versioned platform-neutral contracts with synthetic fixtures.
4. Tests written first, 80%+ coverage, repository lint/type/security checks.
5. Documentation set (section 9), plus the two root documents requested on top of the prompt.

### Explicit exclusions
Guardian/chat agent, Reflector, onboarding, value proposals and approvals, hypotheses, briefs, session summaries, question queues, daily re-rating scheduler, any GUI, Share Extension, PiP, overlays, blocking, feed re-ranking, native iOS/Android code, `/share`, `/chat`, `/values`, conversational checkpointer, runtime Deep Agent for the Judge, MiniLM/vector(384), raw-recording offload, rolling per-reel evaluation, 120-second cooldown, App Groups dependency, any universal list of inflammatory topics.

Session completion writes one database lifecycle event. It does not call another agent.

### Interpretation of "ECC functionality baked into the application"
ECC is development tooling and is not a runtime dependency of the backend (the prompt states this). "Baked in" is implemented as:
- Router modules named `*_api.py` so the vendored ECC FastAPI rule (`**/*_api.py`) applies to them automatically.
- A path-scoped project rule `.claude/rules/project/judge-backend.md` holding the Judge invariants (no media on disk, no media in logs, values read-only, one understanding call) so ECC reviewers and future sessions enforce them.
- `pyproject.toml` gains pytest, coverage (`fail_under = 80`) and mypy path settings for the package; the existing post-edit hook is unchanged and covers every new file.
- `watcher_Agent/scripts/verify.sh` runs the ECC verification-loop phases for Python (ruff, black, isort, mypy, bandit, pytest with coverage).
- This plan lives at the ECC handoff path `.claude/plans/`.
If something else was meant (for example ECC agents invoked at runtime), say so before approval.

---

## 2. Patterns to mirror

| Category | Source | Pattern |
|---|---|---|
| Naming | none in repo | No similar code exists. Use ECC Python rules: snake_case modules, PascalCase types, `*_api.py` routers, `test_<behavior>` names. |
| Errors | `.claude/rules/ecc/common/coding-style.md` | Typed exceptions, fail fast at boundaries, sanitized error codes to clients, detail in server logs. |
| Logging | `.claude/hooks/post-edit-format-lint.sh` (print check) | `logging` only. Structured key/value records. Never media bytes, transcripts, values text or secrets. |
| Data access | `.claude/rules/ecc/common/patterns.md` | Repository pattern behind Protocols; parameterized SQL only. |
| Tests | `pyproject.toml` markers | pytest, `unit` and `integration` markers (adds `live`), Arrange-Act-Assert. |
| Immutability | `.claude/rules/ecc/common/coding-style.md` | Frozen pydantic models and frozen dataclasses; state transitions return new values. Volatile buffers are the one encapsulated mutable area. |
| Response format | `PROJECT_CONTEXT.txt` suggests a `{success,data,error}` envelope | Ingest and session-end responses keep `notify` at the response root as the prompt requires. This deviation is documented in `API_CONTRACT.txt`. |

---

## 3. Layout and files

```
watcher_Agent/
  README.txt                       UPDATE  scope, architecture, setup, run, verify
  requirements.txt                 CREATE  pinned runtime deps
  .env.example                     CREATE  names only, no secrets
  judge/                           CREATE  the Python package (import name: judge)
    config.py                      one typed Settings source (pydantic-settings)
    errors.py                      exception types and sanitized error codes
    capabilities.py                Protocols for every injected capability
    contracts/                     versioned pydantic models (v1)
      capture.py                   FrameObservation, AudioChunk, PrivacyProvenance
      candidate.py                 ReelCandidate, CoverageMetadata
      values.py                    ApprovedValuesSnapshot, ValueEntry
      feedback.py                  FeedbackExample, FeedbackLabel
      understanding.py             ReelUnderstandingResult, ReelFeatures, Verdict
      record.py                    ReelRecord, EvaluationStatus
      notification.py              NotificationEnvelope, IngestResponse
    runtime/                       clock.py, rate_limiter.py, media_budget.py, worker_queue.py
    privacy/eligibility.py         backend gate, dev-only permissive gate
    ingest/                        admission.py, clock_mapping.py, session_buffers.py
    segmentation/                  frame_hash.py, segmenter.py, frame_selector.py
    audio/                         codec.py (PyAV AAC decode), assembler.py, wav.py
    transcriber/                   transcript.py (schema, statuses), validation.py, coverage.py
    values/                        markdown_parser.py, reader.py (PostgresStore), cache.py
    feedback/retriever.py          bounded retrieval with a labeled mode
    understanding/                 prompt.py, gemini.py, validation.py
    embedding/                     gemini.py, indexer.py, profile.py
    notifications/                 policy.py, accounting.py, outbox.py, text.py
    pipeline/                      finalizer.py, judge_pipeline.py, timings.py, lifecycle.py
    persistence/                   pool.py, sessions.py, receipts.py, reels.py, thumbnails.py,
                                   embeddings.py, feedback.py, notifications.py, policy.py, deletion.py
    api/                           app_api.py (create_app), ingest_api.py, session_api.py,
                                   timeline_api.py, feedback_api.py, policy_api.py, health_api.py,
                                   deps.py, user_context.py, multipart.py
    devfakes/                      fake providers, in-memory repositories, fake clock (explicit, opt-in)
  migrations/                      CREATE  0001..0006 *.sql, migrate.py (runner), roles.sql
  contracts/v1/                    CREATE  *.schema.json exported from the models, fixtures/*.json
  scripts/                         CREATE  verify.sh, export_contract_schemas.py, seed_dev_values.py
  docs/                            CREATE  MEMORY_PARAMETERS, API_CONTRACT, PLATFORM_BOUNDARY,
                                           PRIVACY, DATABASE_INTEGRATION, PRD_TRACEABILITY (.txt)
  tests/                           CREATE  unit/, integration/, live/, conftest.py, media_factory.py
JUDGE_MEMORY_OUTPUTS.txt           CREATE  root: every memory output, where it goes, who uses it
JUDGE_WORKFLOW.txt                 CREATE  root: the Judge workflow, step by step
CLAUDE.md                          UPDATE  architecture facts, commands, doc pointers
PROJECT_CONTEXT.txt                UPDATE  current state, layout, integration decision
pyproject.toml                     UPDATE  pytest/coverage/mypy settings
requirements-dev.txt               UPDATE  add pytest-asyncio; include runtime requirements
.claude/rules/project/judge-backend.md   CREATE  Judge invariants for ECC reviewers
```

Native adapter documentation lives in `watcher_Agent/docs/`, outside the `judge` package. Nothing in `judge/` imports Swift, ReplayKit, AVFoundation, Android or notification APIs. A test asserts that no module in `judge/` imports a platform package.

### Runtime dependencies (pinned in `watcher_Agent/requirements.txt`)
`fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `python-multipart`, `psycopg[binary,pool]`, `pgvector`, `av` (PyAV), `pillow`, `numpy`, `langchain-google-genai` (brings `google-genai`), `langgraph`, `langgraph-checkpoint-postgres`, `httpx`. Dev adds `pytest-asyncio`.

One driver (psycopg 3) serves the repositories and the LangGraph Postgres store. Migrations are plain SQL with a small checksummed runner; Alembic would add SQLAlchemy for no benefit here.

---

## 4. Typed contracts (version `1`)

Every contract carries `contract_version`. Timestamps on the wire are integer epoch milliseconds from the capture device (`capture_*`); the server stamps `received_at` itself.

| Contract | Key fields |
|---|---|
| FrameObservation | `event_id` (UUID, client), `owner` (from header), `session_id`, `source_id`, `source_seq`, `capture_ts_ms`, `mime_type=image/jpeg`, `width`, `height`, `privacy` (provenance), JPEG bytes (volatile only) |
| AudioChunk | `event_id`, `session_id`, `source_id`, `source_seq`, `capture_start_ms`, `capture_end_ms`, `codec=aac`, `container` (`adts` default, `mp4` allowed), `sample_rate_hz=16000`, `channels=1`, `priming_samples`, `eligible_intervals[]`, `privacy`, bytes (volatile only) |
| PrivacyProvenance | `gate_id`, `gate_version`, `eligibility` (`eligible`/`ineligible`/`unknown`), `surface` (`instagram_reels`), `method`, `capability` (`native_gate`/`dev_fake`/`none`) |
| ReelCandidate | `candidate_id`, `session_id`, `order_seq`, `start_ms`, `end_ms`, `boundary_uncertainty_ms`, selected frames, PCM segments, `coverage`, `segmentation_status`, `first_second_observed` |
| CoverageMetadata | observed seconds, frame count, audio seconds kept, audio seconds discarded by reason (boundary guard, uncertified, gap, corrupt), gaps |
| ApprovedValuesSnapshot | `revision`, `content_sha256`, `content` (Markdown), `entries[]` (id, title, strength, want_more, want_less, unless, in_my_words, evidence, approved_at, superseded/deleted), `loaded_at` |
| FeedbackExample | `feedback_id`, `reel_id`, `label`, `comment`, reel description excerpt, prior verdict, `values_revision`, `retrieval_mode` |
| ReelUnderstandingResult | `transcript`, `transcript_status`, `on_screen_text`, `description`, `features`, `verdict`, `confidence`, `value_ids`, `reason`, `evidence_basis` |
| ReelRecord | PRD fields plus the operational extension (section 5) |
| NotificationEnvelope | `title`, `body`, `reel_ids`, `notification_id`, `expires_at` |

JSON Schemas are exported from the models to `contracts/v1/`. A test fails if the committed schema drifts from the models.

### Clock and ordering rules
- Capture timestamps align media: frames to audio, audio to reel windows. They are never compared with server time directly.
- Server time governs receipt, idle expiry (600 s), cooldown, leases, deadlines.
- Per session the server keeps `offset = min(received_at - capture_ts)` over accepted events and uses it only to express "observed reel end" on the server clock for latency measurement. Estimates are labeled approximate.
- Order within a session is `(capture_ts, source_seq)`. Duplicate `event_id` returns the original acknowledgment with `duplicate: true` and does no work. An event older than the session's closed watermark minus a 3 s grace is acknowledged as `late_discarded` and its bytes are dropped. A reconnect with the same `session_id` and `source_id` continues the session; a different `source_id` is rejected with 409; an ended session is rejected with 409.

---

## 5. Tiger Data schema and migration plan

Application tables live in schema `judge`. The shared memory Store table is created by the LangGraph Postgres store's own setup, run by the owner role.

| Table | Purpose and key columns |
|---|---|
| (shared) `store` | Logical memory. Judge reads namespace `(user_id, "memories")`, key `values.md`. **SELECT only** for the Judge role. |
| `judge.sessions` | `id` (client UUID), `owner_id`, `source_id`, `status` (active/ended), `started_at`, `last_ingest_at`, `ended_at`, `end_reason`, `next_order_seq`, `accounting_cursor`, judged / conflict / excluded / failed counts |
| `judge.session_events` | Lifecycle outbox for future consumers: `session_completed` with counts. Written once per session. Nobody is invoked. |
| `judge.ingest_receipts` | `event_id` PK, owner, session, kind, `source_seq`, capture timestamps, `received_at`, byte length, outcome. Metadata only. |
| `judge.reels` | PRD fields + `owner_id`, `order_seq`, `evaluation_status`, nullable `verdict`, coverage JSON, `audio_status`, `transcript_status`, `segmentation_status`, `values_revision`, `values_sha256`, `feedback_example_ids`, `retrieval_mode`, `model_id`, `prompt_version`, `understanding_attempts`, `idempotency_key` (unique), `timings` JSON, `latency_overrun`, `error_code`, `batch_id`, `batch_position` |
| `judge.reel_thumbnails` | `reel_id` PK/FK, owner, `jpeg` BYTEA with a size CHECK, sha256, dimensions. At most one per reel by construction. |
| `judge.reel_embeddings` | PK `(reel_id, profile_id)`, model, dimension, task, `embedding vector(768)` nullable, `status` (pending/indexed/failed), attempts, error code |
| `judge.feedback` | id, owner, `reel_id` FK, `label` (agree/disagree/not_for_me/rerate), nullable `score`, `comment`, `source`, client idempotency key |
| `judge.notification_policy` | PK `(owner_id, version)`, `batch_size`, `minimum_conflicts`, `cooldown_seconds`, CHECK `1 <= minimum_conflicts <= batch_size` |
| `judge.notification_batches` | session, `batch_index` (unique per session), `policy_version`, `batch_size`, status (open/evaluated), judged / conflict / excluded counts, `outcome` (triggered / below_threshold / suppressed_cooldown / suppressed_pending) |
| `judge.notification_outbox` | `notification_id` PK, owner, `batch_id` unique, payload JSON, status (pending/leased/acknowledged/expired/cancelled/unacknowledged), `expires_at`, `lease_until`, `dispatch_attempts`, `dispatched_at`, `acknowledged_at` |
| `judge.user_notification_state` | One row per owner: `last_dispatch_at`. Locked `FOR UPDATE` to serialize cooldown decisions. |

`evaluation_status` values: `pending`, `judged`, `insufficient_evidence`, `values_unavailable`, `failed_provider`, `failed_validation`, `failed_timeout`, `skipped_overload`, `lost_on_restart`. Only `judged` rows carry a verdict. This nullable-verdict extension is documented; `neutral` keeps its PRD meaning.

Rules: foreign keys with `ON DELETE CASCADE` from reels to thumbnails, embeddings, feedback; unique constraints for idempotency; every query filters by `owner_id`; `statement_timeout` set per connection; TLS required (`sslmode=require`) outside local development.

Roles: `judge_migrator` owns the schema and runs migrations and Store setup. `judge_runtime` gets DML on `judge.*` and `SELECT` only on the Store table. The values reader passes `refresh_ttl=False` and never calls `setup()`. An integration test connects as the runtime role and proves that reading succeeds and that any write to the Store table is denied. If the library still attempts a write on read, the adapter falls back to one parameterized `SELECT` on the same table behind the same interface.

pgvector: migration `0004` runs `CREATE EXTENSION IF NOT EXISTS vector` under the migrator role and fails with a clear message if the extension is unavailable. Readiness reports the installed version. Search is exact (no ANN index) initially; 768 dimensions is under pgvector's 2000-dimension index limit if one is added later. No hypertables, no pgvectorscale.

Not created: any raw-media column, recording archive, GridFS substitute, offload threshold, or media retention timer.

Deletion: `delete_reel`, `delete_session`, `delete_owner_data` services (owner-scoped, transactional) remove dependent thumbnails, vectors and feedback by cascade, cancel pending outbox items that reference deleted reels, and invalidate the in-process values and feedback caches. Exposed as `DELETE /reels/{reel_id}` and `DELETE /data`.

---

## 6. Processing design

### Volatile media lifecycle
- Transport is `multipart/form-data`: one JSON `metadata` part, one binary `media` part. No base64.
- A hard request cap (400 kB) is enforced from `Content-Length` before the body is parsed. The cap is below the multipart parser's 1 MB disk-spool threshold, so no upload ever reaches a temporary file. A test asserts this.
- Frames: only the representative selection (at most 3) and one thumbnail candidate are kept per open candidate. Audio is decoded to PCM16 at ingest (in a worker thread) and held as timestamped segments in a bounded per-session buffer.
- A process-wide `MediaBudget` counts bytes held. Over budget, ingest answers 503 `overloaded` with `Retry-After`; the oldest waiting candidate is finalized `skipped_overload`. Nothing is offloaded to the database or disk.
- Buffers are released in `finally` on success, failure, timeout, cancellation and shutdown. Tests assert the budget returns to zero on each path.
- The service must run as **one process** (one uvicorn worker), because session buffers are in-process. Startup marks any `pending` reel left by a previous process as `lost_on_restart`.
- Startup refuses to run in production mode if LangSmith/LangChain tracing is enabled, since SDK tracing would export prompt media.

### Privacy gates
- Native gate (future adapter, documented only) decides eligibility before upload and certifies audio intervals.
- Backend gate (implemented): accepts a frame only when provenance says `eligible`, surface is Instagram Reels, and the gate capability is a native gate. Audio is kept only inside the intersection of the chunk's certified `eligible_intervals` and the reel window. Everything else is dropped before any provider request is built.
- `PRIVACY_MODE=enforce` is the default. `dev_permissive` accepts `dev_fake` provenance and is refused unless `APP_ENV=development`. In enforce mode, uploads without a native capability get 422 `privacy_capability_unavailable`.
- Backend filtering cannot undo an upload of private media; `PRIVACY.txt` states which guarantees depend on the native adapter.

### Segmentation
- Per frame: a 64-bit difference hash of the whole frame and one of the lower overlay band (creator and caption area).
- A boundary needs a sharp whole-frame change **and** corroborating evidence: an overlay-band change, a native transition hint, or a frame gap above 8 s. A whole-frame change alone is treated as an in-reel cut.
- The true boundary lies between the last frame of reel A and the first frame of reel B. That interval (up to 2 s) is recorded as `boundary_uncertainty_ms`, and its audio is discarded as a boundary guard, so no cross-boundary audio is assigned to either reel.
- A candidate closes on a boundary, at session end, at the 90 s exposure cap (marked truncated), or on a frame gap.
- `first_second_observed` is false unless a frame exists within the first second of the exposure; the prompt tells the model not to describe a first-second hook when it is false.

### Final audio and finalization
A closed candidate waits for audio only until the session's audio watermark passes the candidate's audio end, or 6 s of server time, whichever comes first. The wait is a scheduled check in the worker, never an open HTTP request. On expiry it proceeds with partial coverage.

### Audio assembly
Each AAC chunk is decoded independently through the codec adapter (ADTS frames are self-delimiting; priming samples are trimmed using chunk metadata). PCM is cut by capture timestamps to the permitted intervals and joined into one in-memory 16 kHz mono WAV. AAC bytes are never concatenated. Corrupt chunks yield `audio_status=corrupt` for their interval.

### Approved context
- `ValuesReader` returns one immutable snapshot or a typed unavailable reason (missing, invalid, hash mismatch, unapproved, over cap, store error). A 16 kB cap rejects the whole snapshot; nothing is truncated.
- Unavailable values: the candidate waits in RAM (bounded: 20 s, polled every 5 s). On expiry it is stored as `values_unavailable`, verdict null, no batch contribution, media discarded.
- Feedback retrieval: the new reel's text does not exist before the understanding call, so v1 uses a labeled fallback, `retrieval_mode=recent_explicit_feedback`: the most recent explicit feedback items (at most 3, one per reel, owner-scoped, tied to still-existing reels). Zero examples is valid. No preliminary captioning or extra per-reel embedding is added. The retriever interface allows a value-concept-vector mode later.

### Understanding call
- One Gemini call through `UnderstandingProvider` with 2 to 3 frames, the WAV clip, the values Markdown and up to three examples, returning one structured object.
- Values, examples, and anything the model reads or hears are placed in delimited data sections; the system instruction states they are data. The model has no tools. The output schema has no field that could change configuration or values.
- Adapter: `ChatGoogleGenerativeAI.with_structured_output`. Milestone 7 starts with a capability check; if the pinned integration cannot combine images, audio and structured output, the same adapter interface is backed by `google-genai` directly.
- One retry only for transient transport errors or 429/5xx, within the job deadline. Schema or validation failure is terminal (`failed_validation`): no repair loop, no second judging call. `understanding_attempts` records real attempts, including unknown outcomes after timeouts.
- Missing provider configuration fails at startup in production mode. Fakes are selected only by an explicit `PROVIDER_MODE=fake`.

### Validation
Schema, verdict enum, finite confidence in [0,1], `value_ids` must exist in the pinned snapshot, a conflict or aligned verdict must cite at least one value and a non-empty user-relative reason, transcript status must be consistent with audio coverage (for example no `speech` when no audio was supplied; OCR text never copied into the transcript field), and speech-dependent conclusions are rejected when audio was unavailable.

Transcript statuses: `speech`, `non_speech_music`, `silence`, `missing_audio`, `protected_unavailable`, `corrupt`, `unintelligible`, `partial`. `protected_unavailable` requires native evidence, never silence alone. Transcript timestamps, if present, are seconds from the start of the assembled clip and are approximate because discarded intervals are removed.

### Persistence, accounting, notification
- Closing a candidate inserts a `pending` reel row with its `order_seq` (metadata only). Completion updates it to a terminal state in one transaction that also stores the thumbnail, advances the session accounting cursor over contiguous terminal reels in capture order, appends judged reels to the open batch, and, when the batch is full, evaluates it and writes the outbox item.
- A job deadline (30 s) turns a stuck `pending` reel into `failed_timeout`, so the cursor can never stall.
- Batch evaluation happens once: `conflict_count >= minimum_conflicts` and no cooldown and no pending notification creates one outbox item with a 120 s expiry; otherwise the batch is recorded as below threshold or suppressed. Suppressed batches are never sent later. Policy changes take effect when the next batch opens.
- Dispatch on any ingest or session-end response: process acknowledgments, lock the owner's notification state, recheck cooldown and expiry, lease the item (30 s), set `dispatched_at` and `last_dispatch_at`, return it at the response root. An unacknowledged item is returned again with the same ID after its lease lapses, at most 3 times, until it expires. Redelivery does not restart the cooldown.
- Embedding runs after the commit as a separate idempotent upsert. Failure never affects the verdict. Retries read stored text only.

### Latency
Stage timings (final-audio wait, queue, context load, inference, validation, DB) are stored per reel. `latency_overrun` is set when observed-reel-end to commit exceeds about 10 s. The older PRD's 4-second figure is superseded by the prompt.

---

## 7. Engineering settings chosen here (not PRD values)

| Setting | Value | Note |
|---|---|---|
| Understanding model | `gemini-3.5-flash-lite` | Confirm with a live call; pinned by explicit ID, never an alias |
| Embedding profile | `gemini-embedding-001`, 768 dims, `SEMANTIC_SIMILARITY`, profile `v1` | Dimension mismatch is rejected; profiles are never mixed in one query |
| Frame upload cap | 300 kB | Native suggestion: JPEG quality about 0.6 |
| Audio chunk | AAC-LC about 32 kbps, ADTS, at most 6 s, 100 kB | |
| Thumbnail | 320 px long edge, quality 70, at most 48 kB, metadata stripped | |
| Request cap | 400 kB | Below the 1 MB spool threshold |
| Values cap | 16 kB | Over cap means unavailable, never truncated |
| Example excerpt cap | 600 characters each | Whole example dropped if over, never cut mid-clause |
| Output budget | 1024 tokens; transcript at most 4000 characters | |
| Python media budget | 256 MB process, 24 MB per session | Explicit 503 and `skipped_overload` when exceeded |
| Candidate duration | at most 90 s | |
| Queue / workers / sessions | 32 / 2 / 4 | |
| Segmentation thresholds | frame hash 20 of 64 bits, overlay band 12 of 64, gap 8 s | Untested on real Instagram; tunable |
| Late-event grace / final audio wait | 3 s / 6 s | |
| Provider timeout / attempts | 8 s / 2 | |
| Job deadline | 30 s | |
| DB statement timeout / pool | 5 s / 1 to 8 | |
| Provider rate limit | 10 requests per minute default | Free-tier quota must be checked in the Google console; no paid fallback |
| Embedding retries | 5 with backoff | |
| Notification expiry / lease / attempts | 120 s / 30 s / 3 | |
| Values wait | 20 s, poll 5 s | |
| Receipt retention | 24 h, pruned at session end | Reel text and metadata are kept until owner deletion |

PRD defaults (frame interval 2 s, 512 px, 16 kHz mono AAC, 5 s chunks, 50 MB native cap, batch 15, minimum conflicts 3, cooldown 600 s, idle timeout 600 s, at most 3 examples, one call, one embedding, one thumbnail) are listed separately in `MEMORY_PARAMETERS.txt` and marked as PRD requirements.

---

## 8. Milestones (each is tests first, then implementation)

| # | Milestone | Validate |
|---|---|---|
| M0 | Scaffold: requirements, pyproject settings, `config.py`, `errors.py`, contracts, schema export, project rule, `verify.sh` | Contract and config tests; schema drift test |
| M1 | Runtime primitives (clock, budget, queue, rate limiter), privacy gate, admission, clock mapping, session buffers | Payload, duplicate, out-of-order, late, reconnect, overload, cleanup tests |
| M2 | Frame hashing, segmenter, frame selector, AAC codec adapter, audio assembler | Chunk across two reels; Reel-to-DM chunk; short, sparse, uncertain exposures; corrupt audio |
| M3 | Values parser and reader interface, feedback retriever, prompt builder, result validation, transcriber | Missing/invalid values, exception clauses, revision pinning, injection cases, audio status matrix |
| M4 | Migrations, pool, repositories, deletion | PostgreSQL integration tests, role permission test |
| M5 | Finalizer, judge pipeline, accounting, policy, outbox, lifecycle | One call and one embedding; 3 of 15 triggers, 2 does not; cooldown; ordering; concurrency; lost responses |
| M6 | API routers, user context, embedding indexer, policy API, health | HTTP tests through both ingest endpoints, session end, timeline, thumbs, feedback, OpenAPI |
| M7 | Real Gemini adapters, Postgres Store values reader, opt-in live smoke tests | Adapter tests with stubbed transport; live tests only when keys are present |
| M8 | Docs, root documents, README / CLAUDE.md / PROJECT_CONTEXT updates, ECC reviewers, full verification | `watcher_Agent/scripts/verify.sh`, coverage at least 80% |

Order favors a demonstrable path early: after M5 the full pipeline runs end to end on fakes.

After M7 the ECC reviewers named in the prompt (python, database, fastapi, security, silent-failure) are run on the diff and CRITICAL/HIGH findings fixed. Database review guidance is applied to plain PostgreSQL; nothing Supabase-specific is introduced.

---

## 9. Documentation deliverables

| File | Content |
|---|---|
| `JUDGE_MEMORY_OUTPUTS.txt` (root) | Short registry of everything the Judge writes: table, fields, who reads it (Guardian, Reflector, app), what it is for, lifetime. Also what the Judge reads and never writes. |
| `JUDGE_WORKFLOW.txt` (root) | The 12-step workflow with states, deadlines and failure outcomes. |
| `watcher_Agent/README.txt` | Scope, architecture, setup, configuration, run, verification. The existing ECC change log is kept. |
| `watcher_Agent/docs/MEMORY_PARAMETERS.txt` | Authoritative registry, categories A to F, PRD defaults versus engineering settings. |
| `watcher_Agent/docs/API_CONTRACT.txt` | Endpoints, encodings, examples, errors, idempotency, acknowledgment, demo-header caveat. |
| `watcher_Agent/docs/PLATFORM_BOUNDARY.txt` | iOS and Android adapter contract, spike responsibilities, Screen Sharing notification setting. |
| `watcher_Agent/docs/PRIVACY.txt` | Gates, audio intervals, volatile retention, proxy buffering, SDK tracing, provider-side processing, deletion. |
| `watcher_Agent/docs/DATABASE_INTEGRATION.txt` | The 12-step Tiger Data tutorial, MongoDBStore replacement, PostgresSaver as a future seam only. |
| `watcher_Agent/docs/PRD_TRACEABILITY.txt` | Requirement to module, tests, docs; PRD versus interpretation versus engineering setting versus deferred. |
| `CLAUDE.md`, `PROJECT_CONTEXT.txt` | Integration decision (FastAPI over HTTPS), layout, commands, pointers to the documents above. |

---

## 10. Validation commands

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check watcher_Agent && .venv/bin/black --check watcher_Agent && .venv/bin/isort --check watcher_Agent
.venv/bin/mypy watcher_Agent/judge
.venv/bin/bandit -c pyproject.toml -r watcher_Agent/judge
.venv/bin/pytest watcher_Agent/tests -m "unit" --cov=judge --cov-report=term-missing
TEST_DATABASE_URL=postgresql://... .venv/bin/pytest watcher_Agent/tests -m integration
GEMINI_API_KEY=... .venv/bin/pytest watcher_Agent/tests -m live      # opt-in
```

Integration tests need PostgreSQL with pgvector. They skip with an explicit reason when `TEST_DATABASE_URL` is unset, and the final report says whether they ran.

---

## 11. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Segmentation thresholds wrong on real Instagram frames | High | Thresholds are configuration; uncertainty recorded per reel; needs a device recording to tune (not available here) |
| Native eligibility gate does not exist yet, so enforce mode rejects all real uploads | High | `dev_permissive` for development only; documented dependency on the platform owner |
| Pinned LangChain integration cannot mix audio, images and structured output | Medium | Capability check first in M7; `google-genai` fallback behind the same interface |
| Model ID or free-tier quota differs from the docs | Medium | IDs are configuration; live smoke test; rate limiter; no paid fallback |
| Values payload format not yet agreed with the memory owner | Medium | Adapter accepts the agreed envelope and fails closed otherwise; format documented for the Guardian owner |
| Store library writes on read under a SELECT-only role | Low | `refresh_ttl=False`; integration test; direct SELECT fallback |
| pgvector missing locally or on the Tiger service | Medium | Migration fails clearly; tutorial covers enabling it |
| Single-process requirement overlooked in deployment | Medium | Startup log, README, readiness output |
| Scope versus the submission deadline | High | Milestone order gives an end-to-end path on fakes after M5 |
| Fake-based tests mistaken for device verification | Medium | Final report lists unverified items: Instagram audio capture, native memory, Gemini quality, banners |

---

## 12. Acceptance

- [ ] All milestones complete, tests written before implementation
- [ ] Every acceptance case in section 12 of the prompt has a named test
- [ ] Lint, format, type, security and pytest checks pass; coverage at least 80%
- [ ] No raw media in PostgreSQL, files, logs or traces (tested)
- [ ] One understanding call and one embedding per reel on the normal path (tested)
- [ ] Docs in section 9 written; root documents named as listed
- [ ] Final report states real commands, results, and what remains unverified
