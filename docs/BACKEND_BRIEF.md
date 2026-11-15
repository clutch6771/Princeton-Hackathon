# Backend brief: storage and the Guardian router, before step 4b

From the Guardian owner, Saturday Nov 14. Checkpoint 2 was accepted on the live run. This work comes before step 4b. Parts A, B and C are done; results are in `docs/GUARDIAN_PROGRESS.md`. Revised after the Part A survey, which found that a teammate has already built the Judge server (branch `Reel-Watcher-Agent`, `watcher_Agent/`) on PostgreSQL.

## Decisions after the survey

1. **Database:** Postgres. MongoDB is dropped. The team database is a Tiger Data instance created by the Guardian owner on Nov 14; the teammate points their server's `DATABASE_URL` at it and runs their own migrations there. One database role for the hackathon; `GUARDIAN_DATABASE_URL` stays as an option but is not set.
2. **Part C:** no new backend, Judge, reel storage or MongoDataSource. Build a small Guardian router that the teammate's server includes.
3. **App version:** match the teammate's branch (`Reel-Watcher-Agent`): `X-Demo-User-Id` header, `POST feedback`, `session/end`, a digest built from the timeline. Build nothing for the calls only the `main` version makes (`agent2/checkpoint`, `agent2/feedback`, `pending-intervention`).
4. **Value cards:** build a text fallback in the chat adapter. Keep the JSON card routes for a later card UI.

Earlier answers that still stand:

- Extend the rule to "write 'none' for any line the evidence does not support", in the skill and the Guardian prompt. Fold it into 4b.
- For 4b: the Guardian's interview focuses on what the user wants less of. It does not ask what they want more of; "Want more" is "none" unless the user states it unprompted. Confirm the format check accepts "none" on that line, and that the Judge prompt treats "Want more: none" as nothing to match.

## Part A: Survey (done)

Read-only survey of the iOS app's network calls and of any teammate backend. Findings are in the Part A report and summarised in `docs/GUARDIAN_PROGRESS.md`.

## Part B: Guardian storage on Postgres (inside guardian/)

- Add a Postgres option to configure(), selected by a connection-string variable in the root .env (use the name the teammate's server uses). Never print or commit it. In-memory stays the default for tests.
- Everything the package keeps must persist: values, proposals, inbox cards, review records, the value-ID counter, chat log, notes, the Reflector's files and chat threads with pending cards. Use LangGraph's Postgres store and checkpointer; check current package names in the docs.
- Every V5 guarantee must still hold: one write path, decide-once, per-user isolation, atomic value-ID counter, one Reflector run at a time per user, all-or-nothing reflect_pending.
- Values lookup: the teammate's Judge reads values with a direct SELECT on public.store, prefix `<user>.guardian.values`. Check whether that query finds what we store. The preferred fix is theirs (call guardian.api.get_values; the owner will ask). Until then, store the approved values text where their query reads it, keep the single write path as its only writer, pin that layout with a test, and document it in the handover as a compatibility contract.
- Tests: run the existing storage tests against both backends. Postgres tests are marked `postgres` and skipped unless the variable is set. Add a restart test: approve a value and leave a card pending, rebuild everything, and both are still there.
- The owner does not have the connection string yet. Use a local Postgres if one is available; otherwise leave those tests skipped and say exactly what to put in .env.

Stop and report after Part B.

## Part C: Guardian router (outside guardian/, replacing dev_server/)

- A FastAPI router the teammate's server can include with one line, plus a standalone app on seed data (keep GUARDIAN_DEV_SEEDED). User comes from the X-Demo-User-Id header, default demo-user. Match the teammate's app version (branch Reel-Watcher-Agent). Do not edit the iOS code or the teammate's code.
- JSON routes as in the handover: /chat, /chat/resume, /status, /memory, /questions, /questions/{id}/answer.
- POST /agent2/chat adapter: accepts {message, profile, context} and returns the ChatMessage shape the app decodes (id, sender "coachAgent", text, timestamp; attached_digest and client_action null).
  - A context digest becomes a drift-notification entry using the IDs in flagged_reels. Verify these are the reel IDs their JudgeDataSource knows.
  - Pass profile.stated_goals to the Guardian as background in onboarding.
  - Card fallback: a pending card is shown as plain text ending "Reply approve or reject." The decision is parsed in code, not by the model: exactly approve/yes or reject/no, case-insensitive, applied through resume or answer_question. Any other reply returns the card text again. No editing in the fallback.
  - Inbox through chat: at the start of a chat turn with no chat card pending, present pending inbox cards one at a time, oldest first. A "What I learned" card is shown as text and dismissed once shown; value cards use the same approve/reject fallback.
- Reflector trigger: export one function for their /session/end to call as a background task. Also start reflect_pending in the background on each /agent2/chat and GET /questions call.
- App feedback comments are canned strings: exclude every source="app" comment from the quote check.
- Tests: offline tests for every route and the fallback. Then one live run through HTTP on seed data: onboarding from stated_goals, approve by text, digest explanation, disagreement, reflect, inbox card through chat, approve, get_values changed.
- README: the one-line include, the JudgeDataSource wiring, every environment variable, running standalone behind a tunnel.
- docs/GUARDIAN_FOR_BACKEND_OWNER.md, one page for the teammate: the one-line include; the steps to run their migrations against the team database and whether their setup-store step is safe after ours; call get_values instead of the SELECT; call the session-end function; their Judge prompt must handle the values format including "Agreed wording", "Want more: none" and "Unless"; check whether ocr_text from the app is being dropped.

Cap Part C at about two hours. Stop and report after it.

## Integration (after Part C, done)

The teammates handed integration to the Guardian owner. Edits to `watcher_Agent/` were allowed for the wiring lines only.

1. Wire the Guardian into the Judge server: include `guardian_router` in `judge/api/app_api.py`; add `review_sessions(owner_id)` as a background task in `POST /session/end`; add the rule from `guardian/judge_prompt.md` to the Judge prompt. Run both test suites.
2. Run the Judge's migrations against the team database (check pgvector, then `migrate.py status` and `up`). Never run `grant` or `seed_dev_values.py`.
3. Write `docs/RUN_DEMO.md`: the `.env` lines and the one command for the combined server, the tunnel, a curl smoke test and the three most likely failures.
4. Add a demo reset command for one user's Guardian records and Judge rows. It names the user and refuses without `--yes`. Test it on the test container only.
5. Smoke-test the combined server against the team database.
6. Fix the two gaps: UUID evidence IDs, and no review for a session with no judged reels.
7. Push. The owner opens and merges the pull request from `guardian` into `main`.

## After that

A trimmed 4b: "none" for any value line the evidence does not support; a refinement changes only the lines the evidence supports and leaves the title, strength and other lines as they were; the interview focuses on "want less"; the Guardian speaks first on an empty message. Then the learning evaluation, then the ECC code and security review. Feature freeze is 4:00 am.
