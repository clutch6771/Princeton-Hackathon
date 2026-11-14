WATCHER / JUDGE BACKEND
=======================
Current integration: 2026-11-14. Product requirements: ../docs/project.prd.md.
Guardian interfaces: ../docs/GUARDIAN_SPEC_V5.md.

Responsibilities
----------------
Interpret externally supplied visual and audio evidence against approved
values, validate the result, and store reel records for the app and Guardian.
The Judge has no capture tools, notification policy, scheduler, outbox or
notification delivery. Capture and local notifications live in AttentionAgent/.
Guardian code is independently owned and unchanged by this integration.

Two input paths share ONE judging pipeline:
  POST /ingest/frame + /ingest/audio
    Sampled JPEGs and independent AAC chunks. Server segmentation, bounded
    audio assembly, one judgment call. Grok uses a separate xAI STT call for the audio;
    Gemini mode can transcribe/judge jointly.
  POST /agent1/ingest
    Current iOS ReelTracker's completed reel: one JPEG and optional device
    speech transcript. Uses native boundaries without segmenting again.
    Device transcript coverage is unknown: stored as partial; raw audio is
    missing, never invented. No extra server ASR call. This compatibility
    path is less complete than the PRD's 2-3 frames plus AAC path.

Both paths use the same values, feedback examples, provider, queue, output
validation, persistence, thumbnails, embeddings and session accounting.
Neither returns notify. Missing values or provider failures produce a null
verdict and an explicit evaluation_status, never a fabricated neutral verdict.

Run from repository root
------------------------
  python3 -m venv .venv
  .venv/bin/pip install -r requirements-dev.txt
  cp watcher_Agent/.env.example watcher_Agent/.env
  # Fill DATABASE_URL, DEMO_USER_ID, XAI_API_KEY; apply migrations first.
  .venv/bin/python -m uvicorn judge.api.app_api:create_app --factory \
      --app-dir watcher_Agent --host 0.0.0.0 --port 8000 --workers 1

Explicit synthetic development (no credentials/database):
  APP_ENV=development PRIVACY_MODE=dev_permissive PROVIDER_MODE=fake \
  STORAGE_MODE=memory DEMO_USER_ID=demo-user \
  .venv/bin/python -m uvicorn judge.api.app_api:create_app --factory \
      --app-dir watcher_Agent --host 0.0.0.0 --port 8000 --workers 1

GET /health (alias /healthz), /readyz, /docs. One process is required because
media and sessions are held in bounded RAM. Every owner request needs
X-Demo-User-Id matching DEMO_USER_ID. This is demo identity, not authentication.

Application integration
------------------------
LiveAgentBackend sends the demo identity, uploads completed reels, reads
/timeline for checkpoint digests, submits /feedback, and ends broadcasts via
/session/end after draining uploads. Set SharedStore.demoUserID to match the
server (default demo-user). Notifications remain in the existing app code.

The current native adapter does NOT certify feed eligibility: legacy uploads
are explicitly marked dev_fake and accepted only with dev_permissive in
DEVELOPMENT. Production rejects them until a native Reels eligibility gate
supplies proper provenance. Do not relax the production gate.

Approved values now default to Guardian's protected Store namespace
(user_id, "guardian", "values"), one {text, updated} entry per Vn. The Judge
SELECTs and renders these entries in numeric ID order, preserving wording.
VALUES_STORE_LAYOUT=document explicitly supports the previous
(user_id, "memories")/values.md format; no fallback to stale documents occurs.

judge.integration.guardian_source.JudgeDataSource implements Guardian's
DataSource against the running Judge's real repositories. Startup exposes it as
app.state.guardian_datasource. Pass that object to
guardian.api.configure(store, checkpointer, source).
Run Guardian entry points in a worker thread; they are synchronous. Guardian
store/checkpointer provisioning and chat route ownership stay with the backend
integration owner. This package does not implement /agent2/chat, value approval
UI or the Reflector; the iOS coach chat route still needs that integration.

Verification and docs
----------------------
  watcher_Agent/scripts/verify.sh
Add TEST_DATABASE_URL pointing ONLY at a disposable PostgreSQL database for
migration and repository integration tests. Live Gemini calls are opt-in.

  ../JUDGE_WORKFLOW.txt             current execution flow
  ../JUDGE_MEMORY_OUTPUTS.txt       stored records and consumers
  docs/API_CONTRACT.txt             HTTP shapes and integration changes
  docs/PLATFORM_BOUNDARY.txt        native implementation versus PRD target
  docs/PRIVACY.txt                  capture provenance and retention
  docs/DATABASE_INTEGRATION.txt     deployment and limited database role
  docs/MEMORY_PARAMETERS.txt        settings and memory registry
  docs/PRD_TRACEABILITY.txt         requirement-to-test mapping and limits

Historical migrations 0001-0005 remain immutable for deployed databases.
0005's notification tables and old batch columns are dormant: no runtime
reads/writes them. No existing notification data is destroyed by this change.
0006 adds the PRD/Guardian feedback label fine (legacy rerate stays readable).
Generated contracts remain version 1 for this coordinated repo update, but
removing notify and acknowledgements is a breaking change for older clients.

Verification for this revision: verify.sh passed (496 tests, 91% coverage;
37 skipped without TEST_DATABASE_URL, 3 live tests deselected). Separate local
PostgreSQL run: 65 passed, 2 skipped because pgvector is unavailable. Factory
startup/shutdown and a completed native upload producing a stored verdict are
covered. Changed Swift files pass SwiftFormat; Xcode build is blocked by the
unaccepted local Xcode license. Live Gemini and physical-device tests were
not run. These counts do not imply coach chat or production capture is ready.

Grok configuration and sample executions
---------------------------------------
Default judge provider: grok. Put your key ONLY in watcher_Agent/.env:
  PROVIDER_MODE=grok
  XAI_API_KEY=<your key>
  GROK_MODEL_ID=grok-4.7
  GROK_TRANSCRIPTION_MODEL=grok-voice-transcribe-2.0
  EMBEDDING_INDEXING_ENABLED=false
The ignored local file is prepared for development/memory mode. This mode
uses synthetic values and loses records on restart. PostgreSQL production
still needs the existing database and eligibility configuration.

Grok's vision/chat API accepts text and images. Waveforms use xAI /v1/stt,
then one /v1/chat/completions request with frames, transcribed text and
approved values. The transcript is retained from STT, not invented by the
judging model. Empty STT output is unintelligible, not proof of music or
silence. Native transcript uploads skip STT. A normal audio attempt therefore
uses TWO xAI requests; one judging call remains. Retries may repeat STT.
No Gemini key/call is required in Grok mode; embedding indexing is disabled.
The existing Gemini/local/fake adapters remain available explicitly.

See docs/SAMPLE_EXECUTIONS.txt for generated audio/video examples, Guardian
approval executions, database tests, deliberately wrong decisions and the
explicit live Grok test. The replay report is not a recognition benchmark.
