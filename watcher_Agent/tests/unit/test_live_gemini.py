"""Opt-in live smoke tests against the real Gemini API.

Never run by default (``-m live`` and ``GEMINI_API_KEY`` are both required).
They send only synthetic media. They check that the pinned model accepts
frames, audio and the response schema together; they say nothing about
judgment quality.
"""

from __future__ import annotations

import os

import pytest

from judge.contracts.candidate import AudioStatus
from judge.contracts.storage import EmbeddingProfile
from judge.contracts.understanding import ModelJudgment
from judge.transcriber.prepare import PreparedAudio
from judge.understanding.gemini import (
    GeminiEmbeddingProvider,
    GeminiUnderstandingProvider,
)
from judge.understanding.prompt import PromptContext, build_understanding_request
from tests.support.media import make_frame_jpeg, sine
from tests.support.samples import snapshot
from tests.support.settings import make_settings

pytestmark = pytest.mark.live

SKIP = "GEMINI_API_KEY is not set; live provider tests were not run"


def _key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        pytest.skip(SKIP)
    return key


async def test_live_understanding_accepts_frames_audio_and_schema() -> None:
    from judge.audio.wav import pcm_to_wav

    settings = make_settings()
    audio = PreparedAudio(
        wav=pcm_to_wav(sine(3.0), 16_000),
        status=AudioStatus.COMPLETE,
        window_ms=3000,
        kept_ms=3000,
        uncertified_ms=0,
        protected_ms=0,
        corrupt_ms=0,
        missing_ms=0,
    )
    request = build_understanding_request(
        PromptContext(
            frames=tuple(make_frame_jpeg(scene=s, overlay=s) for s in (1, 2)),
            audio=audio,
            first_second_observed=False,
            observed_ms=3000,
            values=snapshot(),
            examples=(),
        ),
        prompt_version=settings.understanding_prompt_version,
        max_output_tokens=settings.understanding_max_output_tokens,
    )
    provider = GeminiUnderstandingProvider(
        model_id=settings.understanding_model_id,
        api_key=_key(),
        timeout_seconds=30,
        max_output_tokens=settings.understanding_max_output_tokens,
    )

    judgment = await provider.understand(request)

    assert isinstance(judgment, ModelJudgment)
    assert judgment.verdict in {"aligned", "neutral", "conflict"}


async def test_live_embedding_returns_the_pinned_dimension() -> None:
    profile = EmbeddingProfile.from_settings(make_settings())
    provider = GeminiEmbeddingProvider(profile=profile, api_key=_key())

    vector = await provider.embed("A synthetic description.\nA synthetic transcript.")

    assert len(vector) == profile.dimension
