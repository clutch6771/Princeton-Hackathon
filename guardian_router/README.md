# Guardian router

HTTP routes for the Guardian and Reflector, for the team's server to include. It replaces the temporary `dev_server/`. The Guardian package itself (`guardian/`) exposes Python functions only; this folder is the thin HTTP layer over them.

## Included in the Judge server

One line in `watcher_Agent/judge/api/app_api.py`, next to the other `include_router` calls (already there):

```python
from guardian_router import router as guardian_router

app.include_router(guardian_router, include_in_schema=False)
```

To run the combined server for the real app, see `docs/RUN_DEMO.md`.

Start the server from the repository root, so that `guardian` and `guardian_router` are importable, with Guardian's requirements installed in the same environment:

```bash
pip install -r guardian/requirements.txt
python -m uvicorn judge.api.app_api:create_app --factory --app-dir watcher_Agent --workers 1
```

Nothing else is needed. On the first request the router:

1. takes the data source from `app.state.guardian_datasource`, which the Judge server already sets to its `JudgeDataSource` at startup;
2. calls `guardian.api.configure(datasource=...)`, which opens Postgres storage from `DATABASE_URL` (or keeps everything in memory when it is unset).

If `app.state.guardian_datasource` is missing, the routes answer 503 and say so.

### The JudgeDataSource wiring

`JudgeDataSource` must be called from a worker thread; it raises on the server's event loop. Every route here is a plain `def`, which FastAPI runs in a worker thread, and background reviews run in their own thread. So the rule is met without any code on the Judge side. If you call a Guardian function yourself from an `async def` route, wrap it: `await asyncio.to_thread(api.get_values, user_id)`.

A test runs this router inside the real Judge app on its own fakes (`tests/test_with_judge_server.py`): a reel uploaded through `/agent1/ingest` is found by the Guardian from the app's digest.

### Session end

The Judge server's `POST /session/end` runs this as a background task (already there):

```python
from guardian_router import review_sessions

background_tasks.add_task(review_sessions, owner_id)   # in POST /session/end
```

It runs the Reflector on every finished, unreviewed session of that user. It blocks for one to three minutes per session, never raises, and extra calls return 0. A session counts as finished only once the Judge marks it ended, which can be a few seconds after `/session/end` returns while the last reels are judged; the function waits and retries for about 45 seconds. A session with no judged reels is not reviewed.

Until the router has configured the Guardian (on its first request after the server starts) the function does nothing. The router also starts a review in the background on every `POST /agent2/chat` and `GET /questions`, once the user has at least one approved value, so a session that ended earlier is still reviewed.

## Routes

The user comes from the `X-Demo-User-Id` header and defaults to `demo-user`. The chat thread is one per user per local day (`GUARDIAN_TIMEZONE`), built on the server.

| Route | Guardian function | Body |
| --- | --- | --- |
| `POST /agent2/chat` | the chat adapter below | `{"message", "profile", "context"}` from the app |
| `POST /chat` | `api.chat` | `{"message": "...", "context": {...} or null}` |
| `POST /chat/resume` | `api.resume` | `{"action": "approve" or "edit" or "reject", "edited_text": "..." or null}` |
| `GET /status` | `api.has_values` | none; returns `{"has_values": bool}` |
| `GET /memory` | `api.get_memory` | none |
| `GET /questions` | `api.get_questions` | none; also starts a background review |
| `POST /questions/{id}/answer` | `api.answer_question` | `{"action": "...", "edited_text": "..."}` |

The JSON routes return the function's result unchanged, with errors in the body and status 200. Exact request and response JSON is in `docs/GUARDIAN_BACKEND_HANDOVER.md`. They are for a later card UI; today's app uses only `/agent2/chat`.

### `POST /agent2/chat`: the app's chat screen

The app sends `{message, profile, context}` and decodes a `ChatMessage`:

```json
{
  "id": "5f0c2d0e-7a51-4c0e-9d57-0d0a4f6f3b11",
  "sender": "coachAgent",
  "text": "…",
  "timestamp": "2026-11-14T21:00:00Z",
  "attached_digest": null,
  "client_action": null
}
```

The app has no value-card or inbox screen, so the adapter (`chat_adapter.py`) turns everything into chat text:

- **Value card as text.** A pending card is shown as plain text ending "Reply approve or reject." The decision is read in code, not by the model: exactly `approve` or `yes`, `reject` or `no`, in any case. Any other reply shows the card again. Editing is not offered; a card with suggested wording says to reject it and say it in your own words.
- **Inbox through chat.** At the start of a turn with no card waiting, pending inbox cards are presented one at a time, oldest first. A "What I learned" card is shown and dismissed; the user's next message goes to the Guardian as the answer to its question. A value card waits for approve or reject. The message that arrived when a card was shown is answered once the cards are done.
- **Digest.** The app's `context` is its checkpoint digest. It becomes a drift-notification entry with the reel IDs in `flagged_reels`. The app attaches the same digest to every later message, so each digest is explained once. The app sends reel IDs as upper-case UUIDs; they are lower-cased to match the Judge's records.
- **Stated goals.** During onboarding (no approved value yet) `profile.stated_goals` is passed to the Guardian as background and counts as the user's own words. The app's default goal, "Low-Stress Feed", is ignored: the user did not write it.
- **Opening the chat.** An empty `message` makes the Guardian speak first (or shows a waiting inbox card). Sending it again before the user has answered returns the same text without a model call. Today's app opens the chat empty and does not send this yet; one call when the chat screen appears would do it.
- **`conversation_memory`.** The redesigned app sends its saved messages with every request. They are ignored: the Guardian keeps the conversation itself.
- **Slow replies.** The app gives up on a chat request after 30 seconds. The request still completes on the server; if the user then sends the same message again, they get the reply they missed.

What has been shown to whom is kept in this process only. After a restart a card that was on screen is simply shown again before it can be decided.

## Environment variables

All are read from the environment or the root `.env` (git-ignored). Never commit real values.

| Variable | Needed | Purpose |
| --- | --- | --- |
| `GOOGLE_API_KEY` | Yes | Gemini key for the Guardian and Reflector |
| `GOOGLE_API_KEY_FALLBACKS` | No | Spare Gemini keys, comma-separated. On a quota or rate-limit error that retries do not clear, the process switches to the next key and stays on it |
| `DATABASE_URL` | For persistence | Postgres connection, the same variable the Judge server uses. Unset: everything is kept in memory |
| `GUARDIAN_DATABASE_URL` | No | Overrides `DATABASE_URL` for Guardian only. Needed only if the Judge runs with a SELECT-only role |
| `MIGRATION_DATABASE_URL` | No | An owner role, used only by `python -m guardian.storage setup` |
| `GUARDIAN_CHAT_MODEL` | No | Default `google_genai:gemini-3.5-flash-lite` |
| `GUARDIAN_REFLECTOR_MODEL` | No | Default `google_genai:gemini-3.5-flash-lite` |
| `GUARDIAN_REQUESTS_PER_MINUTE` | No | Chat model calls per minute. Default 12 |
| `GUARDIAN_REFLECTOR_REQUESTS_PER_MINUTE` | No | Reflector model calls per minute, on a separate limiter so a review cannot slow a chat reply. Default half the chat rate |
| `GUARDIAN_TIMEZONE` | No | Where a chat day starts and ends (an IANA name). Default `America/New_York`. Each user has one chat thread per local day |
| `GUARDIAN_DEV_SEEDED` | No | Standalone server only: `1` starts with three approved values |
| `TEST_DATABASE_URL` | No | Tests only. A disposable database; tests delete rows from it, and the Judge's tests drop and rebuild its schema there. Guardian's tests refuse to run if it equals `DATABASE_URL`. Never point it at the team database |

## Running it standalone, behind a tunnel

For trying the app's chat without the Judge server. One seeded demo user, 40 seed reels, everything in memory and lost on restart. It adds `GET /health` and `POST /session/end` (which starts a review). It has no Judge, `/timeline` or `/feedback`, so the app's own checkpoint digest and correction buttons do not work against it.

```bash
pip install -r guardian_router/requirements.txt
GUARDIAN_DEV_SEEDED=1 uvicorn guardian_router.standalone:create_app --factory --host 0.0.0.0 --port 8000
```

Leave `GUARDIAN_DEV_SEEDED` out to start with no values and go through onboarding. To reach it from a phone, open a tunnel and put the HTTPS address in the app's backend URL setting:

```bash
cloudflared tunnel --url http://localhost:8000     # or: ngrok http 8000
```

```bash
curl -s localhost:8000/agent2/chat -H 'Content-Type: application/json' -H 'X-Demo-User-Id: demo-user' \
  -d '{"message": "why were these flagged?", "profile": {"stated_goals": ""},
       "context": {"id": "d1", "flagged_reels": [{"id": "rl_014"}, {"id": "rl_015"}]}}'
```

## Starting a demo clean

```bash
python -m guardian_router.reset_demo --user demo-user          # lists what would go
python -m guardian_router.reset_demo --user demo-user --yes    # deletes it
```

Deletes one user's Guardian records and Judge rows from the database in `DATABASE_URL`. It refuses without `--yes`, and nothing can be recovered. Stop the server first or restart it afterwards.

## Tests

```bash
python -m pytest guardian guardian_router          # offline, scripted model
python -m guardian_router.live_run                 # one live run through HTTP; uses API quota, several minutes
python -m guardian_router.live_reflection          # onboarding, reflection and a review, live; prints every reply
```

The offline tests cover every route, the text fallback, the inbox through chat, the background reviews, and the router inside the real Judge app. The live run plays the app's part on seed data: onboarding from the stated goal, approve by text, a digest explanation, a disagreement, a session review, an inbox card through chat, approve, and a check that the values changed.
