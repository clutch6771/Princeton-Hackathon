# Guardian package

The two agents that talk with the user and learn about them: the **Guardian** (chat agent) and the **Reflector** (background session review). This package exposes Python functions only. `guardian_router/` (outside this package) wraps them in HTTP routes for the team's server to include; the Judge calls one of them for the values text.

Status: the whole demo loop works on seed data: chat, value approval, the values lookup, reel explanation, feedback in chat, the Reflector's session review, and the inbox.

Related pages:

- `docs/GUARDIAN_BACKEND_HANDOVER.md`: exact request and response JSON for the chat routes
- `docs/GUARDIAN_PROGRESS.md`: what each step produced and the library findings
- `docs/GUARDIAN_SPEC_V5.md`: the build spec

## Try the chat in a terminal

```bash
python -m guardian.demo_chat            # no values yet: run the interview, approve a card
python -m guardian.demo_chat --seeded   # start with three values, then type /drift
```

Type `/help` inside it for the commands. Everything is in memory and uses the seed data. To run the whole loop by hand: start with `--seeded`, type `/reflect` (a few minutes), then `/inbox` and `/answer 2 approve`, then `/values`.

## Setup

```bash
python3.13 -m venv guardian/.venv
source guardian/.venv/bin/activate
pip install -r guardian/requirements.txt
```

Put your own Gemini key in a `.env` file in the repository root (see `guardian/.env.example`). The file is git-ignored. Use one key per teammate so the Judge's calls do not use up the agents' quota.

## Storage

In memory by default. Set a Postgres connection string and the package persists everything it keeps: values, proposals, inbox cards, review records, counters, the chat log, notes, the Reflector's files, and chat threads including a card that is waiting.

| Variable | Used for |
| --- | --- |
| `DATABASE_URL` | Runtime connection (the variable the Judge server uses) |
| `GUARDIAN_DATABASE_URL` | Optional override for Guardian only |
| `MIGRATION_DATABASE_URL` | Optional owner role for creating the tables |
| `TEST_DATABASE_URL` | Tests only; a disposable database |

```bash
python -m guardian.storage setup        # create the tables once (uses MIGRATION_DATABASE_URL if set)
```

```python
api.configure(datasource=datasource)    # storage chosen from the environment
```

Connection strings are secrets: keep them in the root `.env`, which is git-ignored. The package never logs them. See `docs/GUARDIAN_BACKEND_HANDOVER.md` for the privileges Guardian's role needs and for the layout the Judge reads values from.

## Using it from the backend

Call `configure` once at startup, then call the entry points from your routes.

```python
from guardian import api

api.configure(datasource=datasource)   # storage from DATABASE_URL (Postgres), or in memory

response = api.chat(user_id, f"{user_id}:{date}", "hi")
# {"reply": "...", "pending": None, "error": None}

response = api.resume(user_id, thread_id, {"action": "approve", "edited_text": None})
values_md = api.get_values(user_id)       # for the Judge; "" if none
may_record = api.has_values(user_id)      # app blocks recording until True
memory = api.get_memory(user_id)          # {"values.md": ..., "brief.md": ..., "hypotheses.md": ..., "notes.md": ...}
```

To run without a database, pass the in-memory versions and the fake data:

```python
from datetime import UTC, datetime
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from guardian import api
from guardian.datasource import FakeDataSource
from guardian.seed.install import install_initial_values

store, datasource = InMemoryStore(), FakeDataSource.from_seed("demo-user")
api.configure(store, InMemorySaver(), datasource)
install_initial_values(store, datasource, "demo-user", datetime.now(UTC))   # optional: three approved values
```

## Entry points

| Backend route | Function | Available |
| --- | --- | --- |
| `POST /chat` | `chat(user_id, thread_id, message, context=None)` | Yes |
| `POST /chat/resume` | `resume(user_id, thread_id, decision)` | Yes |
| Judge, per reel | `get_values(user_id)` | Yes |
| `GET /status` | `has_values(user_id)` | Yes |
| `GET /memory` | `get_memory(user_id)` | Yes |
| `GET /questions` | `get_questions(user_id)` | Yes |
| `POST /questions/{id}/answer` | `answer_question(user_id, question_id, answer)` | Yes |
| Background job | `reflect_pending(user_id)` | Yes. Returns the number of sessions reviewed. Takes 1 to 3 minutes: never call it inside a request the app waits on |

Request and response shapes are in `guardian/types.py`. They match the build spec.

## Rules the backend and app need to follow

- **User IDs** contain only letters, digits, `_` and `-`.
- **Threads.** Use one thread per user per day: `thread_id = f"{user_id}:{date}"`. `chat` and `resume` return an error for a thread ID that does not start with `"{user_id}:"`.
- **Errors are returned, not raised.** Check `error` in every response and show it to the user. The only exception raised is `ConfigError`, when `configure` was not called or the API key is missing.
- **Value cards.** When `pending` is set, show `pending["value_change"]` as a card (value ID, old text, new text, evidence) and call `resume` with the user's decision. A card's text may contain a line starting `Suggested wording:`; show it as it is.
- **While a card is pending**, `chat` does not process a new message. It returns the same card with an error asking the user to decide first.
- **Failed approvals** (invalid edit, already answered, value changed since the card was made) change nothing and come back in `error`. After an invalid edit the card is still pending, so the user can try again.
- **Context.** Pass `context={"entry": "drift_notification", "reel_ids": [...]}` when the chat is opened from a notification. Other entries: `onboarding`, `chat`, `reel`, `learned`.
- **Feedback from chat.** When the user agrees or disagrees in chat, the Guardian records it through `DataSource.record_feedback` with `source="chat"`. The comment on such feedback is the agent's summary, not the user's exact words.

## For the Judge owner

- Call `api.get_values(user_id)` for the values text. Do not read the store directly; where values are stored is internal to this package.
- The draft Judge prompt is in `guardian/judge_prompt.md`.
- A stored value looks like this. The fifth line reads `In my words` or `Agreed wording`; treat both as the user's approved value.

```markdown
## V1: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: said at onboarding; approved 2026-11-14
```

## Fake data

`FakeDataSource.from_seed(user_id)` serves 40 invented reels and 8 feedback items for one user, generated from `seed/persona.md` by `seed/build_seed.py`. It implements the `DataSource` protocol in `guardian/datasource.py`, which the Judge server's `JudgeDataSource` also implements. `FakeDataSource.from_records(user_id, reels, feedback)` accepts real Judge output in the same record shape.

## Learning evaluation

```bash
python -m guardian.eval.learning_eval            # reuses cached Judge outputs
python -m guardian.eval.learning_eval --fresh    # forgets the cache and runs everything again
```

Three stages on the 20 held-out seed reels, with a text-only stand-in Judge that runs `guardian/judge_prompt.md`: the initial values alone; the same values with up to three similar feedback examples; and the values after the Reflector's proposals from the feedback session are approved unchanged. It makes about 60 Judge calls and one Reflector run. Judge outputs are cached in `guardian/eval/.cache/` (git-ignored), and every run that called a model is logged there and listed in the report. The result of the run made for the hackathon is in `guardian/eval/REPORT.md`.

## Tests

```bash
python -m pytest guardian/tests                 # offline, scripted model, no API calls
python -m pytest guardian/tests -m live         # real model; uses quota
TEST_DATABASE_URL=postgresql://... python -m pytest guardian/tests   # also runs every storage test on Postgres
```

Code follows the team config in `pyproject.toml` (black, isort, ruff, mypy):

```bash
ruff check guardian && black --check guardian && mypy guardian --exclude guardian/tests
```
