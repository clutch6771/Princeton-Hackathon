# Backend brief: database and backend work before step 4b

From the Guardian owner, Saturday Nov 14. Checkpoint 2 was accepted on the live run. This work comes before step 4b.

Answers to the two open decisions:

1. Yes to the inbox and session-end routes, as part of Part C below.
2. Yes: extend the rule to "write 'none' for any line the evidence does not support", in the skill and the Guardian prompt. Fold it into 4b.

One more change for 4b: the Guardian's interview focuses on what the user wants less of. It does not ask what they want more of; "Want more" is "none" unless the user states it unprompted. Confirm the format check accepts "none" on that line, and that the Judge prompt treats "Want more: none" as nothing to match.

## Part A: Survey first (read-only, about 20 minutes, then stop and report)

1. Find the iOS app in the repo and list every network call it makes: URL, method, request JSON, response JSON it expects. Include the 8-second polling loop, the CheckpointDigest payload, the reel threshold setting, and what the broadcast extension uploads per reel (OCR text, frames, audio).
2. Find any Python backend a teammate has already written and say what it covers.
3. Give me one table: app call, what the app expects, which Guardian function or new backend piece serves it, and any mismatch with docs/GUARDIAN_BACKEND_HANDOVER.md or the PRD.

Where the app and the PRD disagree, plan to match the app, since it is already built. Do not edit the iOS code or a teammate's code. Wait for my go before Part B.

## Part B: Guardian storage on MongoDB (inside guardian/)

- Add a MongoDB option to configure(), selected by MONGODB_URI in the root .env. Treat the URI like the API key: never print or commit it. In-memory stays the default for tests.
- Everything the package keeps must persist: values, proposals, inbox cards, review records, the value-ID counter, chat log, notes, the Reflector's files and chat threads with pending cards. Use LangGraph's MongoDB store and checkpointer; check current package names in the docs.
- Every V5 guarantee must still hold on MongoDB: one write path, decide-once, per-user isolation, atomic value-ID counter, one Reflector run at a time per user, all-or-nothing reflect_pending.
- Tests: run the existing storage tests against both backends. MongoDB tests are marked `mongo` and skipped unless MONGODB_URI is set. Add a restart test: approve a value and leave a card pending, rebuild everything, and both are still there.

Stop and report after Part B.

## Part C: The real backend (in backend/, outside guardian/)

Promote dev_server/ into backend/ and remove the "temporary" marking. It imports guardian only through its public entry points.

- Routes: the existing four, plus GET /questions, POST /questions/{id}/answer, POST /session/end, POST /feedback, GET /timeline, and the upload route(s) in the shape the app actually sends, per the survey.
- Judge (backend/judge.py): one model call per reel using guardian/judge_prompt.md, the values from guardian.get_values(user_id), and the user's three most recent feedback items as examples. No embeddings or vector search. Input is whatever the app sends: text always, frames and audio if they arrive. Structured output in the reel record shape from the spec. Model set by JUDGE_MODEL, default gemini-3.5-flash-lite. No values means neutral. If the model call fails, store the reel as neutral with confidence 0 and reason "not judged", so uploads never block.
- Storage: reels, feedback and sessions collections in the same database. Raw frames and audio are never stored.
- MongoDataSource: implements the DataSource protocol over those collections, with keyword matching for search and similar feedback, like FakeDataSource. Keep FakeDataSource and the seeded mode working.
- Notifications: check every 15 judged reels, notify when at least 3 conflict, at most once per 10 minutes, wording about the user's own values. Deliver it the way the app expects (upload response body or its polling call), per the survey.
- Reflector trigger: /session/end, app open and a 10-minute idle check each start reflect_pending as a background task, never inside a request.
- Tests: offline tests with a scripted model and in-memory storage. Then one live run of the whole loop through HTTP: upload reels, verdicts stored, notification, chat explains, feedback, session end, inbox card, approve, get_values changed.
- backend/README.md: how to run it locally and behind a tunnel, and every environment variable.

Cap Part C at about three hours. Stop and report after it.

## After that

Step 4b (docs/GUARDIAN_REFLECTION_ADDENDUM.md, plus the two prompt changes above), then the learning evaluation. Feature freeze is 4:00 am.
