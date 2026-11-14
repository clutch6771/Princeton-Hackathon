"""Explicit opt-in recognition checks using only invented example content."""

import os
from pathlib import Path

import pytest

from judge.audio.wav import pcm_to_wav
from judge.config import load_settings
from judge.contracts.candidate import AudioStatus
from judge.transcriber.prepare import PreparedAudio
from judge.understanding.grok import GrokUnderstandingProvider
from judge.understanding.prompt import PromptContext, build_understanding_request
from judge.understanding.validation import validate_judgment
from tests.support.guardian_samples import (
    approve_guardian_values,
    demo_samples,
    jpeg_bytes,
    read_speech,
    sample_card,
)

pytestmark = pytest.mark.live


async def test_live_grok_detects_outrage_against_guardian_value(
    request: pytest.FixtureRequest,
) -> None:
    expression = request.config.getoption("-m") or ""
    if "live" not in expression or "not live" in expression:
        pytest.skip("live Grok tests require explicit -m live")
    settings = load_settings()
    if (
        settings.xai_api_key is None
        or not settings.xai_api_key.get_secret_value().strip()
    ):
        pytest.skip("Set XAI_API_KEY in watcher_Agent/.env for real recognition")
    directory = os.environ.get("JUDGE_SAMPLE_SPEECH_DIR")
    if not directory:
        pytest.skip("Set JUDGE_SAMPLE_SPEECH_DIR to the generated speech directory")
    speech = Path(directory) / "rl_016.aiff"
    if not speech.is_file():
        pytest.skip(
            "Run scripts/generate_sample_speech.py first (see SAMPLE_EXECUTIONS.txt)"
        )
    pcm = read_speech(str(speech))
    duration = round(len(pcm) / 16)
    audio = PreparedAudio(
        wav=pcm_to_wav(pcm, 16_000),
        status=AudioStatus.COMPLETE,
        window_ms=duration,
        kept_ms=duration,
        uncertified_ms=0,
        protected_ms=0,
        corrupt_ms=0,
        missing_ms=0,
    )
    sample = next(s for s in demo_samples() if s["id"] == "rl_016")
    _, values = approve_guardian_values()
    prompt = build_understanding_request(
        PromptContext(
            frames=(jpeg_bytes(sample_card(sample)),),
            audio=audio,
            first_second_observed=False,
            observed_ms=duration,
            values=values,
            examples=(),
        ),
        prompt_version=settings.understanding_prompt_version,
        max_output_tokens=2048,
    )
    provider = GrokUnderstandingProvider(
        api_key=settings.xai_api_key.get_secret_value(),
        model_id=settings.grok_model_id,
        transcription_model=settings.grok_transcription_model,
        timeout_seconds=30,
    )
    judgment = await provider.understand(prompt)
    result = validate_judgment(
        judgment,
        snapshot=values,
        audio=audio,
        first_second_observed=False,
        transcript_max_chars=4000,
    )
    assert result.verdict.value == "conflict" and "V2" in result.value_ids
    assert result.transcript
