---
name: session-review
description: Use at the start of every run to review one finished scrolling session and update memory.
---
# Review a session

Inputs are already in context: the user's values (read-only, in your first
message), brief.md and hypotheses.md.

1. Plan with write_todos using the steps below.
2. Call dump_session(session_id), dump_feedback(since) and
   dump_chatlog(since), using the session ID and "since" time from your first
   message. Call feed_profile(session_id).
3. Read the evidence with grep, not by reading whole files:
   - feedback labelled not_for_me or disagree, and their comments
     (grep "not_for_me|disagree" in /scratch/feedback.jsonl)
   - conflicts the user agreed with
   - chat turns where the user explained a reaction (/scratch/chats.md)
4. Update /memories/hypotheses.md (format below):
   - New pattern with at least two pieces of evidence: add it as watching.
   - Existing pattern seen again: add this session's ID to its evidence and
     update Last seen. Sessions seen is the number of distinct session IDs.
   - Sessions seen reaches 3: set Status to proposed and draft a value change.
   - Not seen in 5 sessions: delete it.
   Only the user's feedback and words count as strong evidence.
5. Draft value changes:
   - First, explicit feedback. Group the disagree and not_for_me marks in
     /scratch/feedback.jsonl by what the reels have in common (their topics),
     and count each group. Every group with three or more marks (this session
     or earlier) gets its own proposal now, up to two. A not_for_me mark counts
     even with no comment: if no stored value covers that kind of reel,
     propose a new value for it with "Suggested wording".
   - Then any hypothesis promoted in step 4.
   Prefer refining an existing value, often with an Unless line, over adding
   one. For a new value, write the header as "## New: <title> (strength: ...)".
   Wording: if the user wrote a comment or chat message that states it, use
   - In my words: "<their exact words>"
   Otherwise use
   - Suggested wording: "<your wording>"
   and make the card's prompt say the wording is a suggestion they can edit,
   for example: "You marked 3 reels like this as not for you. I've suggested
   wording below; edit it to put it in your own words."
   Never write an "In my words" quote the user did not write; it will be
   refused. Queue each draft with queue_question (at most 2; format below).
6. If the rerate-selection skill is available, follow it and queue one
   re-rate card. Otherwise skip this step.
7. Rewrite /memories/brief.md in under 150 words. Keep what still holds, add
   what changed, remove what is contradicted.
8. Write /memories/sessions/<session_id>.md (format below): a 2-3 sentence
   summary, then "What I learned" with 1-3 bullets. Each bullet is one plain
   sentence the user will read, citing evidence in brackets. Example:
   "You marked 3 gym-comparison reels as not for you, but kept watching form
   tutorials. [fb_21, fb_24, fb_25]"
   End with one open question about the pattern you saw, for the user to think
   about. Ask, do not advise, and do not suggest what they should value.
   Example: "What do you think made the comparison reels hard to scroll past?"

You cannot write the user's values. Never treat a pattern in viewing time
alone as a value.

## Formats

A value entry has exactly six lines. For a change to an existing value, copy
the stored entry and change only what the evidence supports; keep its wording
line exactly as it is unless you have an exact quote of the user.

```
## V1: <title> (strength: low | medium | high)
- Want more: ...
- Want less: ...
- Unless: ... (or "none")
- In my words: "<exact quote>"        (or: - Suggested wording: "<your wording>")
- Evidence: <feedback IDs, reel IDs or quotes>
```

Queue a value change like this:

```
queue_question(
  kind="value_change",
  prompt="<one or two sentences telling the user what you noticed>",
  value_change={
    "value_id": "V1",            # or "new"
    "new_text": "<the six lines above>",
    "evidence": "fb_001, fb_003, fb_005"
  }
)
```

hypotheses.md, one block per hypothesis:

```
## H1: <the pattern in one line>
- Evidence: s_001 (what was seen), s_004 (what was seen)
- Sessions seen: 2 · Last seen: 2026-11-14
- Status: watching
```

sessions/<session_id>.md, under 200 words:

```
# Session <session_id>
<2-3 sentence summary of what was watched and how it compared with their values>

## What I learned
- <one plain sentence the user will read> [fb_002, fb_004]
- <up to three bullets>

## A question for you
<one open question about the pattern, ending with a question mark>
```
