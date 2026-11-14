# Plan: Milestone 0 — Platform Spike

**Source PRD**: docs/project.prd.md
**Selected Milestone**: Milestone 0: Platform spike (about 3 hours)
**Complexity**: Medium (the iOS half is the risky part; the backend half is small)

## Summary
Find out early whether the live demo can work. Three questions: (1) can a free-Personal-Team broadcast extension upload frames while Instagram is open, (2) does a banner appear over Instagram during the broadcast, and (3) does a `create_deep_agent` Guardian run with persistent memory, an approval interrupt and our chosen middleware. If the answers are yes, Milestones 1 and 2 can start in parallel. If not, switch to the fallback paths the PRD already lists: the PiP pill for nudges, or WKWebView for capture.

## Patterns to Mirror
The repository is empty apart from `DOCS.txt` and the PRD. **There is no existing code to copy from**, so this milestone sets the conventions the later milestones will follow:

| Category | Source | Pattern (established here) |
|---|---|---|
| Naming | PRD §System architecture | Endpoints `POST /observe`, `/chat`, `/chat/resume`, `/feedback`, `GET /timeline`, `/values`; Python snake_case modules; Swift types UpperCamelCase |
| Errors | — (new) | FastAPI: response envelope `{ok, data, error}`; validate input with Pydantic models; log server-side with `logging`, never leak tracebacks. Extension: drop frame on any failure, never crash |
| Logging | — (new) | Backend `logging` at INFO with request id; extension uses `os.Logger` (subsystem = bundle id) |
| Data access | PRD §Data model | In-memory store for the spike; Mongo (`MongoDBStore`/`MongoDBSaver`) is not used until M1 |
| Tests | — (new) | `backend/tests/` with pytest + FastAPI `TestClient`, AAA layout, behaviour-named tests |

## Files to Change
| File | Action | Why |
|---|---|---|
| `.gitignore` | CREATE | Python, Xcode (`xcuserdata`, `DerivedData`), `.env` |
| `backend/pyproject.toml` | CREATE | fastapi, uvicorn, pydantic, deepagents, langgraph, model SDK, pytest, httpx |
| `backend/.env.example` | CREATE | `MODEL_API_KEY`, `GUARDIAN_MODEL`, `JUDGE_MODEL`; no secrets committed |
| `backend/app/main.py` | CREATE | FastAPI app, `GET /health`, stub `POST /observe` |
| `backend/app/schemas.py` | CREATE | Pydantic request/response models for `/observe` (frame b64, session_id, ts → verdict stub + optional nudge) |
| `backend/app/config.py` | CREATE | Loads env vars and fails at startup if a required one is missing |
| `backend/tests/test_observe.py` | CREATE | Envelope shape, size limit, bad base64 rejected, stub nudge returned on Nth frame |
| `backend/spikes/guardian_spike.py` | CREATE | Minimal `create_deep_agent` with memory file, `CompositeBackend`, `interrupt_on`, one custom middleware |
| `backend/tests/test_guardian_spike.py` | CREATE | values.md persists across two threads; interrupt fires before `propose_value_change` (model mocked/fake) |
| `ios/AttentionCompanion.xcodeproj` + `ios/AttentionCompanion/` | CREATE | SwiftUI app target: notification permission request, `RPSystemBroadcastPickerView` button, backend URL setting |
| `ios/BroadcastExtension/SampleHandler.swift` | CREATE | Throttle to 1 frame / 2 s, downscale to 512 px JPEG, single in-flight upload, drop when busy, raise local notification from `/observe` response |
| `docs/spike-results.md` | CREATE | Record pass/fail for each exit criterion, memory readings, latency, chosen nudge channel, verified middleware class names |

## Tasks

### Task 1: Repo scaffold and decisions
- **Action**: Add `.gitignore` and the `backend/` + `ios/` layout. **Block on the team to decide the model provider and get API keys** (PRD open decision); the Guardian spike in Task 5 needs them. Note: the PRD sample uses `anthropic:claude-sonnet-4-6`; current models are Sonnet 5.5 / Haiku 5.5, so use one of those if the team picks Anthropic.
- **Mirror**: PRD §System architecture layout.
- **Validate**: `git status` is clean after the commit; `.env` is ignored.

### Task 2: FastAPI skeleton (TDD)
- **Action**: Write `test_observe.py` first (RED): `/health` returns 200; `/observe` accepts a base64 JPEG ≤ ~200 KB and returns `{ok:true, data:{verdict:"neutral", nudge:null|{title,body}}}`; it rejects oversized or non-JPEG payloads with a 4xx and a clean error. Then implement (GREEN). The stub returns a fixed nudge every 5th frame for each session so Task 4 can exercise the banner without a model.
- **Mirror**: response envelope above.
- **Validate**: `cd backend && pytest -q --cov=app --cov-report=term-missing` (≥80%).

### Task 3: HTTPS reachability from the phone
- **Action**: Run `uvicorn app.main:app` behind a tunnel (`cloudflared tunnel --url http://localhost:8000` or ngrok) so iOS's App Transport Security accepts the URL with no exceptions.
- **Validate**: `curl https://<tunnel>/health` from the laptop, then load the same URL in Safari on the demo iPhone.

### Task 4: iOS app + Broadcast Upload Extension (physical device)
- **Action**:
  1. Create an Xcode project with the app target and add a Broadcast Upload Extension target. Sign both with **one** Apple ID's free Personal Team. **No App Group**: hardcode the backend URL in the extension for the spike, or pass it through the extension's Info.plist.
  2. App: request notification authorization at launch, show `RPSystemBroadcastPickerView` with `preferredExtension` set to the extension's bundle id.
  3. `SampleHandler.processSampleBuffer`: ignore anything that isn't `.video`; drop frames if less than 2 s has passed or an upload is in flight; convert with `CIImage` → scale to 512 px long edge → JPEG at quality 0.5; POST with `URLSession` (ephemeral config, short timeout). Release buffers right away.
  4. When the response carries a `nudge`, schedule a `UNNotificationRequest` with a nil trigger from inside the extension.
  5. Log memory use (`os_proc_available_memory` / task_info) every 10 frames.
- **Mirror**: PRD §Approach step 1 (stay tiny: no SDKs).
- **Validate** (manual, on device; record each in `docs/spike-results.md`):
  - [ ] Frames arrive at the backend while Instagram Reels is in the foreground (check the server log)
  - [ ] Extension memory stays well under 50 MB over a 5-minute scroll
  - [ ] With Settings › Notifications › Screen Sharing › Allow Notifications **on**, the stub banner appears over Instagram
  - [ ] Everything works on the free Personal Team with no App Group

### Task 5: Guardian prototype (TDD where possible)
- **Action**: In `guardian_spike.py`, build `create_deep_agent` with `memory=["/memories/values.md"]`, a `CompositeBackend` routing `/memories/` to a `StoreBackend` over an `InMemoryStore`, an `InMemorySaver` checkpointer, a stub `propose_value_change` tool, and `interrupt_on={"propose_value_change": {"allowed_decisions": ["approve","edit","reject"]}}`. Add one trivial custom middleware (a stand-in for frame-text-guard), plus the built-in summarization middleware.
- **Tests first**: (a) write values.md in thread A and read it from thread B with the same store; (b) a run that calls `propose_value_change` stops with an interrupt, and `Command(resume=...)` with approve completes it. Use a fake/scripted chat model so the tests are deterministic, and do one manual run against the real model.
- **Also verify and write down**: the exact import paths and class names for `SummarizationMiddleware`, `PIIMiddleware` and the custom `AgentMiddleware` base; whether `create_deep_agent(middleware=...)` accepts them; how the `permissions` argument makes values.md read-only. Check the installed deepagents version against the docs linked in the PRD; don't rely on memory for these.
- **Validate**: `cd backend && pytest -q tests/test_guardian_spike.py`; `python -m spikes.guardian_spike` prints the interrupt payload, then the resumed result.

### Task 6: Decide the nudge channel and record results
- **Action**: Fill in `docs/spike-results.md`. If banners were suppressed, timebox a 45-minute PiP-pill check (`AVPictureInPictureController` + `AVSampleBufferDisplayLayer` while Instagram plays audio). If capture failed, choose the WKWebView fallback. Tick the PRD's spike checkboxes.
- **Validate**: the team agrees on the go/no-go recorded in the doc.

## Validation
```bash
# backend
cd backend && python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'
pytest -q --cov=app --cov=spikes --cov-report=term-missing
uvicorn app.main:app --port 8000 &
curl -s localhost:8000/health
curl -s -X POST localhost:8000/observe -H 'content-type: application/json' \
  -d '{"session_id":"s1","ts":0,"frame_b64":"<small jpeg>"}'

# iOS: build from Xcode to a physical iPhone (the simulator can't run broadcast extensions), then run the manual checklist in Task 4
```

## Risks
| Risk | Likelihood | Mitigation |
|---|---|---|
| Banner suppressed during broadcast even with the Screen Sharing toggle on | Medium | Task 6 PiP check; the timeline in the app still shows verdicts |
| A broadcast extension can't post local notifications or lacks authorization | Medium | Request authorization in the containing app first (same team); if that fails, fall back to PiP or an in-app view |
| Extension killed at 50 MB | Medium | Reuse one `CIContext`, downscale before encoding, one upload in flight, measure in Task 4 |
| Personal Team signing problems (7-day expiry, 10 App IDs a week) | Medium | One Mac and one Apple ID own all builds; only 2 bundle ids |
| deepagents API differs from the PRD snippet (middleware, permissions, StoreBackend namespace signature) | Medium | Task 5 pins versions and records the real signatures before M1 depends on them |
| Model provider not decided | High (still open) | Raise it at kickoff; the spike tests use a fake model, so only the manual run is blocked |
| Tunnel URL changes on every restart | High | Make the URL a single constant in the extension for the spike; move to the Vultr host in M5 |

## Acceptance
- [ ] All tasks complete
- [ ] Validation passes (pytest ≥80% on `app/`, manual device checklist recorded)
- [ ] The four PRD spike exit criteria are marked pass/fail with a chosen nudge channel
- [ ] Real deepagents middleware/permissions signatures documented for M1
- [ ] Patterns established here are followed in M1/M2
