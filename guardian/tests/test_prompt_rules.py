"""The rules the prompts and the skill must carry (spec V5 and the reflection addendum).

These pin the wording of each rule lightly, so that a later edit cannot drop one
without a test failing. How well a model follows them is checked in the live runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from guardian import prompts
from guardian.memory import formats

SKILL = (Path(prompts.__file__).parent / "skills/session-review/SKILL.md").read_text()
JUDGE_PROMPT = (Path(prompts.__file__).parent / "judge_prompt.md").read_text()
GUARDIAN = " ".join(prompts.GUARDIAN_PROMPT.split())
SKILL_TEXT = " ".join(SKILL.split())


@pytest.mark.parametrize(
    "rule",
    [
        # The Guardian speaks first.
        "0. Open the conversation.",
        'Never open with a generic "How can I help?"',
        "name one of their values and ask whether it still fits",
        "Never write a date",
        # The interview: about "want less", until four things are known.
        "what they want LESS of",
        "a concrete recent example",
        "whether there is a version of it they would still want (the exception)",
        "why it matters to them",
        "usually three to five questions; never more than six",
        "propose straight away",
        "Do not ask what they want more of",
        "save_note as you go",
        "anything else they want less of",
        "up to three values",
        # Honest entries.
        'Write "none" on any line',
        '"Want more" is "none" unless',
        '"Unless" is "none" unless',
        "change only the lines the evidence supports",
        "Leave the title, the strength and every other line exactly as they are",
        # Reflection.
        "4. Reflect.",
        "Respond to what the user just said before asking anything new",
        "Ask, do not advise",
        "At most one reflection question per reply",
        "Plain text only, in every message",
        "first call save_note with their exact words, then reply",
        "ask first whether they would like it proposed",
        "If they say a value still fits, propose nothing",
    ],
)
def test_the_guardian_prompt_carries_the_rule(rule):
    assert rule in GUARDIAN


def test_the_old_three_question_limit_is_gone():
    assert "three questions before proposing" not in GUARDIAN
    assert "(or more)" not in GUARDIAN


@pytest.mark.parametrize(
    "rule",
    [
        'write "none" on any line the evidence does not support',
        "change only the lines the evidence supports",
        "Leave the title, the strength and every other line exactly as they are",
        "End with one open question about the pattern you saw",
    ],
)
def test_the_session_review_skill_carries_the_rule(rule):
    assert rule in SKILL_TEXT


def test_the_format_check_accepts_none_on_every_optional_line():
    entry = formats.parse_entry("""## New: Less body comparison (strength: high)
- Want more: none
- Want less: before/after transformations
- Unless: none
- Suggested wording: "I want less body comparison"
- Evidence: fb_001, fb_003""").entry

    assert entry is not None
    assert (entry.want_more, entry.unless) == ("none", "none")


def test_the_judge_prompt_says_a_none_line_matches_nothing():
    assert 'A line that says "none" has nothing to match.' in JUDGE_PROMPT
