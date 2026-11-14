"""Confusion-matrix orientation, denominators and failure visibility."""

import pytest

from judge.evaluation import classification_metrics

pytestmark = pytest.mark.unit


def test_metrics_count_misclassifications_and_missing_verdicts() -> None:
    pairs = [
        ("aligned", "aligned"),
        ("aligned", "conflict"),
        ("neutral", "neutral"),
        ("neutral", None),
        ("conflict", "conflict"),
        ("conflict", "neutral"),
    ]
    rows = [
        {
            "expected_verdict": expected,
            "actual_verdict": actual,
            "confirmed": actual == expected,
            "evaluation_status": "judged" if actual else "failed_provider",
        }
        for expected, actual in pairs
    ]
    result = classification_metrics(rows)
    assert result["labels"] == ["aligned", "neutral", "conflict"]
    assert result["confusion_matrix"] == [[1, 0, 1], [0, 1, 0], [0, 1, 1]]
    assert result["unclassified_by_reference"] == {
        "aligned": 0,
        "neutral": 1,
        "conflict": 0,
    }
    assert result["accuracy"] == 0.5
    assert result["accuracy_on_completed"] == 0.6
    assert result["completion_rate"] == pytest.approx(5 / 6)
    assert result["per_class"]["aligned"]["precision"] == 1.0
    assert result["per_class"]["aligned"]["recall"] == 0.5
    assert result["macro_average"]["f1"] == pytest.approx(5 / 9)
    assert result["weighted_average"]["f1"] == pytest.approx(5 / 9)


def test_empty_evaluation_does_not_claim_perfect_accuracy() -> None:
    result = classification_metrics([])
    assert result["accuracy"] is None
    assert result["accuracy_on_completed"] is None
    assert result["completed"] == 0


def test_verdict_accuracy_is_separate_from_value_citation_accuracy() -> None:
    result = classification_metrics(
        [
            {
                "expected_verdict": "conflict",
                "actual_verdict": "conflict",
                "confirmed": False,
            },
        ]
    )
    assert result["accuracy"] == 1.0
    assert result["verdict_and_values_accuracy"] == 0.0
