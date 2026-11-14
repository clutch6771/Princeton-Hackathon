# Guardian dev server (TEMPORARY)

**This is not the team's backend.** It is a throwaway server so the iOS app can talk to the Guardian's chat for integration checkpoint 1, before the real backend serves these routes. Delete this folder once it does.

What it is: four routes, one fixed user (`demo-user`), in-memory storage, and the Guardian's seed reel data. No database, no ingest, no Judge, no notifications. Everything is lost when the server stops.

## Run

From the repository root, with the Guardian environment active (see `guardian/README.md`) and your Gemini key in the root `.env`:

```bash
pip install -r dev_server/requirements.txt
uvicorn dev_server.app:create_app --factory --host 0.0.0.0 --port 8000
```

To start with three approved values, so drift explanations work straight away:

```bash
GUARDIAN_DEV_SEEDED=1 uvicorn dev_server.app:create_app --factory --host 0.0.0.0 --port 8000
```

A physical iPhone needs HTTPS. Put a tunnel in front of port 8000 (for example `cloudflared tunnel --url http://localhost:8000`) and use the tunnel's URL in the app.

## Routes

Request and response bodies are exactly those in `docs/GUARDIAN_BACKEND_HANDOVER.md`. The user is fixed, so no user header is read, and the server builds the thread ID itself (one per day).

| Route | Body |
| --- | --- |
| `POST /chat` | `{"message": "...", "context": {...} or null}` |
| `POST /chat/resume` | `{"action": "approve" or "edit" or "reject", "edited_text": "..." or null}` |
| `GET /status` | none; returns `{"has_values": true or false}` |
| `GET /memory` | none; returns file name to markdown |

Errors a user can cause come back with status 200 and an `error` field. A malformed body returns 422.

## Try it

```bash
curl -s localhost:8000/status
curl -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"message": "I want less body comparison. Gym stuff is great, comparing bodies is not."}'
curl -s -X POST localhost:8000/chat/resume -H 'content-type: application/json' \
  -d '{"action": "approve", "edited_text": null}'
curl -s localhost:8000/memory
```

With a seeded server, open a drift explanation on seed reels:

```bash
curl -s -X POST localhost:8000/chat -H 'content-type: application/json' \
  -d '{"message": "Why did I get this notification?", "context": {"entry": "drift_notification", "reel_ids": ["rl_014", "rl_015", "rl_016"]}}'
```

## Tests

```bash
python -m pytest dev_server
```
