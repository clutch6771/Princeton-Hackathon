# Persona: Maya (simulated user)

Maya is the simulated user behind every seed file. `build_seed.py` turns this page into `reels.json`, `feedback.json`, `initial_values.json` and `heldout_labels.json`. She is not a real person; the reels are invented, modelled on common short-video content.

## Who she is

Third-year biology student. Scrolls Instagram Reels to unwind after studying, mostly between 9 pm and midnight. Goes to the gym three times a week and tore a knee ligament playing football two years ago, so she follows rehab content closely. Cooks for herself on a student budget. Prefers short, direct replies and dislikes being lectured.

## What she told the Guardian at onboarding (her three approved values)

These are the "initial approved values" used by the tests and as stage 1 of the learning evaluation.

### V1: Fitness without body comparison (strength: high)

- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- Her words: "gym stuff is great, comparing bodies is not"

### V2: News without outrage (strength: medium)

- Want more: calm explainers, local news, science
- Want less: rage-bait politics, people shouting at each other
- Unless: none
- Her words: "I want to know what happened, not be told who to be angry at"

### V3: Cooking I can actually make (strength: low)

- Want more: simple recipes, budget meals, meal prep
- Want less: extreme food challenges, mukbang
- Unless: none
- Her words: "show me dinner I can cook on a Tuesday"

## What she has not said yet (the two things the system should learn)

The initial values miss two of her real preferences. The feedback session exposes both, and the held-out set tests whether the system picked them up.

### Pattern A: injury rehab is fine, even when it shows a before and after

V1 says "less before/after transformations", so the Judge flags rehab progress reels as conflicts. Maya disagrees: recovery is about getting better, not looking better. She says so in writing each time, which gives the Reflector exact quotes.

- Her reaction: `disagree`, with a comment
- Expected learning: a refinement of V1 with an "Unless" line for injury rehab and recovery progress
- Judge's call before learning: conflict. Maya's label: not a conflict

### Pattern B: hustle and get-rich-quick content is not for her

None of her values mention it, so the Judge calls these reels neutral. Maya marks them "Not for me" and does not explain.

- Her reaction: `not_for_me`, no comment
- Expected learning: a new value, proposed with `Suggested wording` because she wrote nothing to quote
- Judge's call before learning: neutral. Maya's label: conflict

## How she reacts in general

| Content | Her reaction |
| --- | --- |
| Form tutorials, training plans, stretching | Fine; wants more |
| Before/after body transformations, physique rating | Conflict; agrees with the flag |
| Injury rehab and physio progress | Fine; disagrees with a flag, and says why |
| Calm explainers, local news, science | Fine; wants more |
| Shouting politics, rage-bait | Conflict; agrees with the flag |
| Simple recipes, budget meal prep | Fine; wants more |
| Mukbang, extreme food challenges | Conflict; agrees with the flag |
| Hustle culture, get-rich-quick, crypto pitches | Conflict; marks "Not for me" without comment |
| Pets, travel, study tips | Neutral |

## The three data sets

| Set | Session ID | Reels | Purpose |
| --- | --- | --- | --- |
| Feedback session | `s_001` | 12 | The only reels the system learns from. Maya gives 5 `disagree` marks with comments (pattern A) and 3 `not_for_me` marks without comments (pattern B) |
| Demo session | `s_002` | 8 | The drift-notification demo: a body-transformation montage, a talking-head reel whose meaning is only in the audio, shouting politics, a mukbang, and one reel carrying a prompt-injection attempt |
| Held-out set | `s_heldout` | 20 | Scoring only. 4 reels in pattern A, 4 in pattern B, 12 clear cases. Never given as feedback and never listed as a session, so the Reflector does not review it |

## Held-out labels

`heldout_labels.json` records, for each held-out reel, whether Maya would call it a conflict and whether it belongs to pattern A or B. **These 20 labels are the ones to check by hand.**
