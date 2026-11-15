# Guardian: what the Judge server needs to do

For the backend owner. One page. The Guardian and Reflector run inside your server through a router it includes. Details and exact JSON: `guardian_router/README.md` and `docs/GUARDIAN_BACKEND_HANDOVER.md`. How to run it: `docs/RUN_DEMO.md`.

**Status, Nov 14 evening.** After the handover of integration, the Guardian owner made three small edits in `watcher_Agent/`: the include line (section 1), the session-end call (section 4) and one prompt rule (section 5). Your migrations have been run on the team database (section 2). Still open for you: sections 3 and 6.

## 1. Include the router (done)

In `judge/api/app_api.py`, next to the other `include_router` calls:

```python
from guardian_router import router as guardian_router

app.include_router(guardian_router, include_in_schema=False)
```

`include_in_schema=False` keeps your OpenAPI document to the Judge-owned routes, which `test_openapi_documents_the_judge_owned_contracts` pins.

- Install Guardian's packages in the server's environment: `pip install -r guardian/requirements.txt`. Your `requirements.txt` does not include `langchain` or `deepagents`.
- Start the server from the repository root with `python -m uvicorn ...`, as your README says, so `guardian` and `guardian_router` import.
- Set `GOOGLE_API_KEY` (the Guardian's Gemini key) and `DEMO_USER_ID=demo-user` (the app's default header value) in the root `.env`.
- The router takes its data from `app.state.guardian_datasource`, which you already set. It adds `POST /agent2/chat` (the app's chat), `/chat`, `/chat/resume`, `/status`, `/memory`, `/questions` and `/questions/{id}/answer`. None clash with your routes.

This is tested against your real app factory on your fakes: `guardian_router/tests/test_with_judge_server.py`.

## 2. The team database

The team database is a new Tiger Data instance. Point `DATABASE_URL` at it (ask the Guardian owner for the string; never commit it). We use **one database role** for the hackathon.

Guardian's tables already exist there, in schema `public`: `store`, `store_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`. They are empty.

Migrations 0001 to 0006 were applied on Nov 14 (pgvector 0.8.7 is available). For reference, and for any later database:

| Step | Command | Run it? |
| --- | --- | --- |
| Check pgvector | `SELECT name FROM pg_available_extensions WHERE name = 'vector';` | Yes. If it returns nothing, use `up --without-vector` |
| Your migrations | `python watcher_Agent/migrations/migrate.py status`, then `... migrate.py up` | **Yes.** Creates schema `judge` and `public.judge_schema_migrations`. Touches nothing of Guardian's |
| `migrate.py setup-store` | | **Safe but not needed.** It calls the same `PostgresStore.setup()` Guardian already ran, at the same pinned version (`langgraph-checkpoint-postgres==3.2.1`), and applies only missing migrations. Guardian itself calls it on every start |
| `migrate.py grant --runtime-role ...` | | **No, not with one role.** It runs `REVOKE ALL ON public.store` from the role and grants back SELECT only. On our single role that removes Guardian's write access and every approval fails |
| `scripts/seed_dev_values.py` | | **No, never on this database.** It writes synthetic V1 to V3 into the rows where Guardian stores approved values, overwriting real ones and bypassing the user's approval |

A SELECT-only Judge role is optional. If you want one later, create it, run `grant` for that role only, and give Guardian a writing role through `GUARDIAN_DATABASE_URL`.

## 3. Reading values: call `get_values` instead of the SELECT

Your direct SELECT on `public.store` works today; a test pins the layout and it was checked on the team database. The preferred call is:

```python
from guardian import api

values_md = await asyncio.to_thread(api.get_values, owner_id)   # "" if none
```

Then Guardian's storage layout can change without breaking the Judge. Guardian functions block and must be called from a worker thread.

## 4. Call the session-end function (done)

In `POST /session/end`, as a background task:

```python
from guardian_router import review_sessions

background_tasks.add_task(review_sessions, owner_id)
```

It reviews the user's finished sessions (one to three minutes each), never raises, and extra calls are harmless. A session is reviewed only once its status is `ENDED`; yours can still be `ENDING` when the route returns, so the function retries for about 45 seconds. Calling it where the session becomes `ENDED` (`complete_session_if_done`) would be more exact. Until either is added, the router starts reviews itself on each chat message.

## 5. Your Judge prompt and the values format

Each approved value has six lines:

```markdown
## V1: Fitness without body comparison (strength: high)
- Want more: none
- Want less: before/after transformations, physique rating
- Unless: injury rehab and recovery progress
- Agreed wording: "I don't want body comparison videos"
- Evidence: fb_001, fb_003; approved 2026-11-14
```

- **Wording line.** It is either `In my words` or `Agreed wording`. Your parser accepts both; treat both as the user's approved value.
- **`Want more: none` is the normal case.** The Guardian's interview is about what the user wants less of. "none" on any line means nothing to match on that line.
- **The prompt now says what the lines mean (done).** One rule was added to the "Rules" block of `judge/understanding/prompt.py`, from `guardian/judge_prompt.md`: *conflict* when the reel matches a "Want less" line and no "Unless" exception applies; *aligned* when it matches a "Want more" line; otherwise *neutral*; a line that says "none" matches nothing. `UNDERSTANDING_PROMPT_VERSION` was left at `judge-v1`; bump it if you track prompt changes by version.

## 6. Things to check on your side

- **`ocr_text` from the app is dropped.** `NativeReelUpload` has no `ocr_text` field and uses `extra="ignore"`, so the app's on-screen text never reaches the Judge; `on_screen_text` comes only from the model. Add the field if on-screen text should count. (`stated_goals` and `sensitivity` are dropped on purpose, which is right.)
- **The app's chat timeout is now 30 seconds** (it was 12), which covers every Guardian reply seen in the live runs.
- **The app could let the Guardian speak first:** call `agent2/chat` with an empty `message` when the chat screen appears and show the reply as the first bubble. The chat now opens empty.
- **App feedback comments are not treated as the user's words.** The app sends a preset correction as `comment`, so Guardian never quotes it in a value.
- **The app has no card or inbox screen**, so value cards and "What I learned" cards arrive as chat text, and the user answers "approve" or "reject". The JSON routes are there when a card UI exists.
