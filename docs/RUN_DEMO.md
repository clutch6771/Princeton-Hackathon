# Running the demo: one server for the real app

The Judge and the Guardian run as one server, started from `guardian/.venv`. This page has the `.env` lines, the command, the tunnel, a smoke test and the failures you are most likely to see.

Already done on the team database: Guardian's tables and the Judge's migrations (0001 to 0006, pgvector included). Do not run `migrate.py grant` or `scripts/seed_dev_values.py` against it.

## 1. The `.env` file

One file, `.env` in the repository root (git-ignored; never commit it). Both halves read it.

```bash
# Keys and database
GOOGLE_API_KEY=...          # Guardian and Reflector (Gemini)
GOOGLE_API_KEY_FALLBACKS=key2,key3   # optional spare Gemini keys, comma-separated
DATABASE_URL=...            # the team database; the Judge and the Guardian share it
XAI_API_KEY=...             # Judge, for PROVIDER_MODE=grok

# Judge server
APP_ENV=development
PRIVACY_MODE=dev_permissive
STORAGE_MODE=postgres
DEMO_USER_ID=demo-user
PROVIDER_MODE=grok
EMBEDDING_INDEXING_ENABLED=false
PROVIDER_TIMEOUT_SECONDS=30
JOB_DEADLINE_SECONDS=90
PROVIDER_MAX_ATTEMPTS=1
UNDERSTANDING_MAX_OUTPUT_TOKENS=2048
```

Why each Judge line is there:

| Line | Reason |
| --- | --- |
| `APP_ENV=development` and `PRIVACY_MODE=dev_permissive` | The app's uploads are marked `dev_fake`. Under `enforce` (the default, and the only mode production allows) every upload is rejected |
| `STORAGE_MODE=postgres` | Reels and verdicts go to the team database. `memory` loses them on restart and uses made-up values |
| `DEMO_USER_ID=demo-user` | Must equal the demo user ID in the app's settings (its default is `demo-user`). The Judge refuses any other value in `X-Demo-User-Id` |
| `PROVIDER_MODE` | Which model judges reels, and so which key is needed (table below) |
| The last five lines | Needed for Grok, copied from `watcher_Agent/.env.example`: Grok has no embeddings, and its two calls per reel need longer timeouts |

| `PROVIDER_MODE` | Key it needs | Notes |
| --- | --- | --- |
| `grok` (the Judge's default) | `XAI_API_KEY` | Also needs `EMBEDDING_INDEXING_ENABLED=false`, or the server refuses to start |
| `gemini` | `GEMINI_API_KEY` | A different variable from `GOOGLE_API_KEY`. The same Google key works in both, but then the Judge and the Guardian share one quota |
| `fake` | none | Made-up verdicts, for checking the plumbing only. Add `FAKE_VERDICT_PATTERN=conflict` to get flagged reels |

**The tracing flags must be unset.** `LANGSMITH_TRACING`, `LANGCHAIN_TRACING_V2` and `LANGCHAIN_TRACING` must not be set to a true value in the shell or in `.env`: tracing would send frames, transcripts and values to a third party. The Judge refuses to start if the shell has one set. Leave them out, and check with `env | grep -i tracing`.

## 2. Start the server

From the repository root, one command:

```bash
guardian/.venv/bin/python -m uvicorn judge.api.app_api:create_app --factory --app-dir watcher_Agent --host 0.0.0.0 --port 8000 --workers 1
```

- Run it from the repository root: that is how `guardian`, `guardian_router` and `.env` are found.
- One worker only. The Judge keeps open sessions in memory.
- `guardian/.venv` has both sets of packages. To rebuild it: `pip install -r guardian/requirements.txt -r watcher_Agent/requirements.txt`.

## 3. Open a tunnel for the phone

```bash
cloudflared tunnel --url http://localhost:8000     # or: ngrok http 8000
```

In the app's settings, set the backend URL to the HTTPS address it prints, turn on the live backend, and leave the demo user ID as `demo-user`.

## 4. Smoke test

With the server running, in another terminal, from the repository root:

```bash
BASE=http://localhost:8000
H='X-Demo-User-Id: demo-user'

curl -s $BASE/health
curl -s $BASE/readyz

FRAME=$(guardian/.venv/bin/python -c "import base64,io;from PIL import Image;b=io.BytesIO();Image.new('RGB',(288,512),(40,90,160)).save(b,'JPEG');print(base64.b64encode(b.getvalue()).decode())")
SESSION=$(uuidgen)
curl -s $BASE/agent1/ingest -H "$H" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SESSION\",\"reel_index\":1,\"dwell_seconds\":12,\"frame_base64\":\"$FRAME\",\"transcript_text\":\"Ninety days, that is all it took me. Look at this before and after.\"}"

sleep 30
curl -s "$BASE/timeline?session_id=$SESSION&limit=5" -H "$H"

curl -s $BASE/agent2/chat -H "$H" -H 'Content-Type: application/json' \
  -d '{"message":"hi","profile":{"stated_goals":""},"context":null}'
```

What you should see:

| Call | Expected |
| --- | --- |
| `GET /health` | `{"status":"ok"}` |
| `GET /readyz` | `"status":"ready"`, `"backend":"postgres"`, `"privacy":{"mode":"dev_permissive",...}` and the provider mode you set |
| `POST /agent1/ingest` | `"status":"accepted"` |
| `GET /timeline` | One item with the transcript you sent. With an approved value: `"evaluation_status":"judged"` and a verdict. Before onboarding: `"verdict":null` and `"evaluation_status":"values_unavailable"`, which is correct |
| `POST /agent2/chat` | `"sender":"coachAgent"` and a greeting in `text`. This call also connects the Guardian to the database |

The smoke test leaves a session and a chat turn under `demo-user`. Clear them before the demo (next section).

## 5. Start the demo clean

Stop the server, then:

```bash
guardian/.venv/bin/python -m guardian_router.reset_demo --user demo-user          # lists what would go
guardian/.venv/bin/python -m guardian_router.reset_demo --user demo-user --yes    # deletes it
```

It deletes that one user's Guardian records (values, cards, notes, chat) and Judge rows (sessions, reels, feedback). Nothing can be recovered. Start the server again afterwards.

Then, in the app: open the chat and finish onboarding (approve the first value) **before** recording. Until a value is approved the Judge has nothing to judge against.

## If the Guardian stops replying

The Gemini free tier allows 500 requests a day for each model on a key. A chat turn uses 2 to 5 requests and a session review about 14, so a long demo day can use them up. The count starts again at 3:00 am Eastern.

How to recognise it:

- In the app, every Guardian reply is "The Guardian could not reply just now. Please try again in a moment."
- In the server log, each failed turn has a line like this:

```text
chat run failed for thread demo-user:2026-11-15: GoogleRateLimitError: Error calling model 'gemini-3.5-flash-lite' (RESOURCE_EXHAUSTED): 429 RESOURCE_EXHAUSTED ... Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 500 ... Please retry in 8m5s.
```

The words to look for are `RESOURCE_EXHAUSTED` and `Quota exceeded`. (A failed review logs `session review failed` or `review of session ... failed` with the same error.)

**Automatic, if spare keys are listed.** Put spare Gemini keys in `GOOGLE_API_KEY_FALLBACKS` in `.env`, separated by commas, before starting the server. When a Guardian or Reflector call fails with a quota or rate-limit error that the usual retries did not clear, the server switches to the next key, repeats the call once, and stays on that key. The log then has one line, and never a key:

```text
switched to fallback key 1
```

The switch lasts until the server restarts; a restart begins again with `GOOGLE_API_KEY`. The Judge's provider key is separate and is not covered by this.

**By hand, if every key is used up** (the error lines continue after the last `switched to fallback key` line), or if no spare keys were listed: put a fresh key in `GOOGLE_API_KEY` in `.env` and restart the server. Nothing is lost: values, cards and the chat are in the database.

## 6. The three most likely failures

**The server exits at startup with "Invalid configuration".** The message names every variable that is wrong, for example:

```text
judge.errors.ConfigurationError: Invalid configuration: XAI_API_KEY is required when PROVIDER_MODE=grok; Set EMBEDDING_INDEXING_ENABLED=false for Grok; no Gemini key is needed
```

Other forms: `DEMO_USER_ID is required`, `PRIVACY_MODE must be 'enforce' unless APP_ENV=development`, `DATABASE_URL is required when STORAGE_MODE=postgres`, `LANGSMITH_TRACING must be disabled`. Fix the line in `.env` and start again.

**The app shows no checkpoints and uploads fail: the user ID does not match.** The Judge answers:

```json
{"success":false,"data":null,"error":{"code":"unauthenticated","message":"missing X-Demo-User-Id header"}}
{"success":false,"data":null,"error":{"code":"forbidden","message":"this identity is not allowed"}}
```

401 means the header is missing; 403 means it differs from `DEMO_USER_ID`. Make the app's demo user ID and `DEMO_USER_ID` the same.

**Reels arrive but are never flagged: `"verdict":null`.** Look at `evaluation_status` in `/timeline`:

| `evaluation_status` | Cause | Fix |
| --- | --- | --- |
| `values_unavailable` | The user has no approved value yet | Finish onboarding in the chat and approve the card |
| `failed_provider` or `failed_timeout` | The Judge's model call failed | Check the provider key and quota; see the server log |
| (upload answered `privacy_capability_unavailable`, status 422) | `PRIVACY_MODE` is `enforce` | Set `APP_ENV=development` and `PRIVACY_MODE=dev_permissive` |

Two more, briefly:

- **`ModuleNotFoundError: No module named 'guardian_router'`** (or `judge`): the command was not run from the repository root, or not with `guardian/.venv`.
- **The app says "Coach unavailable. Please try again."**: the tunnel or backend URL is wrong, or the reply took longer than the app's 30 seconds. Send the same message again; the server returns the reply that was missed. If the chat text is "The Guardian could not reply just now", the Guardian's model call failed: check `GOOGLE_API_KEY` and the server log.
