# Guardian handover for the backend

For the backend owner. Checkpoint 1 covers the chat routes: `/chat`, `/chat/resume`, `/status` and `/memory`. Checkpoint 2 adds the inbox routes and the background review job; they are described at the end of this page.

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

## Checkpoint 2: the inbox and the review job

| Route or job | Function call | Request body |
| --- | --- | --- |
| `GET /questions` | `api.get_questions(user_id)` | none |
| `POST /questions/{id}/answer` | `api.answer_question(user_id, question_id, answer)` | `{"action": "...", "edited_text": "..."}` |
| Background job | `api.reflect_pending(user_id)` | none |

### The review job

`api.reflect_pending(user_id)` runs the Reflector on every finished session that has not been reviewed and returns how many it reviewed.

- **Never call it inside a request the app is waiting on.** One session takes 1 to 3 minutes. Run it as a background task.
- Call it in three places: when the broadcast stops (`POST /session/end`), when the app opens, and on a 10-minute timer. Extra calls are harmless: it returns `0` if a review is already running or nothing is waiting.
- A session counts as finished when `stopped` is true or it has had no new reels for 10 minutes. Both come from your `DataSource.list_sessions`.
- It does not raise for a failed review. A failed run is rolled back and retried on a later call, up to three times.

### `GET /questions`

Returns the cards waiting for the user, oldest first. `created` is a Python `datetime`; send it as an ISO 8601 string.

```json
[
  {
    "id": "learned_s_001",
    "kind": "learned",
    "prompt": "- You marked 3 hustle reels as not for you. [fb_002, fb_004, fb_007]\n- You disagreed with 5 flags on injury rehab reels. [fb_001, fb_003, fb_005]\n\nWhat makes a rehab story feel different from a transformation video to you?",
    "session_id": "s_001",
    "value_change": null,
    "reel_ids": null,
    "created": "2026-11-14T22:30:00+00:00"
  },
  {
    "id": "q_3f9a1c2b7d4e",
    "kind": "value_change",
    "prompt": "You disagreed with 5 flags on injury rehab reels. I've added an Unless line; edit it if you'd like.",
    "session_id": "s_001",
    "value_change": {
      "value_id": "V1",
      "old_text": "## V1: Fitness without body comparison (strength: high)\n- Want more: form tutorials, training plans, recovery\n- Want less: before/after transformations, physique rating\n- Unless: none\n- In my words: \"gym stuff is great, comparing bodies is not\"\n- Evidence: said at onboarding; approved 2026-11-14",
      "new_text": "## V1: Fitness without body comparison (strength: high)\n- Want more: form tutorials, training plans, recovery\n- Want less: before/after transformations, physique rating\n- Unless: injury rehab and recovery progress\n- In my words: \"gym stuff is great, comparing bodies is not\"\n- Evidence: said at onboarding; approved 2026-11-14",
      "evidence": "fb_001, fb_003, fb_005"
    },
    "reel_ids": null,
    "created": "2026-11-14T22:30:01+00:00"
  }
]
```

- A `learned` card's `prompt` is one to three bullets and, usually, a closing question after a blank line. Show it as text.
- A `value_change` card has the same four fields as a card in chat, so the app can use one card component. Show `prompt` above it.
- `kind` can also be `rerate` in the contract, but that feature is not built; no such card is produced.
- An empty inbox is `[]`.

### `POST /questions/{id}/answer`

| Card kind | Valid `action` | Effect |
| --- | --- | --- |
| `value_change` | `approve`, `edit` (with `edited_text`), `reject` | Approve or a valid edit stores the value; reject stores nothing. The card leaves the inbox |
| `learned` | `dismiss` | The card leaves the inbox |

Request:

```json
{"action": "approve"}
```

Response, success:

```json
{"ok": true, "error": null}
```

Response, an invalid edit. Nothing is stored and the card stays in the inbox:

```json
{
  "ok": false,
  "error": "An entry has 6 lines; this one has 1. Keep the card's six lines: the ## header, Want more, Want less, Unless, the wording line and Evidence. Change only the text after each label."
}
```

Other refusals, each with `"ok": false` and nothing changed:

| Case | `error` |
| --- | --- |
| The action does not fit the card | `A "What I learned" card can only be dismissed.` or `A value card can be answered with approve, edit or reject.` |
| The card was already answered | `This card was already answered.` |
| Unknown ID, or another user's card | `This card does not exist.` |
| The value changed since the card was made | `This value has changed since the card was made, so the card no longer applies.` The card is closed and leaves the inbox |

After a successful approval, `api.get_values(user_id)` returns the new text, so the Judge uses it from the next reel.

### Checks for checkpoint 2

- [ ] A finished session triggers `reflect_pending` in the background, and `GET /questions` then returns a "What I learned" card
- [ ] A value-change card renders with the same component as in chat
- [ ] Approving it changes what the Judge gets from `get_values`
- [ ] Dismissing the "What I learned" card removes it
- [ ] An invalid edit shows the error and keeps the card

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
