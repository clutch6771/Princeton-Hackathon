"""Guardian approval, actual media codecs, and independent decision scoring."""

from dataclasses import replace

import numpy as np
import pytest

from judge.contracts.understanding import Verdict
from judge.evaluation import assess_decision
from tests.support.guardian_samples import (
    OWNER,
    approve_guardian_values,
    demo_samples,
    execute_stream_sample,
    reference_for,
    replay_judgment,
)
from tests.support.harness import Harness
from tests.support.media import sine

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("sample", demo_samples(), ids=lambda sample: sample["id"])
async def test_guardian_values_and_replayed_decision_survive_audio_video_pipeline(
    sample,
) -> None:
    text, values = approve_guardian_values()
    assert values.active_value_ids == {"V1", "V2", "V3"}
    assert values.content == text and "News without outrage" in text
    h = await Harness.create(values=values, embedding_indexing_enabled=False)
    h.owner = OWNER
    h.script = lambda request: replay_judgment(sample, audio=request.has_audio)
    try:
        record = await execute_stream_sample(h, sample, sine(3.0))
        assert record.evaluation_status == "judged"
        assert record.coverage and record.coverage.selected_frame_count == 3
        assert record.coverage.audio_kept_ms > 0
        assert len(h.understanding.calls) == 1 and h.understanding.calls[0].has_audio
        assert record.transcript == sample["transcript"]
        reference = reference_for(sample, values)
        assert assess_decision(record, reference, values).confirmed is True
        # Independent negative controls: arbitrary wrong choices must be denied.
        for verdict in Verdict:
            ids = (
                ()
                if verdict is Verdict.NEUTRAL
                else tuple(reference.value_ids or {"V2"})
            )
            arbitrary = record.model_copy(update={"verdict": verdict, "value_ids": ids})
            assessment = assess_decision(arbitrary, reference, values)
            assert assessment.confirmed is (verdict is reference.verdict)
        wrong_citation = record.model_copy(update={"value_ids": ("V99",)})
        assert assess_decision(wrong_citation, reference, values).confirmed is False
        assert (
            assess_decision(
                record, replace(reference, values_sha256="f" * 64), values
            ).confirmed
            is None
        )
        unavailable = record.model_copy(
            update={"evaluation_status": "failed_provider", "verdict": None}
        )
        assert assess_decision(unavailable, reference, values).confirmed is None
        assert h.runtime.budget.used == 0
    finally:
        await h.close()


async def test_inflammatory_sample_uses_guardians_news_value_not_fitness_value() -> (
    None
):
    _, values = approve_guardian_values()
    sample = next(s for s in demo_samples() if s["id"] == "rl_016")
    assert sample["value_ids"] == ["V2"] and sample["verdict"] == "conflict"
    h = await Harness.create(values=values, embedding_indexing_enabled=False)
    h.owner = OWNER
    # A structurally valid but wrongly cited verdict is detected by evaluation.
    h.script = lambda request: replay_judgment(
        sample, audio=request.has_audio
    ).model_copy(update={"value_ids": ("V1",)})
    try:
        record = await execute_stream_sample(
            h, sample, np.zeros(48_000, dtype=np.int16)
        )
        assert (
            assess_decision(record, reference_for(sample, values), values).confirmed
            is False
        )
    finally:
        await h.close()
