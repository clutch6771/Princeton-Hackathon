"""Tests for the learning evaluation harness. Fake models stand in for the stand-in
Judge and the Reflector, so nothing here calls an API."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from guardian import api
from guardian.eval import learning_eval as ev
from guardian.tests.fakes import ScriptedChatModel, reply
from guardian.tests.test_reflect_pending import HUSTLE_CHANGE, good_run

NOW = datetime(2026, 11, 14, 22, 0, tzinfo=UTC)


class FakeJudge:
    """Judges from a rule on the prompt text and counts its calls."""

    def __init__(self, rule: Any = None) -> None:
        self.prompts: list[str] = []
        self._rule = rule or (lambda prompt: "neutral")

    def invoke(self, prompt: str) -> AIMessage:
        self.prompts.append(prompt)
        verdict = self._rule(prompt)
        return AIMessage(content=json.dumps({"is_feed": True, "verdict": verdict}))


def conflict_unless_refined(prompt: str) -> str:
    """Flags every reel until the values carry an "Unless" exception."""
    return "neutral" if "- Unless: injury rehab" in prompt else "conflict"


@pytest.fixture
def cache(tmp_path) -> ev.Cache:
    return ev.Cache(tmp_path / "cache")


@pytest.fixture
def environment():
    return ev.build_environment(clock=lambda: NOW)


# --- The stand-in Judge's prompt ------------------------------------------------------


def test_the_prompt_is_the_appendix_judge_prompt_with_values_and_the_reel(environment):
    reel = environment.heldout[0]

    prompt = ev.build_prompt("## V1: Something (strength: high)", [], reel)

    assert "You look at one short video" in prompt  # from guardian/judge_prompt.md
    assert 'A line that says "none" has nothing to match.' in prompt
    assert "## V1: Something (strength: high)" in prompt
    assert ev.NO_EXAMPLES in prompt
    assert reel["description"] in prompt and reel["transcript"] in prompt
    assert "{values_md}" not in prompt and "{feedback_examples}" not in prompt


def test_the_stand_in_judge_never_sees_the_seed_verdict_reason_or_features(environment):
    reel = environment.heldout[0]

    prompt = ev.build_prompt("values", [], reel)

    assert reel["reason"] not in prompt
    assert str(reel["features"]["topics"][0]) not in prompt.replace(
        reel["description"], ""
    ).replace(reel["transcript"], "")
    assert "confidence: 0." not in prompt


def test_reel_text_is_labelled_as_untrusted_data(environment):
    prompt = ev.build_prompt("values", [], environment.heldout[0])

    assert "<untrusted_data>" in prompt and "</untrusted_data>" in prompt


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ('{"is_feed": true, "verdict": "conflict", "reason": "x"}', "conflict"),
        ('```json\n{"verdict": "aligned"}\n```', "aligned"),
        ('Here you go: {"verdict": "Neutral"} thanks', "neutral"),
        ('{"is_feed": false}', ev.VERDICT_ERROR),
        ('{"verdict": "maybe"}', ev.VERDICT_ERROR),
        ("no json at all", ev.VERDICT_ERROR),
    ],
)
def test_the_verdict_is_read_from_the_judges_json(text, verdict):
    assert ev.parse_verdict(text) == verdict


def test_a_feedback_example_says_what_was_judged_and_what_the_reaction_means(
    environment,
):
    # Run 1 of the live evaluation: given only "The person marked it: disagree", the
    # Judge answered with the feedback labels as verdicts on 7 of 20 reels.
    examples = environment.datasource.find_similar_feedback(
        ev.USER, "injury rehab recovery progress knee surgery", 3
    )

    block = ev.examples_block(examples)

    assert examples and block.count("\n- ") == len(examples) - 1
    assert "It was judged conflict." in block
    assert "they disagreed with that verdict" in block
    assert "marked it: disagree" not in block


def test_each_feedback_label_is_explained_in_words():
    assert set(ev.REACTIONS) == {"agree", "disagree", "not_for_me", "fine"}


# --- Scoring ---------------------------------------------------------------------------


def test_scoring_counts_conflict_calls_that_match_the_personas_labels(environment):
    labels = environment.labels
    perfect = {
        reel_id: "conflict" if label["conflict"] else "neutral"
        for reel_id, label in labels.items()
    }
    all_conflict = dict.fromkeys(labels, "conflict")
    errors = dict.fromkeys(labels, ev.VERDICT_ERROR)

    assert ev.score(perfect, labels) == ev.Score(20, 20, 8, 8, 0)
    wrong = ev.score(all_conflict, labels)
    assert wrong.matches == sum(1 for label in labels.values() if label["conflict"])
    assert ev.score(errors, labels) == ev.Score(0, 20, 0, 8, 20)


def test_the_environment_has_one_feedback_session_and_twenty_hidden_heldout_reels(
    environment,
):
    sessions = environment.datasource.list_sessions(ev.USER)

    assert [session["id"] for session in sessions] == [ev.FEEDBACK_SESSION]
    assert len(environment.heldout) == 20 and len(environment.labels) == 20
    assert api.get_values(ev.USER).count("## V") == 3


# --- Stages and the cache --------------------------------------------------------------


def test_the_baseline_stage_gives_no_feedback_examples(environment, cache):
    judge = FakeJudge()

    result = ev.run_stage(ev.BASELINE, judge, environment, "values", cache)

    assert len(judge.prompts) == 20 and result.calls == 20
    assert all(ev.NO_EXAMPLES in prompt for prompt in judge.prompts)


def test_the_feedback_stage_gives_up_to_three_examples_from_the_feedback_set(
    environment, cache
):
    judge = FakeJudge()

    ev.run_stage(ev.FEEDBACK, judge, environment, "values", cache)

    with_examples = [p for p in judge.prompts if ev.NO_EXAMPLES not in p]
    assert with_examples
    for prompt in judge.prompts:
        examples = prompt.split("REELS THEY ALREADY REACTED TO")[1].split("DO THIS")[0]
        assert examples.count("\n- ") <= 3
        assert "rl_02" not in examples and "rl_03" not in examples  # no held-out reel


def test_a_stage_is_served_from_the_cache_the_second_time(environment, cache):
    first, again = FakeJudge(), FakeJudge()

    one = ev.run_stage(ev.BASELINE, first, environment, "values", cache)
    two = ev.run_stage(ev.BASELINE, again, environment, "values", cache)

    assert again.prompts == [] and two.calls == 0
    assert one.verdicts == two.verdicts


def test_changed_values_are_judged_again(environment, cache):
    ev.run_stage(ev.REFINED, FakeJudge(), environment, "old values", cache)
    judge = FakeJudge()

    result = ev.run_stage(ev.REFINED, judge, environment, "new values", cache)

    assert result.calls == 20


def test_every_run_that_called_the_model_is_logged_and_cached_ones_are_not(
    environment, cache
):
    ev.run_stage(ev.BASELINE, FakeJudge(), environment, "values", cache)
    ev.run_stage(ev.BASELINE, FakeJudge(), environment, "values", cache)  # cached
    ev.run_stage(ev.BASELINE, FakeJudge(), environment, "other values", cache)

    runs = [run for run in cache.runs() if run["stage"] == ev.BASELINE]
    assert [run["calls"] for run in runs] == [20, 20]


# --- The Reflector step ------------------------------------------------------------------


def test_the_reflector_step_approves_every_value_card_unchanged(cache):
    reflector = ScriptedChatModel(script=good_run())
    environment = ev.build_environment(clock=lambda: NOW, reflector_model=reflector)
    before = api.get_values(ev.USER)

    outcome = ev.reflector_step(environment, cache)

    assert outcome.reviewed == 1
    assert sorted(card["kind"] for card in outcome.queued) == [
        "learned",
        "value_change",
    ]
    (change,) = [card for card in outcome.queued if card["kind"] == "value_change"]
    assert change["new_text"] == HUSTLE_CHANGE["new_text"]
    assert outcome.approved == 1 and outcome.values_after != before
    assert "- Agreed wording: \"I don't want hustle" in outcome.values_after


def test_the_reflector_step_runs_once_and_is_then_read_from_the_cache(cache):
    reflector = ScriptedChatModel(script=good_run())
    environment = ev.build_environment(clock=lambda: NOW, reflector_model=reflector)
    first = ev.reflector_step(environment, cache)
    calls = len(reflector.calls)

    again = ev.reflector_step(ev.build_environment(clock=lambda: NOW), cache)

    assert again == first and len(reflector.calls) == calls
    assert len([run for run in cache.runs() if run["stage"] == ev.REFLECTOR]) == 1


def test_a_second_reflector_run_must_be_asked_for_and_both_runs_are_reported(cache):
    failed = ScriptedChatModel(script=[reply("I give up.")])  # writes no summary
    first = ev.reflector_step(
        ev.build_environment(clock=lambda: NOW, reflector_model=failed), cache
    )
    working = ScriptedChatModel(script=good_run())
    environment = ev.build_environment(clock=lambda: NOW, reflector_model=working)

    judge = FakeJudge()
    report = ev.evaluate(environment, judge, cache, retry_reflector=True)
    second = report.outcome

    assert (first.reviewed, first.approved) == (0, 0)
    assert (second.reviewed, second.approved) == (1, 1)
    # Stages 1 and 2 are judged before the Reflector runs, with the initial values.
    assert all("get-rich-quick" not in prompt for prompt in judge.prompts[:40])
    assert all("Less get-rich-quick content" in prompt for prompt in judge.prompts[40:])
    runs = [run for run in cache.runs() if run["stage"] == ev.REFLECTOR]
    assert [run["reviewed"] for run in runs] == [0, 1]
    text = ev.render(report, cache)
    assert "Reflector run 1" in text and "Reflector run 2" in text
    assert "queued nothing" in text and "Less get-rich-quick content" in text


def test_a_review_that_did_not_complete_is_not_reported_as_no_proposal(cache):
    failed = ScriptedChatModel(script=[reply("I give up.")])  # writes no summary
    environment = ev.build_environment(clock=lambda: NOW, reflector_model=failed)

    text = ev.render(ev.evaluate(environment, FakeJudge(), cache), cache)

    assert "did not complete" in text
    assert "queued no value change" not in text


def test_with_no_value_card_the_refined_stage_is_no_proposal(cache):
    reflector = ScriptedChatModel(script=good_run(change=None))
    environment = ev.build_environment(clock=lambda: NOW, reflector_model=reflector)
    judge = FakeJudge()

    report = ev.evaluate(environment, judge, cache)

    assert report.outcome.approved == 0
    assert report.refined is None
    assert len(judge.prompts) == 40  # two stages only
    assert "no proposal" in ev.render(report, cache)


# --- End to end ----------------------------------------------------------------------------


def test_the_report_gives_three_stages_the_reflector_step_and_the_caveats(cache):
    refinement = {
        "value_id": "V1",
        "new_text": """## V1: Fitness without body comparison (strength: high)
- Want more: form tutorials, training plans, recovery
- Want less: before/after transformations, physique rating
- Unless: injury rehab and recovery progress
- In my words: "gym stuff is great, comparing bodies is not"
- Evidence: said at onboarding; fb_001, fb_003, fb_005""",
        "evidence": "fb_001, fb_003, fb_005",
    }
    reflector = ScriptedChatModel(script=good_run(change=refinement))
    environment = ev.build_environment(clock=lambda: NOW, reflector_model=reflector)

    report = ev.evaluate(environment, FakeJudge(conflict_unless_refined), cache)
    text = ev.render(report, cache)

    conflicts = sum(1 for label in environment.labels.values() if label["conflict"])
    assert report.baseline.score.matches == conflicts
    assert report.feedback.score.matches == conflicts
    assert report.refined is not None
    assert report.refined.score.matches == 20 - conflicts
    for needle in (
        "Stage 1",
        "Stage 2",
        "Stage 3",
        "out of 20",
        "in-pattern",
        "What the Reflector queued",
        "- Unless: injury rehab and recovery progress",
        "within noise",
        "not a blind test",
        "Every run",
    ):
        assert needle in text, needle


def test_a_judge_reply_that_cannot_be_read_is_counted_as_an_error(environment, cache):
    class Broken:
        def invoke(self, prompt: str) -> AIMessage:
            return reply("I cannot decide.")

    result = ev.run_stage(ev.BASELINE, Broken(), environment, "values", cache)

    assert result.score.errors == 20 and result.score.matches == 0
