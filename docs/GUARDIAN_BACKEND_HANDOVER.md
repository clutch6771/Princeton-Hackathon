# Guardian handover for the backend

For the backend owner. This page is the reference for the Guardian's functions and the exact JSON of each route. **The routes are already built:** `guardian_router/` is a FastAPI router your server includes with one line (see `docs/GUARDIAN_FOR_BACKEND_OWNER.md` for the short version and `guardian_router/README.md` for the details). It also serves `POST /agent2/chat`, the route today's app calls.

The Guardian package exposes Python functions. Each route below is one function call, and the router returns the function's result as the response body, unchanged.

## Wiring

```python
from guardian import api

api.configure(datasource=datasource)   # once: storage comes from DATABASE_URL (Postgres) or stays in memory
```

The router does this itself on the first request, with `app.state.guardian_datasource`. The rest of this section applies if you call the functions directly.

- **User ID:** the `X-Demo-User-Id` header, default `demo-user`. It may contain only letters, digits, `_` and `-`.
- **Thread ID:** build it on the server, one per user per day: `thread_id = f"{user_id}:{date}"`. The app does not send it.
- **Timing:** `chat` and `resume` call the model and take about 2 to 10 seconds. They are ordinary blocking functions, so declare the FastAPI routes with `def`, not `async def`, and FastAPI will run them in its thread pool.
- **Errors:** the functions return errors in the `error` field; they do not raise for anything a user can cause. Send the body back with status 200 and let the app show `error`. The only exception raised is `guardian.config.ConfigError` (missing `configure` call or API key), which is a server fault.
- **Your `DataSource`:** `JudgeDataSource` (`watcher_Agent/judge/integration/guardian_source.py`) implements the protocol in `guardian/datasource.py`. It must be called from a worker thread, which a `def` route is. `FakeDataSource.from_seed("demo-user")` serves 40 seed reels for development.

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

One optional field was added for the app's chat adapter: `stated_goals`, the goal the user typed when setting up the app. With `"entry": "onboarding"` the Guardian starts from it and may quote it as the user's own words:

```json
{"entry": "onboarding", "stated_goals": "less gym comparison stuff"}
```

`api.pending_card(user_id, thread_id)` is also new: it returns the value card waiting on a thread (the `pending` object below) or `None`, without sending a message.

## `POST /chat`

Request:

```json
{"message": "hi", "context": null}
```

**An empty message** (`"message": ""`) means the app has just opened the chat screen. The Guardian speaks first: a greeting and its first question during onboarding, the explanation when `context` carries a drift notification, otherwise a follow-up on something the user said or a question about one of their values. If its last message is still unanswered, the same message comes back again without a model call.

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

### A removal card

When the user asks the Guardian to delete a value, `pending` has `kind: "value_removal"`. `value_change.value_id` names the value, `old_text` is the value as stored, and `new_text` is empty:

```json
{
  "reply": null,
  "pending": {
    "kind": "value_removal",
    "value_change": {
      "value_id": "V2",
      "old_text": "## V2: News without outrage (strength: medium)\n- Want more: calm explainers, local news, science\n- Want less: rage-bait politics, people shouting at each other\n- Unless: none\n- In my words: \"I want to know what happened, not be told who to be angry at\"\n- Evidence: said at onboarding; approved 2026-11-14",
      "new_text": "",
      "evidence": "asked in chat"
    }
  },
  "error": null
}
```

Show it as a confirmation ("Remove V2: News without outrage?") with approve and reject. Answer it through `/chat/resume` like any card. `approve` deletes the value: `GET /memory` and `get_values` no longer include it, and its ID is never used again. `reject` changes nothing. `edit` is refused and the card stays. A removal card is decided once, and is refused if the value changed after the card was made. Through `/agent2/chat` it is shown as text: "Remove V2: News without outrage? Reply approve or reject."

Only the Guardian proposes removals, and only when the user asks; they never arrive in the inbox.

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
- Call it in three places: when the broadcast stops (`POST /session/end`), when the app opens, and on a 10-minute timer. Extra calls are harmless: it returns `0` if a review is already running or nothing is waiting. `guardian_router.review_sessions(user_id)` wraps it for a background task (it never raises and waits briefly for a session that is still being judged), and the router also starts a review on each `POST /agent2/chat` and `GET /questions`.
- A session counts as finished when `stopped` is true or it has had no new reels for 10 minutes. Both come from your `DataSource.list_sessions`.
- It does not raise for a failed review. A failed run is rolled back and retried on a later call, up to three times.

### `GET /questions`

Returns the cards waiting for the user, oldest first. `created` is a Python `datetime`; the router sends it as an ISO 8601 string in UTC, such as `2026-11-14T22:30:00Z`.

```json
[
  {
    "id": "learned_s_001",
    "kind": "learned",
    "prompt": "- You marked 3 hustle reels as not for you. [fb_002, fb_004, fb_007]\n- You disagreed with 5 flags on injury rehab reels. [fb_001, fb_003, fb_005]\n\nWhat makes a rehab story feel different from a transformation video to you?",
    "session_id": "s_001",
    "value_change": null,
    "reel_ids": null,
    "created": "2026-11-14T22:30:00Z"
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
    "created": "2026-11-14T22:30:01Z"
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

## Erasing a user's data: `POST /account/reset`

Deletes everything stored about the user in the `X-Demo-User-Id` header: the Guardian's records (values, cards, notes, the chat log, the Reflector's files, chat threads) and, when the server stores to Postgres, the Judge's rows for that user (sessions, reels, feedback). It cannot be undone.

Request:

```json
{"confirm": "DELETE"}
```

Response, with what was deleted (the keys are table names on Postgres, `store` and `threads` in memory):

```json
{"user": "demo-user", "deleted": {"store": 41, "checkpoints": 6, "checkpoint_blobs": 9, "checkpoint_writes": 30, "judge.sessions": 2, "judge.reels": 23}}
```

| Case | Status and `detail` |
| --- | --- |
| The server has not allowed it | 403: `Account reset is disabled on this server. Set GUARDIAN_ALLOW_RESET=1 to allow it.` |
| No header, or an invalid user ID (this route has no default user) | 400 |
| `confirm` is not exactly `DELETE` | 400: `To erase this account, send {"confirm": "DELETE"}.` |
| The user has a recording session open | 409: `A recording session is still open. Stop the recording, then try again.` |
| A review of their last session is running | 409: `A review of the last session is still running. Try again in a few minutes.` |

It is off unless `GUARDIAN_ALLOW_RESET=1` is set in the environment or `.env`. It also clears what the server process remembers about that user's chat (a card on screen, an offer, the last digest).

**App change needed (not made; the Guardian owner does not edit app files).** A row in Settings, for example "Erase my data", that:

1. shows a confirmation dialog saying it deletes the values, notes, chat and recorded sessions and cannot be undone;
2. on confirm, calls `POST account/reset` with body `{"confirm": "DELETE"}` and the usual `X-Demo-User-Id` header;
3. on 200, clears the app's local chat: empty `messages` in `SessionManager` and remove the `guardianMemory` entry it saves in the shared `UserDefaults` (and `sessionInsights`, if the session history should go too);
4. on 409 or 403, shows the `detail` text.

## Storage, and the values layout the Judge reads

Guardian keeps its state in memory by default and in Postgres when a connection string is set.

| Variable | Used for |
| --- | --- |
| `DATABASE_URL` | Guardian's runtime connection, the same variable the Judge server uses |
| `GUARDIAN_DATABASE_URL` | Optional. Overrides `DATABASE_URL` for Guardian only, for when the Judge's runtime role is read-only on the store |
| `MIGRATION_DATABASE_URL` | Optional. An owner role, used only by `python -m guardian.storage setup` to create the tables |
| `TEST_DATABASE_URL` | Tests only. Must point at a disposable database; tests delete their users' rows from it |

With no `store` and `checkpointer` passed, `api.configure(datasource=...)` reads these itself. On Postgres it uses LangGraph's `PostgresStore` and `PostgresSaver` (package `langgraph-checkpoint-postgres`, the version the Judge pins) in schema `public`: tables `store`, `store_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`.

**The role Guardian connects with needs SELECT, INSERT, UPDATE and DELETE on those tables.** The team uses one database role for the hackathon, so nothing needs granting. Do not run the Judge's `migrate.py grant` step for that role: it revokes everything on `public.store` and grants back SELECT only, which would stop every approval. If the Judge later gets its own SELECT-only role, give Guardian a writing role through `GUARDIAN_DATABASE_URL`.

The tables were created in the team database on Nov 14 with `python -m guardian.storage setup`. The Judge's `setup-store` step calls the same LangGraph setup and is safe to run afterwards, though not needed. Never run `watcher_Agent/scripts/seed_dev_values.py` against the team database: it overwrites the rows that hold approved values.

Guardian also takes Postgres advisory locks (`pg_advisory_lock`) so that a value card is decided once, value IDs are never reused, and one review runs per user, even with several server processes.

### Compatibility contract: where approved values are stored

The Judge reads approved values with a direct query instead of calling Guardian:

```sql
SELECT key, value, updated_at FROM public.store
WHERE prefix = '<user_id>.guardian.values' AND (expires_at IS NULL OR expires_at > now())
```

Guardian guarantees the following, and a test pins it (`test_the_judges_select_finds_approved_values_and_nothing_else`):

- Each approved value is one row in namespace `(<user_id>, "guardian", "values")`, which LangGraph stores as prefix `<user_id>.guardian.values`.
- The key is the value ID (`V1`, `V2`, ...). Nothing else is ever stored in that namespace; the ID counter and pending proposals live elsewhere.
- `value` is `{"text": "<the full entry>", "updated": "<ISO time>"}`, and `text` starts with `## <key>:`.
- Joining the `text` of the rows in ID order, separated by a blank line, gives exactly what `api.get_values(user_id)` returns.
- Only one function writes these rows: the decision on a pending card (`proposals.decide`).
- User IDs contain only letters, digits, `_` and `-`, so the prefix is unambiguous.

**Preferred instead:** call `guardian.api.get_values(user_id)`. Then the storage layout can change without breaking the Judge. The contract above holds until that switch is made.

## The router, and a standalone server

`guardian_router/` serves every route on this page, plus `POST /agent2/chat` for today's app. Include it in the Judge server with one line, or run it on its own with seed data, one demo user and in-memory storage:

```bash
pip install -r guardian_router/requirements.txt
GUARDIAN_DEV_SEEDED=1 uvicorn guardian_router.standalone:create_app --factory --host 0.0.0.0 --port 8000
```

`GUARDIAN_DEV_SEEDED=1` starts it with three approved values, so a drift explanation works straight away. Leave it out to start with none and go through the interview. See `guardian_router/README.md`.

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
