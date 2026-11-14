"""xAI request contract tests. HTTP is mocked; no recognition accuracy claim."""

import json
from typing import Any

import httpx
import pytest

from judge.config import ProviderMode, validate_for_startup
from judge.errors import ConfigurationError, ProviderError
from judge.runtime_factory import create_runtime
from judge.understanding.grok import GrokUnderstandingProvider
from judge.understanding.prompt import PromptContext, build_understanding_request
from tests.support.guardian_samples import (
    approve_guardian_values,
    demo_samples,
    jpeg_bytes,
    replay_judgment,
    sample_card,
)
from tests.support.samples import prepared_audio
from tests.support.settings import make_settings

pytestmark = pytest.mark.unit


def request(*, audio: bool = True) -> Any:
    _, values = approve_guardian_values()
    sample = demo_samples()[3]
    return build_understanding_request(
        PromptContext(
            frames=(jpeg_bytes(sample_card(sample)),),
            audio=prepared_audio(supplied=audio),
            first_second_observed=False,
            observed_ms=6000,
            values=values,
            examples=(),
        ),
        prompt_version="grok-test",
        max_output_tokens=2048,
    )


def completion(**changes: Any) -> dict[str, Any]:
    judgment = replay_judgment(demo_samples()[3]).model_dump(mode="json")
    judgment.update(changes)
    return {"choices": [{"message": {"content": json.dumps(judgment)}}]}


async def test_grok_transcribes_then_judges_images_with_guardian_values() -> None:
    seen = []
    transcript = demo_samples()[3]["transcript"]

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.headers["authorization"] == "Bearer fake-xai-test-key"
        return httpx.Response(
            200,
            json={"text": transcript} if req.url.path == "/v1/stt" else completion(),
        )

    provider = GrokUnderstandingProvider(
        api_key="fake-xai-test-key", transport=httpx.MockTransport(handler)
    )
    result = await provider.understand(request())
    assert [r.url.path for r in seen] == ["/v1/stt", "/v1/chat/completions"]
    assert b'filename="reel.wav"' in seen[0].content
    body = json.loads(seen[1].content)
    assert body["model"] == "grok-4.7" and body["reasoning_effort"] == "low"
    content = body["messages"][1]["content"]
    text = " ".join(p["text"] for p in content if p["type"] == "text")
    assert "News without outrage" in text and transcript in text
    assert any(p["type"] == "image_url" for p in content)
    assert body["response_format"]["type"] == "json_schema" and "tools" not in body
    assert result.verdict == "conflict" and result.value_ids == ("V2",)
    assert result.transcript == transcript and result.audio_observation == "speech"


async def test_grok_native_text_path_has_no_extra_transcription_call() -> None:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=completion())

    provider = GrokUnderstandingProvider(
        api_key="fake-xai", transport=httpx.MockTransport(handler)
    )
    await provider.understand(request(audio=False))
    assert [r.url.path for r in seen] == ["/v1/chat/completions"]


@pytest.mark.parametrize("status", [401, 429, 503])
async def test_grok_errors_are_sanitized_and_stop_before_judgment(status: int) -> None:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, json={"error": "sensitive-provider-detail"})

    provider = GrokUnderstandingProvider(
        api_key="fake-xai", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderError) as error:
        await provider.understand(request())
    assert "sensitive-provider-detail" not in str(error.value)
    assert len(seen) == 1


@pytest.mark.parametrize("stt", [{}, {"text": "x" * 4001}])
async def test_grok_rejects_missing_or_oversized_transcripts(
    stt: dict[str, str],
) -> None:
    provider = GrokUnderstandingProvider(
        api_key="fake-xai",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=stt)),
    )
    with pytest.raises(ProviderError, match="STT"):
        await provider.understand(request())


async def test_grok_empty_stt_does_not_claim_music_or_silence() -> None:
    provider = GrokUnderstandingProvider(
        api_key="fake-xai",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"text": ""} if req.url.path == "/v1/stt" else completion()
            )
        ),
    )
    result = await provider.understand(request())
    assert result.transcript == "" and result.audio_observation == "unintelligible"


async def test_grok_runtime_needs_only_xai_key_and_disables_embedding_work() -> None:
    settings = make_settings(
        provider_mode=ProviderMode.GROK,
        xai_api_key="fake-xai",
        embedding_indexing_enabled=False,
    )
    validate_for_startup(settings, environ={})
    runtime = await create_runtime(settings)
    try:
        assert runtime.understanding.model_id == "grok-4.7"
        assert runtime.understanding.supports_audio
        assert settings.gemini_api_key is None
    finally:
        await runtime.stop()
    with pytest.raises(ConfigurationError, match="XAI_API_KEY"):
        validate_for_startup(
            make_settings(provider_mode="grok", embedding_indexing_enabled=False),
            environ={},
        )
    with pytest.raises(ConfigurationError, match="EMBEDDING_INDEXING_ENABLED"):
        validate_for_startup(
            make_settings(provider_mode="grok", xai_api_key="fake-xai"), environ={}
        )
