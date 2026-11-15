# Learning evaluation: results

Run on the night of Nov 14 to 15, 2026, between 11:52 pm and 12:08 am Eastern (04:52 to 05:08 UTC on Nov 15), with `python -m guardian.eval.learning_eval`. Stand-in Judge and Reflector: `gemini-3.5-flash-lite`. The method is in `docs/GUARDIAN_SPEC_V5.md` ("Learning evaluation") and at the top of `learning_eval.py`.

Every run is reported here, not only the last. The full output of both script runs is at the end.

## Result

A match is a conflict / not-conflict call by the stand-in Judge that agrees with the simulated persona's label. 8 of the 20 held-out reels are in the pattern the feedback is about (4 injury-rehab reels the persona is fine with, 4 hustle reels the persona does not want).

| Stage | Values given to the Judge | Feedback examples | Matches | In-pattern | Which run |
| --- | --- | --- | --- | --- | --- |
| 1. Baseline | Initial V1 to V3 | None | 16 of 20 | 4 of 8 | Run 1 |
| 2. Feedback learning | Initial V1 to V3 | Up to 3 similar reels | 17 of 20 | 5 of 8 | Run 2. Run 1 gave 12 of 20 with 7 invalid verdicts (below) |
| 3. Approved value refinement | V1 with a new "Unless" line, plus a new V4 | Same as stage 2 | 19 of 20 | 8 of 8 | Run 2. One reply could not be read (below) |

With 20 reels, a difference of one or two is within noise. Stage 1 to stage 2 (16 to 17) is inside that. Stage 1 to stage 3 is three reels overall and four of the eight in-pattern reels.

What moved, reel by reel:

- **Stage 2:** `rl_023` (a physio comparing ankle mobility before and after) went from conflict to aligned once similar rehab reels the persona had disagreed about were shown as examples.
- **Stage 3:** `rl_026`, `rl_027` and `rl_028` (hustle and get-rich-quick reels) went from neutral to conflict once the new value V4 existed. Examples alone had not moved them.
- **Stage 3, one unreadable reply:** for `rl_035` the Judge's JSON was malformed inside `features` (unquoted words in a list). Its `verdict` field said "aligned", which would have matched the persona's label, but the script counts a reply it cannot parse as no match. The parser was not changed after seeing this.

## What the Reflector queued

The Reflector step was run three times in all. All three are listed.

| Run | What happened | What it queued |
| --- | --- | --- |
| 1. In the first script run | The review hit the Reflector's limit of 25 model calls and was rolled back, as designed | Nothing. Stage 3 was reported as "no proposal" |
| 2. A traced diagnostic run, outside the script, to see why run 1 failed | Completed in 14 model calls. No refusals from the code checks | A refinement of V1 (an "Unless" line for rehab) and a new hustle value. Not committed, not approved, not used for any stage |
| 3. The retry in the second script run (`--retry-reflector`) | Completed | A refinement of V1 and a new hustle value, plus the "What I learned" card. Both value cards were approved unchanged through `answer_question`; stage 3 uses the result |

So two of the three runs completed, and both of those queued the hustle value as well as the V1 refinement. Run 1 queued neither. The diagnosis of run 1: no cause was found in the code; the traced run shows half of a normal review's model calls go to updating its to-do list, so a run that wanders can reach 25. In the product a failed review is rolled back and retried on the next call, up to three times.

The two cards approved in run 3, as queued:

```text
## V1: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: injury rehab and recovery progress
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: said at onboarding; approved 2026-11-15; fb_001, fb_003, fb_005, fb_006, fb_008

## New: Hustle culture and get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, crypto, and dropshipping pitches
- Unless: none
- Suggested wording: "I want practical skills, not get-rich-quick sales pitches"
- Evidence: fb_002, fb_004, fb_007
```

The V1 card changes only the "Unless" line and adds the feedback IDs. The second was stored as V4 with `Agreed wording`, because the user approved wording the app suggested.

## What changed between the two script runs

Two changes were made to the evaluation script after run 1, in commit `6053bd3`. Both were made after seeing results on the held-out reels, so stage 2 is not a blind measurement of the example format.

1. **How a feedback example is shown to the Judge.** In run 1 an example read "The person marked it: disagree." The Judge then returned "disagree", "not_for_me", "keep" or "dislike" as its verdict on 7 of the 20 reels, which are not verdicts. An example now states the verdict it was a reaction to and what the reaction means ("It was judged conflict. The person's reaction: they disagreed with that verdict."). The Judge prompt itself was not changed.
2. **A second Reflector run has to be asked for** (`--retry-reflector`), and the report lists every Reflector run.

Stage 1 was not run again: its prompts did not change, so run 2 read it from the cache.

## Caveats

- With 20 held-out reels, a difference of one or two between stages is within noise.
- **This is not a blind test of the session-review skill.** The Reflector is run on the feedback session (`s_001`), and the skill was changed after seeing the Reflector's output on that same session: (1) after the first live run, step 5 was reworded to group the disagree and not_for_me marks, count each group, and propose for every group with three or more marks; (2) format examples that happened to use seed IDs were replaced with made-up IDs; (3) in step 4b the rules for refinements (change only the lines the evidence supports) and for "none" lines were added, again after live reviews of that session. The held-out reels and their labels were never shown to the Reflector or used to adjust the skill, the prompts or the checks.
- The example format in the evaluation script was changed after run 1, as described above.
- The Judge is a text-only stand-in that runs the appendix Judge prompt on each reel's description, on-screen text and transcript. It is not the team's Judge.
- Temperature 0 is requested, but the library reports that `gemini-3.5-flash-lite` uses fixed sampling and ignores it. A repeated run of a stage can differ; stages 1 and 3 were each run once.
- The held-out labels are the simulated persona's, generated with the seed data.
- The seed data is invented. This shows the mechanism works on it; it is not evidence about real users.

## Full output, script run 2 (the reported result)

```text
LEARNING EVALUATION

Stand-in Judge: gemini-3.5-flash-lite, text only, on the 20 held-out reels.
A match is a conflict / not-conflict call that agrees with the persona's label.

Stage 1  Baseline                    16 out of 20    in-pattern: 4 out of 8
Stage 2  Feedback learning           17 out of 20    in-pattern: 5 out of 8
Stage 3  Approved value refinement   19 out of 20    in-pattern: 8 out of 8   (1 unreadable)

Per reel
reel     in-pattern  persona        stage 1    stage 2    stage 3
rl_021   yes         not conflict   aligned    aligned    aligned   
rl_022   yes         not conflict   aligned    aligned    aligned   
rl_023   yes         not conflict   conflict   aligned    aligned   
rl_024   yes         not conflict   aligned    aligned    aligned   
rl_025   yes         conflict       conflict   conflict   conflict  
rl_026   yes         conflict       neutral    neutral    conflict  
rl_027   yes         conflict       neutral    neutral    conflict  
rl_028   yes         conflict       neutral    neutral    conflict  
rl_029   no          conflict       conflict   conflict   conflict  
rl_030   no          conflict       conflict   conflict   conflict  
rl_031   no          not conflict   aligned    aligned    aligned   
rl_032   no          not conflict   aligned    aligned    aligned   
rl_033   no          conflict       conflict   conflict   conflict  
rl_034   no          not conflict   aligned    aligned    aligned   
rl_035   no          not conflict   aligned    aligned    error     
rl_036   no          conflict       conflict   conflict   conflict  
rl_037   no          not conflict   aligned    aligned    aligned   
rl_038   no          not conflict   neutral    neutral    neutral   
rl_039   no          conflict       conflict   conflict   conflict  
rl_040   no          not conflict   neutral    neutral    neutral   

What the Reflector queued (one run on the feedback session)
reflect_pending reviewed 1 session(s) and left 3 card(s) in the inbox.

Card 1: value_change
Prompt: You disagreed with 5 before/after transformation reels because they were about injury recovery and physical therapy rather than physique comparison. I've suggested an update to your fitness value to include injury recovery and rehab as an exception. You can edit this wording if you like.
Target: V1    Evidence: fb_001, fb_003, fb_005, fb_006, fb_008
## V1: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: injury rehab and recovery progress
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: said at onboarding; approved 2026-11-15; fb_001, fb_003, fb_005, fb_006, fb_008

Card 2: value_change
Prompt: You marked 3 hustle culture and money-making talking head reels as not for you. I've suggested wording below for a new value; edit it to put it in your own words.
Target: new    Evidence: fb_002, fb_004, fb_007
## New: Hustle culture and get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, crypto, and dropshipping pitches
- Unless: none
- Suggested wording: "I want practical skills, not get-rich-quick sales pitches"
- Evidence: fb_002, fb_004, fb_007

Card 3: learned
Prompt: - You value physical therapy and injury recovery progress videos even when presented as before/after montages, distinguishing them from body comparison. [fb_001, fb_003, fb_005, fb_006, fb_008]
- You dislike hustle culture, dropshipping, and crypto sales pitches. [fb_002, fb_004, fb_007]

What do you find most valuable when watching physical therapy recovery content compared to standard fitness transformations?

Value-change cards approved unchanged through answer_question: 2

Values given to the Judge in stages 1 and 2
## V1: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: said at onboarding; approved 2026-11-15

## V2: News without outrage (strength: medium)
- Want more: calm explainers, local news, science
- Want less: rage-bait politics, people shouting at each other
- Unless: none
- In my words: "I want to know what happened, not be told who to be angry at"
- Evidence: said at onboarding; approved 2026-11-15

## V3: Cooking I can actually make (strength: low)
- Want more: simple recipes, budget meals, meal prep
- Want less: extreme food challenges, mukbang
- Unless: none
- In my words: "show me dinner I can cook on a Tuesday"
- Evidence: said at onboarding; approved 2026-11-15

Values given to the Judge in stage 3
## V1: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: injury rehab and recovery progress
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: said at onboarding; fb_001, fb_003, fb_005, fb_006, fb_008; approved 2026-11-15

## V2: News without outrage (strength: medium)
- Want more: calm explainers, local news, science
- Want less: rage-bait politics, people shouting at each other
- Unless: none
- In my words: "I want to know what happened, not be told who to be angry at"
- Evidence: said at onboarding; approved 2026-11-15

## V3: Cooking I can actually make (strength: low)
- Want more: simple recipes, budget meals, meal prep
- Want less: extreme food challenges, mukbang
- Unless: none
- In my words: "show me dinner I can cook on a Tuesday"
- Evidence: said at onboarding; approved 2026-11-15

## V4: Hustle culture and get-rich-quick content (strength: medium)
- Want more: none
- Want less: hustle culture, crypto, and dropshipping pitches
- Unless: none
- Agreed wording: "I want practical skills, not get-rich-quick sales pitches"
- Evidence: fb_002, fb_004, fb_007; approved 2026-11-15

Every run that called a model, oldest first
2026-11-15T04:52:41+00:00  baseline  16 out of 20, in-pattern 4 out of 8, 20 model calls
2026-11-15T04:54:22+00:00  feedback  12 out of 20, in-pattern 3 out of 8, 20 model calls
2026-11-15T04:58:04+00:00  reflector reviewed 0, queued [], approved 0
2026-11-15T05:03:16+00:00  feedback  17 out of 20, in-pattern 5 out of 8, 18 model calls
2026-11-15T05:06:48+00:00  reflector reviewed 1, queued ['value_change', 'value_change', 'learned'], approved 2
2026-11-15T05:08:00+00:00  refined   19 out of 20, in-pattern 8 out of 8, 20 model calls

Every Reflector run
Reflector run 1 (2026-11-15T04:58:04+00:00): reviewed 0 session(s), approved 0 value change(s).
  The review did not complete, so it queued nothing.
Reflector run 2 (2026-11-15T05:06:48+00:00): reviewed 1 session(s), approved 2 value change(s).
  Queued for V1: ## V1: Fitness without body comparison (strength: high)
  Queued for new: ## New: Hustle culture and get-rich-quick content (strength: medium)

Caveats
- With 20 held-out reels, a difference of one or two between stages is within noise.
- This is not a blind test of the session-review skill. The Reflector is run on the feedback session (s_001), and the skill was changed after seeing the Reflector's output on that same session: (1) after the first live run, step 5 was reworded to group the disagree and not_for_me marks, count each group, and propose for every group with three or more marks; (2) format examples that happened to use seed IDs were replaced with made-up IDs; (3) in step 4b the rules for refinements (change only the lines the evidence supports) and for "none" lines were added, again after live reviews of that session. The held-out reels and their labels were never shown to the Reflector or used to adjust anything.
- The Judge here is a text-only stand-in that runs the appendix Judge prompt on each reel's description, on-screen text and transcript. It is not the team's Judge.
- Temperature 0 is requested, but the library reports that gemini-3.5-flash-lite uses fixed sampling and ignores it, so a repeated run of a stage can differ. Every run that called the model is listed above, not only the last.
- The held-out labels are the simulated persona's, generated with the seed data.
```

## Full output, script run 1

```text
LEARNING EVALUATION

Stand-in Judge: gemini-3.5-flash-lite, text only, on the 20 held-out reels.
A match is a conflict / not-conflict call that agrees with the persona's label.

Stage 1  Baseline                    16 out of 20    in-pattern: 4 out of 8
Stage 2  Feedback learning           12 out of 20    in-pattern: 3 out of 8   (7 unreadable)
Stage 3  Approved value refinement   no proposal: the Reflector queued no value change, so there is nothing to approve

Per reel
reel     in-pattern  persona        stage 1    stage 2    stage 3
rl_021   yes         not conflict   aligned    error      -         
rl_022   yes         not conflict   aligned    aligned    -         
rl_023   yes         not conflict   conflict   aligned    -         
rl_024   yes         not conflict   aligned    neutral    -         
rl_025   yes         conflict       conflict   error      -         
rl_026   yes         conflict       neutral    error      -         
rl_027   yes         conflict       neutral    error      -         
rl_028   yes         conflict       neutral    neutral    -         
rl_029   no          conflict       conflict   conflict   -         
rl_030   no          conflict       conflict   conflict   -         
rl_031   no          not conflict   aligned    aligned    -         
rl_032   no          not conflict   aligned    error      -         
rl_033   no          conflict       conflict   error      -         
rl_034   no          not conflict   aligned    aligned    -         
rl_035   no          not conflict   aligned    aligned    -         
rl_036   no          conflict       conflict   error      -         
rl_037   no          not conflict   aligned    aligned    -         
rl_038   no          not conflict   neutral    neutral    -         
rl_039   no          conflict       conflict   conflict   -         
rl_040   no          not conflict   neutral    neutral    -         

What the Reflector queued (one run on the feedback session)
reflect_pending reviewed 0 session(s) and left 0 card(s) in the inbox.

Value-change cards approved unchanged through answer_question: 0

Values given to the Judge in stages 1 and 2
## V1: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: none
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: said at onboarding; approved 2026-11-15

## V2: News without outrage (strength: medium)
- Want more: calm explainers, local news, science
- Want less: rage-bait politics, people shouting at each other
- Unless: none
- In my words: "I want to know what happened, not be told who to be angry at"
- Evidence: said at onboarding; approved 2026-11-15

## V3: Cooking I can actually make (strength: low)
- Want more: simple recipes, budget meals, meal prep
- Want less: extreme food challenges, mukbang
- Unless: none
- In my words: "show me dinner I can cook on a Tuesday"
- Evidence: said at onboarding; approved 2026-11-15

Values given to the Judge in stage 3
(not run)

Every run that called a model, oldest first
2026-11-15T04:52:41+00:00  baseline  16 out of 20, in-pattern 4 out of 8, 20 model calls
2026-11-15T04:54:22+00:00  feedback  12 out of 20, in-pattern 3 out of 8, 20 model calls
2026-11-15T04:58:04+00:00  reflector reviewed 0, queued [], approved 0

Caveats
- With 20 held-out reels, a difference of one or two between stages is within noise.
- This is not a blind test of the session-review skill. The Reflector is run on the feedback session (s_001), and the skill was changed after seeing the Reflector's output on that same session: (1) after the first live run, step 5 was reworded to group the disagree and not_for_me marks, count each group, and propose for every group with three or more marks; (2) format examples that happened to use seed IDs were replaced with made-up IDs; (3) in step 4b the rules for refinements (change only the lines the evidence supports) and for "none" lines were added, again after live reviews of that session. The held-out reels and their labels were never shown to the Reflector or used to adjust anything.
- The Judge here is a text-only stand-in that runs the appendix Judge prompt on each reel's description, on-screen text and transcript. It is not the team's Judge.
- Temperature 0 is requested, but the library reports that gemini-3.5-flash-lite uses fixed sampling and ignores it, so a repeated run of a stage can differ. Every run that called the model is listed above, not only the last.
- The held-out labels are the simulated persona's, generated with the seed data.
```
