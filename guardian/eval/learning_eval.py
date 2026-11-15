"""The three-stage learning evaluation (docs/GUARDIAN_SPEC_V5.md, "Learning evaluation").

    python -m guardian.eval.learning_eval            # run, reusing cached results
    python -m guardian.eval.learning_eval --fresh    # forget the cache and run again

It separates learning from feedback examples from learning from an approved change to
the values. A text-only stand-in Judge runs the appendix Judge prompt
(guardian/judge_prompt.md) on the 20 held-out seed reels, three times:

  1. Baseline: the initial approved values, no feedback examples.
  2. Feedback learning: the same values, plus up to three similar reels from the
     feedback set for each judged reel.
  3. Approved value refinement: the values after the Reflector's proposals from the
     feedback session are approved unchanged, plus the same examples.

Between stages 2 and 3 `reflect_pending` runs once on the feedback session and every
value-change card is approved unchanged through `answer_question`. If the Reflector
queues no value change, stage 3 is reported as "no proposal"; nothing is written by hand.

Judge outputs are cached per stage in guardian/eval/.cache/, and every run that
called a model is appended to a run log there, so the report can list every run and
not only the last one. The stand-in Judge is test code, not the team's Judge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from guardian import api, config, models
from guardian.datasource import SEED_DIR, FakeDataSource, FeedbackExample
from guardian.middleware import label_untrusted
from guardian.seed.install import install_initial_values
from guardian.types import Answer

USER = "eval-user"
FEEDBACK_SESSION = "s_001"
HELDOUT_SESSION = "s_heldout"
MAX_EXAMPLES = 3
JUDGE_MODEL_VAR = "GUARDIAN_EVAL_JUDGE_MODEL"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
JUDGE_PROMPT_FILE = Path(__file__).resolve().parents[1] / "judge_prompt.md"

BASELINE = "baseline"
FEEDBACK = "feedback"
REFINED = "refined"
REFLECTOR = "reflector"
STAGE_TITLES = {
    BASELINE: "Stage 1  Baseline",
    FEEDBACK: "Stage 2  Feedback learning",
    REFINED: "Stage 3  Approved value refinement",
}

VERDICTS = ("aligned", "neutral", "conflict")
VERDICT_ERROR = "error"
NO_EXAMPLES = "(none)"
NO_PROPOSAL = (
    "no proposal: the Reflector queued no value change, so there is nothing to approve"
)

# What each feedback label means, in words the Judge cannot mistake for a verdict.
REACTIONS = {
    "agree": "they agreed with that verdict",
    "disagree": "they disagreed with that verdict",
    "not_for_me": "they marked the reel as not for them, so they want less of this",
    "fine": "they said the reel was fine for them",
}

REEL_SECTION = """THE REEL
This is a text-only test: you have no frames or audio. The text below is what the
frames showed and what was said. Judge it as you would the reel.
{reel}

Return only the JSON object."""

CAVEATS = (
    "With 20 held-out reels, a difference of one or two between stages is within noise.",
    "This is not a blind test of the session-review skill. The Reflector is run on the "
    "feedback session (s_001), and the skill was changed after seeing the Reflector's "
    "output on that same session: (1) after the first live run, step 5 was reworded to "
    "group the disagree and not_for_me marks, count each group, and propose for every "
    "group with three or more marks; (2) format examples that happened to use seed IDs "
    "were replaced with made-up IDs; (3) in step 4b the rules for refinements (change "
    'only the lines the evidence supports) and for "none" lines were added, again '
    "after live reviews of that session. The held-out reels and their labels were "
    "never shown to the Reflector or used to adjust anything.",
    "The Judge here is a text-only stand-in that runs the appendix Judge prompt on each "
    "reel's description, on-screen text and transcript. It is not the team's Judge.",
    "Temperature 0 is requested, but the library reports that gemini-3.5-flash-lite "
    "uses fixed sampling and ignores it, so a repeated run of a stage can differ. "
    "Every run that called the model is listed above, not only the last.",
    "The held-out labels are the simulated persona's, generated with the seed data.",
)


# --- Data ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Environment:
    """The seed data for the evaluation and a configured Guardian over it."""

    datasource: FakeDataSource
    heldout: tuple[dict[str, Any], ...]
    labels: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Score:
    matches: int
    total: int
    in_pattern_matches: int
    in_pattern_total: int
    errors: int


@dataclass(frozen=True)
class StageResult:
    stage: str
    verdicts: dict[str, str]
    score: Score
    calls: int  # model calls made in this run; 0 when it was all cached


@dataclass(frozen=True)
class ReflectorOutcome:
    reviewed: int
    queued: tuple[dict[str, Any], ...]
    approved: int
    refused: tuple[str, ...]
    values_before: str
    values_after: str


@dataclass(frozen=True)
class Report:
    baseline: StageResult
    feedback: StageResult
    outcome: ReflectorOutcome
    refined: StageResult | None
    labels: dict[str, dict[str, Any]]
    judge_model: str


def build_environment(
    clock: Callable[[], datetime] | None = None, reflector_model: Any | None = None
) -> Environment:
    """Configure the Guardian in memory on the feedback session and the held-out reels,
    with the persona's initial values approved. Only the feedback session is listed,
    so `reflect_pending` reviews exactly that one."""
    now = clock or (lambda: datetime.now(UTC))
    reels = json.loads((SEED_DIR / "reels.json").read_text())
    wanted = [
        r for r in reels if r["session_id"] in (FEEDBACK_SESSION, HELDOUT_SESSION)
    ]
    datasource = FakeDataSource.from_records(
        USER,
        reels=wanted,
        feedback=json.loads((SEED_DIR / "feedback.json").read_text()),
        clock=now,
    )
    store = InMemoryStore()
    api.configure(
        store, InMemorySaver(), datasource, clock=now, reflector_model=reflector_model
    )
    install_initial_values(store, datasource, USER, now())
    return Environment(
        datasource=datasource,
        heldout=tuple(r for r in wanted if r["session_id"] == HELDOUT_SESSION),
        labels=json.loads((SEED_DIR / "heldout_labels.json").read_text()),
    )


# --- The cache and the run log --------------------------------------------------------------


class Cache:
    """Judge outputs by stage, the Reflector step's outcome, and a log of every run."""

    def __init__(self, directory: Path = CACHE_DIR) -> None:
        self._directory = directory

    def _path(self, name: str) -> Path:
        self._directory.mkdir(parents=True, exist_ok=True)
        return self._directory / name

    def _read(self, name: str) -> dict[str, Any]:
        path = self._path(name)
        return json.loads(path.read_text()) if path.exists() else {}

    def get(self, stage: str, key: str) -> str | None:
        entry = self._read(f"{stage}.json").get(key)
        return str(entry["verdict"]) if entry else None

    def put(self, stage: str, key: str, verdict: str, raw: str) -> None:
        stored = {**self._read(f"{stage}.json"), key: {"verdict": verdict, "raw": raw}}
        self._path(f"{stage}.json").write_text(json.dumps(stored, indent=1))

    def log_run(self, record: dict[str, Any]) -> None:
        with self._path("runs.jsonl").open("a") as log:
            log.write(json.dumps(record) + "\n")

    def runs(self) -> list[dict[str, Any]]:
        path = self._path("runs.jsonl")
        lines = path.read_text().splitlines() if path.exists() else []
        return [json.loads(line) for line in lines if line.strip()]

    def load_outcome(self) -> ReflectorOutcome | None:
        saved = self._read(f"{REFLECTOR}.json")
        if not saved:
            return None
        return ReflectorOutcome(
            **{
                **saved,
                "queued": tuple(saved["queued"]),
                "refused": tuple(saved["refused"]),
            }
        )

    def save_outcome(self, outcome: ReflectorOutcome) -> None:
        self._path(f"{REFLECTOR}.json").write_text(
            json.dumps(asdict(outcome), indent=1)
        )

    def clear(self) -> None:
        if self._directory.exists():
            for path in self._directory.iterdir():
                path.unlink()


# --- The stand-in Judge ---------------------------------------------------------------------


def judge_prompt_template() -> str:
    """The Judge prompt from guardian/judge_prompt.md, so the two cannot drift apart."""
    match = re.search(r"```text\n(.*?)```", JUDGE_PROMPT_FILE.read_text(), re.DOTALL)
    if match is None:
        raise config.ConfigError(f"no ```text block found in {JUDGE_PROMPT_FILE}")
    return match.group(1).strip()


def examples_block(examples: Sequence[FeedbackExample]) -> str:
    """Past reactions, each with the verdict it was a reaction to. A bare label such
    as "disagree" says nothing without that verdict, and the Judge then copies the
    label into its own verdict (seen in the first live run)."""
    if not examples:
        return NO_EXAMPLES
    lines = []
    for example in examples:
        reaction = REACTIONS.get(example["label"], example["label"])
        comment = f' They said: "{example["comment"]}"' if example["comment"] else ""
        lines.append(
            f'- {example["reel"]["one_line"]} It was judged '
            f'{example["reel"]["verdict"]}. The person\'s reaction: {reaction}.{comment}'
        )
    return "\n".join(lines)


def reel_block(reel: dict[str, Any]) -> str:
    """What the stand-in Judge is told about a reel: its description, on-screen text
    and transcript. Never the seed's own verdict, reason or features."""
    text = (
        f"Description: {reel['description']}\n"
        f"On-screen text: {reel.get('on_screen_text') or '(none)'}\n"
        f"Transcript: {reel.get('transcript') or '(no speech)'}"
    )
    return label_untrusted(text)


def build_prompt(
    values_md: str, examples: Sequence[FeedbackExample], reel: dict[str, Any]
) -> str:
    filled = (
        judge_prompt_template()
        .replace("{values_md}", values_md.strip())
        .replace("{feedback_examples}", examples_block(examples))
    )
    return f"{filled}\n\n{REEL_SECTION.replace('{reel}', reel_block(reel))}"


def parse_verdict(text: str) -> str:
    """The verdict in the Judge's JSON, or "error" if there is none to read."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return VERDICT_ERROR
    verdict = str(data.get("verdict", "")).strip().lower()
    return verdict if verdict in VERDICTS else VERDICT_ERROR


def reply_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") for block in content if isinstance(block, dict)
    )


def model_name(model: Any) -> str:
    return str(getattr(model, "model", type(model).__name__))


def score(verdicts: dict[str, str], labels: dict[str, dict[str, Any]]) -> Score:
    """How many conflict / not-conflict calls match the persona's labels."""
    matched = [
        reel_id
        for reel_id, label in labels.items()
        if verdicts.get(reel_id) in VERDICTS
        and (verdicts[reel_id] == "conflict") == bool(label["conflict"])
    ]
    in_pattern = [reel_id for reel_id, label in labels.items() if label["in_pattern"]]
    return Score(
        matches=len(matched),
        total=len(labels),
        in_pattern_matches=len([r for r in matched if r in in_pattern]),
        in_pattern_total=len(in_pattern),
        errors=len([v for v in verdicts.values() if v == VERDICT_ERROR]),
    )


def run_stage(
    stage: str, judge: Any, environment: Environment, values_md: str, cache: Cache
) -> StageResult:
    """Judge the 20 held-out reels once. A reel already judged with the same prompt
    by the same model is read from the cache."""
    verdicts: dict[str, str] = {}
    calls = 0
    for reel in environment.heldout:
        examples = (
            []
            if stage == BASELINE
            else environment.datasource.find_similar_feedback(
                USER,
                f"{reel['description']} {reel.get('transcript', '')}",
                MAX_EXAMPLES,
            )
        )
        prompt = build_prompt(values_md, examples, reel)
        key = hashlib.sha256(f"{model_name(judge)}\n{prompt}".encode()).hexdigest()
        cached = cache.get(stage, key)
        if cached is not None:
            verdicts[reel["id"]] = cached
            continue
        calls += 1
        try:
            raw = reply_text(judge.invoke(prompt))
        except (
            Exception
        ) as error:  # noqa: BLE001 - a failed call is reported, not cached
            detail = config.redact_secrets(f"{type(error).__name__}: {error}")
            sys.stderr.write(f"judge call failed for {reel['id']}: {detail}\n")
            verdicts[reel["id"]] = VERDICT_ERROR
            continue
        verdicts[reel["id"]] = parse_verdict(raw)
        cache.put(stage, key, verdicts[reel["id"]], raw)
    result = StageResult(stage, verdicts, score(verdicts, environment.labels), calls)
    if calls:
        cache.log_run(
            {
                "stage": stage,
                "when": datetime.now(UTC).isoformat(timespec="seconds"),
                "calls": calls,
                "score": asdict(result.score),
                "verdicts": verdicts,
            }
        )
    return result


# --- The Reflector step ---------------------------------------------------------------------


def reflector_step(
    environment: Environment, cache: Cache, retry: bool = False
) -> ReflectorOutcome:
    """Run `reflect_pending` once on the feedback session and approve every
    value-change card unchanged. Run once; later calls read the saved outcome.

    `retry` runs it again on purpose. The earlier run stays in the run log, and the
    report lists every run with what it queued.
    """
    saved = cache.load_outcome()
    if saved is not None and not retry:
        return saved
    before = api.get_values(USER)
    reviewed = api.reflect_pending(USER)
    queued, refused, approved = [], [], 0
    for question in api.get_questions(USER):
        change = question["value_change"]
        queued.append(
            {
                "kind": question["kind"],
                "prompt": question["prompt"],
                "value_id": change["value_id"] if change else None,
                "new_text": change["new_text"] if change else None,
                "evidence": change["evidence"] if change else None,
            }
        )
        if change is None:
            continue
        result = api.answer_question(USER, question["id"], Answer(action="approve"))
        if result["ok"]:
            approved += 1
        else:
            refused.append(str(result["error"]))
    outcome = ReflectorOutcome(
        reviewed=reviewed,
        queued=tuple(queued),
        approved=approved,
        refused=tuple(refused),
        values_before=before,
        values_after=api.get_values(USER),
    )
    cache.save_outcome(outcome)
    cache.log_run(
        {
            "stage": REFLECTOR,
            "when": datetime.now(UTC).isoformat(timespec="seconds"),
            "reviewed": reviewed,
            "queued": [card["kind"] for card in queued],
            "approved": approved,
            "cards": queued,
        }
    )
    return outcome


# --- The evaluation and its report ----------------------------------------------------------


def evaluate(
    environment: Environment, judge: Any, cache: Cache, retry_reflector: bool = False
) -> Report:
    initial = api.get_values(USER)
    baseline = run_stage(BASELINE, judge, environment, initial, cache)
    feedback = run_stage(FEEDBACK, judge, environment, initial, cache)
    outcome = reflector_step(environment, cache, retry=retry_reflector)
    refined = (
        run_stage(REFINED, judge, environment, outcome.values_after, cache)
        if outcome.approved
        else None
    )
    return Report(
        baseline, feedback, outcome, refined, environment.labels, model_name(judge)
    )


def _score_line(title: str, result: Score) -> str:
    errors = f"   ({result.errors} unreadable)" if result.errors else ""
    return (
        f"{title:<36} {result.matches:>2} out of {result.total}    "
        f"in-pattern: {result.in_pattern_matches} out of {result.in_pattern_total}{errors}"
    )


def _reel_lines(report: Report) -> list[str]:
    stages = [report.baseline, report.feedback, report.refined]
    lines = ["reel     in-pattern  persona        stage 1    stage 2    stage 3"]
    for reel_id, label in report.labels.items():
        wanted = "conflict" if label["conflict"] else "not conflict"
        cells = [
            f"{stage.verdicts.get(reel_id, '-'):<10}" if stage else f"{'-':<10}"
            for stage in stages
        ]
        mark = "yes" if label["in_pattern"] else "no"
        lines.append(f"{reel_id:<8} {mark:<11} {wanted:<14} {' '.join(cells)}")
    return lines


def _reflector_lines(outcome: ReflectorOutcome) -> list[str]:
    lines = [
        f"reflect_pending reviewed {outcome.reviewed} session(s) and left "
        f"{len(outcome.queued)} card(s) in the inbox."
    ]
    for number, card in enumerate(outcome.queued, start=1):
        lines.append(f"\nCard {number}: {card['kind']}")
        lines.append(f"Prompt: {card['prompt']}")
        if card["new_text"]:
            lines.append(f"Target: {card['value_id']}    Evidence: {card['evidence']}")
            lines.append(str(card["new_text"]))
    lines.append(
        f"\nValue-change cards approved unchanged through answer_question: "
        f"{outcome.approved}"
    )
    lines.extend(f"Refused on approval: {error}" for error in outcome.refused)
    return lines


def _every_reflector_run(cache: Cache) -> list[str]:
    """Each Reflector run in the log, with the value changes it queued."""
    lines = []
    runs = [run for run in cache.runs() if run["stage"] == REFLECTOR]
    for number, run in enumerate(runs, start=1):
        lines.append(
            f"Reflector run {number} ({run['when']}): reviewed {run['reviewed']} "
            f"session(s), approved {run['approved']} value change(s)."
        )
        changes = [card for card in run.get("cards", []) if card.get("new_text")]
        if not run["queued"]:
            lines.append(
                "  The review completed and queued nothing."
                if run["reviewed"]
                else "  The review did not complete, so it queued nothing."
            )
        for card in changes:
            first_line = str(card["new_text"]).splitlines()[0]
            lines.append(f"  Queued for {card['value_id']}: {first_line}")
    return lines or ["(no Reflector run is logged)"]


def _run_lines(cache: Cache) -> list[str]:
    lines = []
    for run in cache.runs():
        if run["stage"] == REFLECTOR:
            detail = (
                f"reviewed {run['reviewed']}, queued {run['queued']}, "
                f"approved {run['approved']}"
            )
        else:
            result = run["score"]
            detail = (
                f"{result['matches']} out of {result['total']}, in-pattern "
                f"{result['in_pattern_matches']} out of {result['in_pattern_total']}, "
                f"{run['calls']} model calls"
            )
        lines.append(f"{run['when']}  {run['stage']:<9} {detail}")
    return lines or ["(no model was called: everything came from the cache)"]


def render(report: Report, cache: Cache) -> str:
    refined = (
        _score_line(STAGE_TITLES[REFINED], report.refined.score)
        if report.refined
        else f"{STAGE_TITLES[REFINED]:<36} {NO_PROPOSAL}"
    )
    sections = [
        "LEARNING EVALUATION",
        f"Stand-in Judge: {report.judge_model}, text only, on the 20 held-out reels.\n"
        "A match is a conflict / not-conflict call that agrees with the persona's label.",
        "\n".join(
            [
                _score_line(STAGE_TITLES[BASELINE], report.baseline.score),
                _score_line(STAGE_TITLES[FEEDBACK], report.feedback.score),
                refined,
            ]
        ),
        "Per reel\n" + "\n".join(_reel_lines(report)),
        "What the Reflector queued (one run on the feedback session)\n"
        + "\n".join(_reflector_lines(report.outcome)),
        "Values given to the Judge in stages 1 and 2\n" + report.outcome.values_before,
        "Values given to the Judge in stage 3\n"
        + (report.outcome.values_after if report.refined else "(not run)"),
        "Every run that called a model, oldest first\n" + "\n".join(_run_lines(cache)),
        "Every Reflector run\n" + "\n".join(_every_reflector_run(cache)),
        "Caveats\n" + "\n".join(f"- {caveat}" for caveat in CAVEATS),
    ]
    return "\n\n".join(sections)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the learning evaluation.")
    parser.add_argument("--fresh", action="store_true", help="forget cached results")
    parser.add_argument(
        "--retry-reflector",
        action="store_true",
        help="run the Reflector step again; earlier runs stay in the report",
    )
    arguments = parser.parse_args(argv)
    cache = Cache()
    if arguments.fresh:
        cache.clear()
    judge_name = os.environ.get(JUDGE_MODEL_VAR, "").strip() or config.chat_model_name()
    judge = models.build_model(judge_name)
    report = evaluate(
        build_environment(), judge, cache, retry_reflector=arguments.retry_reflector
    )
    sys.stdout.write(render(report, cache) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
