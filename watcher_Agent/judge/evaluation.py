"""Read-only comparison with independently labeled sample decisions.

This is an evaluation aid, not a second model call or a truth detector.
Labels are supplied by the evaluator and never included in model requests.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from judge.contracts.record import EvaluationStatus, ReelRecord
from judge.contracts.understanding import Verdict
from judge.contracts.values import ApprovedValuesSnapshot


@dataclass(frozen=True)
class DecisionReference:
    """An expected decision for one sample and a particular values snapshot."""

    sample_id: str
    verdict: Verdict
    value_ids: frozenset[str]
    values_sha256: str


@dataclass(frozen=True)
class DecisionAssessment:
    """Confirmed/denied against a reference, or inconclusive without evidence."""

    sample_id: str
    expected_verdict: str
    actual_verdict: str | None
    confirmed: bool | None
    explanation: str


def assess_decision(
    record: ReelRecord,
    reference: DecisionReference,
    values: ApprovedValuesSnapshot,
) -> DecisionAssessment:
    """Compare verdict and cited values; never change stored decisions or values."""
    actual = record.verdict.value if record.verdict is not None else None
    confirmed: bool | None = None
    if record.owner_id != values.owner_id:
        explanation = "Cannot compare records and values owned by different users."
    elif (
        record.values_sha256 != values.content_sha256
        or reference.values_sha256 != values.content_sha256
    ):
        explanation = "Cannot compare a decision made against different values."
    elif record.evaluation_status is not EvaluationStatus.JUDGED:
        explanation = "No completed judgment; absence of a verdict is inconclusive."
    elif not reference.value_ids <= values.active_value_ids:
        explanation = "Reference cites values that are not active in this snapshot."
    else:
        confirmed = (
            record.verdict is reference.verdict
            and frozenset(record.value_ids) == reference.value_ids
        )
        explanation = (
            "Verdict and cited values match the independently supplied reference."
            if confirmed
            else "Verdict or cited values disagree with the reference."
        )
    return DecisionAssessment(
        sample_id=reference.sample_id,
        expected_verdict=reference.verdict.value,
        actual_verdict=actual,
        confirmed=confirmed,
        explanation=explanation,
    )


def classification_metrics(executions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Three-class metrics with failures reported instead of silently excluded.

    Rows are reference labels; columns are predicted labels. Accuracy and
    recall include missing verdicts as misses. Conditional accuracy considers
    only completed judgments. Undefined per-class ratios use zero, following
    the usual zero_division=0 classification-report convention.
    """
    labels = [v.value for v in (Verdict.ALIGNED, Verdict.NEUTRAL, Verdict.CONFLICT)]
    matrix = [[0 for _ in labels] for _ in labels]
    missing = {label: 0 for label in labels}
    support = {label: 0 for label in labels}
    for row in executions:
        expected = Verdict(row["expected_verdict"]).value
        support[expected] += 1
        actual = row.get("actual_verdict")
        if actual is None or row.get("evaluation_status", "judged") != "judged":
            missing[expected] += 1
            continue
        actual = Verdict(actual).value
        matrix[labels.index(expected)][labels.index(actual)] += 1
    total = len(executions)
    completed = sum(sum(row) for row in matrix)
    correct = sum(matrix[i][i] for i in range(len(labels)))
    classes = {}
    for i, label in enumerate(labels):
        tp = matrix[i][i]
        predicted = sum(row[i] for row in matrix)
        precision = tp / predicted if predicted else 0.0
        recall = tp / support[label] if support[label] else 0.0
        classes[label] = {
            "precision": precision,
            "recall": recall,
            "f1": (
                2 * precision * recall / (precision + recall)
                if precision + recall
                else 0.0
            ),
            "support": support[label],
            "predicted": predicted,
        }
    macro = {
        metric: sum(c[metric] for c in classes.values()) / len(labels)
        for metric in ("precision", "recall", "f1")
    }
    weighted = {
        metric: (
            sum(c[metric] * c["support"] for c in classes.values()) / total
            if total
            else None
        )
        for metric in ("precision", "recall", "f1")
    }
    return {
        "labels": labels,
        "matrix_rows": "reference",
        "matrix_columns": "predicted",
        "confusion_matrix": matrix,
        "unclassified_by_reference": missing,
        "total": total,
        "completed": completed,
        "unclassified": total - completed,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "completion_rate": completed / total if total else None,
        "accuracy_on_completed": correct / completed if completed else None,
        "verdict_and_values_accuracy": (
            sum(r.get("confirmed") is True for r in executions) / total
            if total
            else None
        ),
        "per_class": classes,
        "macro_average": macro,
        "weighted_average": weighted,
    }
