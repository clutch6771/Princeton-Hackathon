# Guardian handover for the backend: chat routes (checkpoint 1)

For the backend owner. It covers the four routes that are ready now: `/chat`, `/chat/resume`, `/status` and `/memory`. `/questions` and the background review job come at checkpoint 2.

The Guardian package exposes Python functions; you own the routes. Each route below is one function call. Return the function's result as the response body, unchanged.

## Wiring

```python
from guardian import api

api.configure(store, checkpointer, datasource)   # once at startup: MongoDBStore, MongoDBSaver, your DataSource
```

- **User ID:** read it from the request header the team agreed on (shown here as `X-User-Id`). It may contain only letters, digits, `_` and `-`.
- **Thread ID:** build it on the server, one per user per day: `thread_id = f"{user_id}:{date}"`. The app does not send it.
- **Timing:** `chat` and `resume` call the model and take about 2 to 10 seconds. They are ordinary blocking functions, so declare the FastAPI routes with `def`, not `async def`, and FastAPI will run them in its thread pool.
- **Errors:** the functions return errors in the `error` field; they do not raise for anything a user can cause. Send the body back with status 200 and let the app show `error`. The only exception raised is `guardian.config.ConfigError` (missing `configure` call or API key), which is a server fault.
- **Your `DataSource`:** implement the protocol in `guardian/datasource.py` over MongoDB. Until then, `FakeDataSource.from_seed("demo-user")` serves 40 seed reels.

## Routes

| Route | Function call | Request body |
| --- | --- | --- |
| `POST /chat` | `api.chat(user_id, thread_id, message, context)` | `{"message": "...", "context": {...} or null}` |
| `POST /chat/resume` | `api.resume(user_id, thread_id, decision)` | `{"action": "approve" or "edit" or "reject", "edited_text": "..." or null}` |
| `GET /status` | `api.has_values(user_id)` | none |
| `GET /memory` | `api.get_memory(user_id)` | none |

`context` says where the chat was opened from. Pass the body's `context` straight through:

```json
{"entry": "drift_notification", "reel_ids": ["rl_014", "rl_015", "rl_016"]}
```

`entry` is one of `onboarding`, `chat`, `drift_notification`, `reel`, `learned`. Send `reel_ids` when the user taps a drift notification or a reel in the Timeline. Send `null` for an ordinary message.

## `POST /chat`

Request:

```json
{"message": "hi", "context": null}
```

Response, a plain reply:

```json
{
  "reply": "What would you like to see less of in your feed?",
  "pending": null,
  "error": null
}
```

Response, a **pending value card**. The app shows `pending.value_change` as a card and waits for the user's decision:

```json
{
  "reply": null,
  "pending": {
    "kind": "value_change",
    "value_change": {
      "value_id": "new",
      "old_text": null,
      "new_text": "## New: Fitness without body comparison (strength: high)\n- Want more: form tutorials, training plans, recovery\n- Want less: before/after transformations, physique rating\n- Unless: none\n- In my words: \"gym stuff is great, comparing bodies is not\"\n- Evidence: said in chat",
      "evidence": "said in chat"
    }
  },
  "error": null
}
```

- `value_id` is `"new"` or an existing ID such as `"V3"`. For a change, `old_text` holds the current entry.
- `reply` may be set alongside `pending`; show both.
- `new_text` may contain a line starting `Suggested wording:`. Show it as it is.

Response, an **error**. While a card is waiting, a new message is not processed; the same card comes back with an error:

```json
{
  "reply": null,
  "pending": { "kind": "value_change", "value_change": { "...": "the same card as above" } },
  "error": "Please approve, edit or reject the value card first."
}
```

Other errors have `pending: null`, for example a thread that does not belong to the user:

```json
{"reply": null, "pending": null, "error": "This conversation does not belong to this user."}
```

If the model is unavailable: `"error": "The Guardian could not reply just now. Please try again in a moment."`

## `POST /chat/resume`

Request, approve or reject:

```json
{"action": "approve", "edited_text": null}
```

Request, edit. `edited_text` is the full entry as the user changed it:

```json
{"action": "edit", "edited_text": "## New: Fitness without body comparison (strength: high)\n- Want more: ...\n- Want less: ...\n- Unless: physio and rehab\n- In my words: \"...\"\n- Evidence: said in chat"}
```

Response, success. The value is stored and the Guardian replies:

```json
{
  "reply": "Saved as V1. Anything else you'd like to change?",
  "pending": null,
  "error": null
}
```

Response, an **invalid edit**. Nothing is stored and the card is still waiting, so the user can correct it and try again:

```json
{
  "reply": null,
  "pending": { "kind": "value_change", "value_change": { "...": "the same card" } },
  "error": "An entry has 6 lines; this one has 1. Keep the card's six lines: the ## header, Want more, Want less, Unless, the wording line and Evidence. Change only the text after each label."
}
```

Response, no card waiting (including a second answer to a card that was already answered):

```json
{"reply": null, "pending": null, "error": "No value card is waiting in this conversation."}
```

If the value changed after the card was made, the response has `"pending": null` and an `error` saying the card no longer applies. The card is closed; do not show it again.

## `GET /status`

`api.has_values(user_id)` returns a boolean. Suggested body:

```json
{"has_values": false}
```

The app must not allow recording until this is `true`. Without an approved value the Judge has nothing to judge against.

## `GET /memory`

Read-only. File name to markdown text; an empty string means nothing is stored yet.

```json
{
  "values.md": "## V1: Fitness without body comparison (strength: high)\n- Want more: form tutorials, training plans, recovery\n- Want less: before/after transformations, physique rating\n- Unless: none\n- In my words: \"gym stuff is great, comparing bodies is not\"\n- Evidence: said in chat; approved 2026-11-14",
  "brief.md": "",
  "hypotheses.md": "",
  "notes.md": ""
}
```

## For the Judge

`api.get_values(user_id)` returns the `values.md` text above, or `""`. Call it for each reel; do not read the store directly. The draft Judge prompt is in `guardian/judge_prompt.md`.

## A reference server until the backend is ready

`dev_server/` holds a temporary FastAPI server with exactly these four routes, one fixed user (`demo-user`), in-memory storage and the seed data. It is not the team's backend and should be deleted once the real routes exist. It doubles as a working example of the wiring above.

```bash
pip install -r dev_server/requirements.txt
GUARDIAN_DEV_SEEDED=1 uvicorn dev_server.app:create_app --factory --host 0.0.0.0 --port 8000
```

`GUARDIAN_DEV_SEEDED=1` starts it with three approved values, so a drift explanation works straight away. Leave it out to start with none and go through the interview. See `dev_server/README.md` for curl examples.

## Try it without a server

```bash
python -m guardian.demo_chat            # run the interview and approve a card
python -m guardian.demo_chat --seeded   # start with three values, then type /drift
```

## Checks for this checkpoint

- [ ] `POST /chat` returns a reply for "hi"
- [ ] A value card arrives in `pending` and renders in the app
- [ ] `POST /chat/resume` with `approve` stores the value; `GET /status` turns `true`
- [ ] An invalid edit shows the error and keeps the card on screen
- [ ] `POST /chat` with a drift-notification `context` returns an explanation of those reels
- [ ] The Judge's `get_values` call returns the approved value
