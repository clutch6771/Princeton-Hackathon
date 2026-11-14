# PRD — Values-Aligned Attention Companion (working title)

Nov 14, 2026 · HackPrinceton Fall 2026 · Living version: https://claude.ai/artifact/8vqMLvXLKpJgb66Jc9Yf7V

## Overview

An iPhone app that learns what a person wants from their short-video feed by talking with them, watches and listens to what Instagram actually plays, and tells them, with reasons, when the two drift apart. It learns over time from the user's feedback, and changes their stated values only with their approval.

It implements the "attention guardian" proposed by Schuster and Lazar in *Attention, moral skill, and algorithmic recommendation* (2024): independent middleware that determines the user's values, classifies the content they are exposed to, and predicts which content fits those values. Values come from conversation and explicit feedback, not from behavioural tracking.

Product principles from the paper:

- **Reasons, not scores.** Every flag cites one of the user's own stated values.
- **Scaffold, don't automate.** The app reports and asks; it never blocks content, and the user approves every change to their values.
- **The user's terms.** Notifications describe drift from what the user said they want, never "bad" content.

**Target user:** someone who feels their short-video feed has drifted from what they care about and wants help noticing it.

**Not in scope:** blocking or re-ranking Instagram's feed (not possible on iOS), parental controls, storing raw video or audio, platforms other than Instagram Reels for the demo.

## Level, type, skills

**Level:** Hard for a 31-hour build. Most of the risk is in integration: capture, upload, transcription, judging, notifications and agents all have to connect.

**Type:** AI agents, iOS, digital wellbeing and media literacy.

**Skills required:**

- Swift and SwiftUI on a physical iPhone; ReplayKit Broadcast Upload Extension (video and app audio); local notifications; a Share Extension (stretch)
- Python with FastAPI; background jobs
- Gemini API for reel understanding (frames and audio in one call)
- Gemini API for both agents
- LangChain `create_agent` and `deepagents`
- Embeddings and vector search
- MongoDB Atlas
- Deploying an HTTPS backend (Vultr instance or tunnel)

## User flow

1. **Onboarding.** The chat agent asks up to three questions about what the user wants more and less of, then proposes a first value. The user approves, edits or rejects it.
2. **Start watching.** A one-screen explainer says what screen recording captures (screen and app audio) and that the user starts and stops it. The user starts the broadcast and opens Instagram.
3. **Capture and upload.** The broadcast extension samples one frame every 2 seconds and records app audio in 5-second chunks, then uploads both. No video is uploaded.
4. **Judging.** The backend splits the stream into reels, transcribes each reel's audio, and judges each reel against the user's values.
5. **Batch notification.** After a set number of reels, if enough of them conflicted, the user gets a banner such as "4 of your last 15 reels went against what you told me you wanted."
6. **Explanation.** Tapping the banner opens the chat on those reels. The agent explains each one by naming the value it touched, and the user can agree, disagree or ask questions.
7. **Session end.** When recording stops, the Reflector (a background deep agent) reviews the session, updates what the system knows about the user, and prepares a short "What I learned" summary plus any proposed value changes as approval cards.
8. **Re-rating (optional).** At most once a day, the user gets a notification asking them to re-rate two or three reels the system was unsure about.
9. **"Not for me."** At any time, the user can open the Timeline of recent reels (thumbnails and descriptions) and mark one "Not for me", with an optional comment. Stretch: share a reel link from Instagram with a comment through the Share Extension.

## Platform constraints (iOS)

iOS provides no API for drawing over another app or reading its content. The app senses through a user-started screen broadcast and reaches the user through notifications and its own screens.

| Need | Possible? | How | Catch |
| --- | --- | --- | --- |
| See what Instagram shows | Yes | ReplayKit Broadcast Upload Extension receives system-wide video frames | User starts it each session; recording indicator shows; 50 MB memory cap; physical device only |
| Hear what Instagram plays | Yes, to verify | The same extension receives app-audio sample buffers | Protected content may be silent; confirm Instagram audio arrives in the spike |
| Overlay on Instagram | No | No API | Notifications instead |
| Notify during recording | Yes, with a setting | Local notification raised by the extension | Hidden while screen sharing unless Settings > Notifications > Screen Sharing > Allow Notifications is on |
| Floating status pill | Stretch | Picture in Picture with custom-rendered frames | Workaround, not a supported overlay |
| Receive a reel from Instagram | Link only | Share Extension | Instagram videos cannot be downloaded; use the link and the user's comment |
| Full-screen block | No | Screen Time shield needs a paid developer account | Out of scope: team uses free Personal Teams |

**Free developer accounts:** builds expire after 7 days, 10 App IDs per week, and teammates cannot share bundle IDs. Build to the demo phone from one Mac and one Apple ID. Avoid App Groups unless the spike shows they work; the extension talks to the backend directly.

**Platform spike (Milestone 0 exit criteria)**

- [ ] Extension receives and uploads frames while Instagram is in the foreground
- [ ] Extension receives Instagram's audio (not silence) and uploads 5-second compressed chunks
- [ ] Extension stays under 50 MB for 10 minutes of scrolling
- [ ] A banner appears over Instagram during the broadcast with the Screen Sharing toggle on
- [ ] App, extension and banner work when signed with a free Personal Team

## System architecture

Five parts connected through the backend: the iPhone app and its extensions, the ingest pipeline with the Judge, the chat agent, the Reflector, and one database.

- **iPhone:** broadcast extension (1 frame every 2 s and 5 s audio chunks, uploads both, raises the batch notification); app screens (chat with approval cards, Timeline with "Not for me", inbox cards, values).
- **Backend:** Ingest and Judge (splits reels, transcribes audio, judges frames plus transcript against values, decides to notify); chat agent (`create_agent`); Reflector (deep agent, runs after a session ends).
- **Database:** memory files; reels and feedback; questions and chat threads.

Data flow: extension → `/ingest` → Judge → reels collection → (notify) → extension banner → app chat → chat agent. Session end → Reflector → memory files and question queue → app.

The Judge and both agents never call each other. They share the database: the Judge writes reels and verdicts, the Reflector writes memory files and questions, and the chat agent reads both.

## Integration contracts

Most of the work is in these connections. Each row is a contract two owners agree on before building.

| Connection | Transport | Payload | Owner (sender / receiver) |
| --- | --- | --- | --- |
| Extension → backend: frames | `POST /ingest/frame` | session ID, timestamp, 512 px JPEG | iOS / backend |
| Extension → backend: audio | `POST /ingest/audio` | session ID, start and end time, 16 kHz mono AAC, 5 s | iOS / backend |
| Backend → extension: notify | Response body of either ingest call | `{"notify": {"title", "body", "reel_ids"}}` or `null` | backend / iOS |
| Ingest → Judge | In-process queue | reel ID, 2–3 frames, transcript | backend / Judge |
| Judge → database | Insert | `Reel` record (below) | Judge / database |
| App → chat agent | `POST /chat`, `POST /chat/resume` | message, context (entry point, reel IDs), approval decisions | iOS / Guardian |
| App → backend: feedback | `POST /feedback` | reel ID, label (`agree`, `disagree`, `not_for_me`, `fine`), source, comment | iOS / backend |
| App ← backend: questions | `GET /questions` | The inbox: "What I learned" cards, approval cards, and re-rate requests if built | Guardian / iOS |
| App → backend: session end | `POST /session/end` | session ID. Ten idle minutes also count as a session end, and pending sessions are reviewed when the app opens | iOS / backend |
| App ← backend: timeline | `GET /timeline` | Recent reels with thumbnail, description, verdict | backend / iOS |
| Share Extension → backend (stretch) | `POST /share` | reel link, comment | iOS / backend |
| Judge ← values | In-process call `get_values(user_id)` | values.md as text; empty if none | Guardian / Judge |

**Reel record (written by the Judge, read by the agents and app):**

```json
{
  "id": "rl_014",
  "session_id": "s_003",
  "start": "2026-11-14T21:03:12Z",
  "end": "2026-11-14T21:03:41Z",
  "thumbnail_url": "/thumbs/rl_014.jpg",
  "transcript": "okay so here's why you're never going to look like this...",
  "on_screen_text": "90 days. No excuses.",
  "description": "Before-and-after body transformation montage with voice-over",
  "features": {
    "topics": ["fitness", "body transformation"],
    "format": "before/after montage",
    "tone": "aspirational",
    "hook": "shock reveal in the first second",
    "emotional_pull": "comparison",
    "is_ad": false
  },
  "verdict": "conflict",
  "confidence": 0.82,
  "value_ids": ["V3"],
  "reason": "Shows body comparison, which V3 says you want less of."
}
```

All services use one demo user ID, sent as a header. Thumbnails are the only images kept.

**Guardian functions behind the backend routes.** The Guardian package exposes Python functions only; the backend owner wraps them in routes. The Guardian build spec is the source of truth for field definitions.

| Backend route | Guardian function | Returns | Scope |
| --- | --- | --- | --- |
| `POST /chat` | `chat` (one thread per user per day) | Reply, or a pending value card | MVP |
| `POST /chat/resume` | `resume` | Reply, or a validation error | MVP |
| `GET /questions` | `get_questions` | Inbox cards: "What I learned", value changes | MVP |
| `POST /questions/{id}/answer` | `answer_question` | OK, or a validation error | MVP |
| `GET /memory` | `get_memory` | Stored memory files as text, read-only | MVP |
| `GET /status` | `has_values` | Whether recording may start | MVP |
| Background job on session end, app open and a 10-minute timer | `reflect_pending` | Number of sessions reviewed | MVP |
| Called by the Judge for each reel | `get_values` | values.md as text | MVP |
| `DELETE /memory/{file}/{item}` | `delete_memory_item` (notes and hypotheses only) | Nothing | Optional |

**Value cards.** A proposed value always has the same four fields: value ID, old text, new text and evidence. In chat it arrives with the reply and is answered through `/chat/resume`; in the inbox it arrives as a card and is answered through `/questions/{id}/answer`. The app can use one card component for both. An approval or edit that fails a check (format, a card already answered, a value that changed since the card was made) changes nothing and returns an error to show. While a value card is waiting in chat, `/chat` returns the same card again until it is answered. A card's text may include a line starting `Suggested wording:`; approving confirms that wording and editing replaces it, with no extra UI.

**Judge output.** The Judge returns the transcript, on-screen text, description, features, verdict, confidence, value IDs and reason; the backend adds the ID, session, times and thumbnail to form the reel record.

## Reel understanding

Frames alone miss talking-head reels with no captions, so each reel is judged on three signals: frames, on-screen text and an audio transcript.

**Pipeline per reel**

1. **Segment.** A new reel starts when consecutive frames differ sharply (a scroll replaces the whole screen). Frames and audio chunks are grouped by timestamp into reels.
2. **Filter.** The first frame is checked for being a short-video feed; anything else (messages, other apps) is dropped, with its audio.
3. **Join audio.** The reel's audio chunks are joined into one clip.
4. **Judge.** One Gemini call receives 2–3 frames, the audio clip, values.md and up to 3 similar reels the user gave feedback on. It returns the transcript and the other reel record fields as structured output. Music-only audio gives an empty transcript, which is fine.
5. **Store and index.** The record is saved with an embedding of its description and transcript; the frames and audio are discarded except one thumbnail.

**Latency budget:** under about 10 seconds from the end of a reel to its verdict. Batch notifications make this budget comfortable.

**Notification policy (backend, configurable)**

- Check after every 15 judged reels.
- Notify if at least 3 of those reels conflicted with a value; otherwise stay silent.
- At most one notification every 10 minutes.
- Wording reports drift from the user's values: "4 of your last 15 reels went against what you told me you wanted." Never "bad reels".
- Tapping opens the chat with those reel IDs as context.

## Agents

Two agents with different jobs. The chat agent, called the Guardian in the build spec, is fast and small; the Reflector is slow, thorough and runs in the background.

| | Chat agent | Reflector |
| --- | --- | --- |
| Built with | LangChain `create_agent` | `deepagents` `create_deep_agent` |
| Runs | While the user chats | After a session ends; can be re-run on demand |
| Job | Values interview, explaining flags, handling disagreement | Reviewing the session, updating memory, preparing questions |
| Context per call | Short system prompt, values.md, user brief, 8 tools | Planning, file tools, session data offloaded to files |
| Writes | Value proposals (with approval), notes | Brief, hypotheses, session summary, question queue |
| User waiting? | Yes, so few model calls per message | No, so it can run under rate limits |

**Why the Reflector is a deep agent.** Reviewing a session is long, multi-step work over more data than fits in a prompt: dozens of reel records, feedback and chat transcripts. It uses planning (`write_todos`), offloads the session to files and searches them, maintains long-term memory files, and queues value changes for user approval. LangChain's own memory docs describe this background "consolidation" pattern for deep agents.

**How they communicate.** Only through the database: the Reflector writes memory files and a question queue; the chat agent reads the files on every call and the app shows the queue.

## Memory

The system never turns behaviour into values on its own. What it notices becomes a question for the user.

| Layer | Holds | Written by | Loaded into chat |
| --- | --- | --- | --- |
| Values (`values.md`) | Approved values in the user's words | Only by user approval | Always |
| Brief (`brief.md`) | ~150 words: who the user is, how to talk to them, current focus | Reflector | Always |
| Hypotheses (`hypotheses.md`) | Patterns noticed, each with evidence, count and last-seen date | Reflector | Never; they become questions |
| Notes (`notes.md`) | Things the user said about their life and feelings | Chat agent | On demand |
| Session summaries | One short summary per session | Reflector | On demand |
| Raw history | Reel records, feedback, chat threads | Judge, app, checkpointer | Through tools only |

**Rules**

- Every memory item cites evidence: a quote, a reel ID or a feedback ID.
- Newer explicit statements override older ones.
- A hypothesis seen in 3 sessions becomes a proposed value change; one not seen in 5 sessions is dropped.
- values.md and brief.md have size caps so the chat prompt stays small.
- MVP: the user can view everything stored about them on a read-only screen. Deleting individual notes or hypotheses is optional; values change only through a value card.

**values.md entry format (also read by the Judge):**

```markdown
## V3: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: disagreed with rl_112; approved 2026-11-14
```

The fifth line reads `In my words` when the wording is the user's own (quoted from them or typed by them), or `Agreed wording` when the app suggested it and the user approved it. The Judge treats both the same.

## Learning evaluation

One small experiment shows whether the system learns, and from what. A simulated user (a one-page persona) labels every test reel in advance. Twenty held-out reels are used only for scoring: they are never given as feedback and never shown to the Reflector.

| Stage | Values the Judge uses | Feedback examples the Judge sees | Shows |
| --- | --- | --- | --- |
| 1. Baseline | Initial approved values | None | Starting point |
| 2. Feedback learning | Initial approved values, unchanged | The user's feedback on 12 other reels | What examples alone add |
| 3. Approved value refinement | Values after the Reflector's proposal is approved | Same as stage 2 | What the approved change adds on top |

Each stage reports how many of the 20 held-out reels the Judge calls the way the persona would, plus the count on the roughly 8 reels in the pattern the feedback was about. It uses a text-only stand-in for the Judge (the same prompt on reel descriptions and transcripts), about 60 model calls and one Reflector run. If the Reflector proposes nothing, stage 3 is reported as "no proposal". With 20 reels, a difference of one or two is within noise.

## Data storage and privacy

One database holds everything: MongoDB Atlas by default (Tiger Data is an equivalent alternative; pick one).

| Collection | Holds |
| --- | --- |
| store (managed by `MongoDBStore`) | Memory files: values, brief, hypotheses, notes, session summaries |
| checkpoints (managed by `MongoDBSaver`) | Chat threads and pending approvals |
| reels | Reel records with transcript, features, verdict and embedding (vector index) |
| feedback | Agree / disagree / not for me / fine, with comment and source (app, chat, re-rate, share) |
| questions | Pending inbox cards, with status |
| sessions | Start, end, reel counts, notification log |

**Privacy:** frames and audio are processed in memory and discarded; only text, one thumbnail per reel and embeddings are stored. Capture runs only while the user has started a broadcast, and non-feed screens are dropped before transcription.

## Models and rate limits

| Job | Model | Notes |
| --- | --- | --- |
| Transcription | Done inside the Judge call; Gemini accepts audio | One call per reel |
| Judge | Gemini Flash-Lite or Flash: frames and audio in, structured output | One call per reel |
| Chat agent | Gemini Flash | 2–4 calls per user message |
| Reflector | Gemini Flash | Runs in the background with a rate limiter |
| Embeddings | Gemini embeddings | One per reel |

The team uses the Gemini API free tier throughout, one key per teammate so the Judge's per-reel calls do not use up the agents' quota, with a local model for development. Tokens per minute is usually the binding limit, so prompts stay short, the Judge sends small frames, and the Reflector queues its calls. Exact providers are set by environment variables.

## Milestones and owners

Submission deadline: Sunday, Nov 15, 8:00 am. Feature freeze: Sunday 4:00 am.

**MVP demo loop.** The hackathon build is one loop: onboarding, value approval, a drift notification and reel explanation, feedback (disagreement and "Not for me"), then the Reflector's "What I learned" card and a proposed value change the user approves. Three explicit feedback marks on one kind of reel are enough for the Reflector to propose a change, so the loop can be shown in one session. Everything outside this loop is optional.

**Integration checkpoints.** Saturday 9:00 pm: chat, value approval and the Judge's values lookup running through the backend with fake reel data. Sunday 1:00 am: the full loop on the demo phone with real Judge output.

| Milestone | Scope | Owner |
| --- | --- | --- |
| M0 Platform spike (3 h) | Frame and audio capture, upload, banner during recording | iOS |
| M1 Ingest and Judge (6 h) | Ingest endpoints, segmentation, transcription, Judge, reel records | Backend / Judge |
| M2 Chat agent (6 h) | Interview, approvals with the protected value-write path, then the injection guard, then explanations; `chat` / `resume` on fake data | Guardian |
| M3 App screens (6 h) | Chat with value cards, Timeline with "Not for me", inbox, read-only Values screen | iOS |
| M4 Notifications and feedback (4 h) | Notification policy, `/feedback`, `/questions` | Backend |
| M5 Reflector (4 h) | Session review, memory updates, "What I learned" and value-change cards | Guardian |
| M6 Integration and ship (4 h) | End-to-end on the demo phone, hosted backend, README, demo | All |

Optional, in this order, once the MVP loop works end to end: re-rate cards and the daily re-rate notification; evidence checker; deleting memory items; Share Extension; a second model provider as fallback; PiP pill; streaming chat replies.

## Tracks and judging

| Track | What it asks | How this project answers |
| --- | --- | --- |
| Best use of deepagents (LangChain) | Build a deep agent | The Reflector: long-running background review with planning, file offloading, long-term memory and approval-gated updates |
| Best Practical AI Innovation (Amazon) | Creativity, usefulness, technical implementation, UX; agentic behaviour and learning from feedback | Learns from "not for me" feedback and approved value changes; AI layer on an existing platform |
| Main track (pick one) | One of six | Suggested: Entertainment and Media (media literacy) |
| Best Overall | Creativity, utility, charity, avidity | User-controlled alternative to engagement-driven feeds |

**Hosting:** the backend needs a public HTTPS URL. A Vultr instance qualifies for a Vultr prize if one is offered; a local tunnel is used during development. Prize eligibility for Vultr, MongoDB and Tiger Data is to be confirmed on Devpost.

## Risks and open decisions

The largest risks are in capture and integration, not in the AI.

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Instagram audio is silent in the capture | Talking-head reels misjudged | Verify in M0; fall back to frames and on-screen text |
| Extension exceeds 50 MB | Capture stops | Downscale immediately, small audio chunks, one upload in flight |
| Upload bandwidth on venue Wi-Fi | Late verdicts | Small frames, compressed audio, batch notifications tolerate delay |
| Banner hidden during recording | No nudge | Screen Sharing notification setting on the demo phone |
| Reel segmentation errors | Two reels merged or split | Threshold tuning on recorded test sessions |
| Free-tier rate limits | Stalls during demo | Fallback provider, rate limiter, background Reflector, short prompts |
| Prompt injection through captions or speech | Agent manipulated | Frame and transcript text treated as untrusted data; agents cannot edit values directly |
| Contracts drift between teammates | Broken integration at the end | Agree on the contracts table before M1; shared fake data |

**Decisions**

- [x] Paid Apple Developer account: none, building on free Personal Teams
- [ ] Main track: Entertainment and Media, or Healthcare
- [ ] One Gemini API key per teammate
- [ ] MongoDB Atlas (default) or Tiger Data
- [ ] Notification thresholds (default: 3 conflicts in 15 reels, 10-minute gap)

## Demo script (2 minutes)

1. **Problem (15 s).** Feeds optimise for engagement, not for what you care about.
2. **Values (25 s).** Tell the agent one thing you want less of; approve the value card.
3. **Watch (30 s).** Start recording, scroll Instagram, including a talking-head reel with no captions. A batch notification arrives: "3 of your last 15 reels went against what you told me."
4. **Explain (20 s).** Tap it; the agent names the value each reel touched, quoting the talking-head reel's audio.
5. **Learn (20 s).** Disagree with one and mark two similar reels "Not for me". End the session and show the Reflector's "What I learned" card and the value change it proposes; approve it.
6. **Under the hood (10 s).** One slide: capture, Judge, chat agent, Reflector deep agent, memory.

Preparation: pre-seeded values, a recorded backup of the full loop.

## Sources

- Schuster and Lazar, [Attention, moral skill, and algorithmic recommendation](https://doi.org/10.1007/s11098-023-02083-6), Philosophical Studies, 2024
- HackPrinceton Fall 2026 On-Site Hacker Packet
- LangChain, [Deep Agents memory](https://docs.langchain.com/oss/python/deepagents/memory), [backends](https://docs.langchain.com/oss/python/deepagents/backends), [fault tolerance](https://docs.langchain.com/oss/python/deepagents/fault-tolerance)
- MongoDB, [LangGraph integration](https://www.mongodb.com/docs/atlas/ai-integrations/langgraph/)
- Apple Developer Forums: [Family Controls on a Personal Team](https://developer.apple.com/forums/thread/852204), [notifications during screen recording](https://developer.apple.com/forums/thread/694768)
- Fora Soft, [screen sharing with ReplayKit and the 50 MB limit](https://www.forasoft.com/blog/article/how-to-implement-screen-sharing-in-ios-1193)
