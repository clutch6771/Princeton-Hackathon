"""Real provider adapters against a stubbed HTTP transport, and wiring.

These tests prove how requests are built and how responses and failures are
mapped. They do not prove Gemini's quality, quotas or availability.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from judge.config import ProviderMode, StorageMode
from judge.contracts.candidate import AudioStatus
from judge.contracts.storage import EmbeddingProfile
from judge.devfakes.memory_storage import MemoryStorage
from judge.errors import (
    ConfigurationError,
    EmbeddingDimensionError,
    ProviderError,
    ProviderErrorKind,
)
from judge.persistence.postgres import resolve_conninfo
from judge.runtime_factory import create_runtime
from judge.understanding.gemini import (
    GeminiEmbeddingProvider,
    GeminiUnderstandingProvider,
    classify_provider_error,
)
from judge.understanding.local_openai import (
    LocalEmbeddingProvider,
    LocalUnderstandingProvider,
)
from judge.understanding.prompt import (
    PromptContext,
    UnderstandingRequest,
    build_understanding_request,
)
from tests.support.samples import FAKE_WAV, judgment, prepared_audio, snapshot
from tests.support.settings import make_production_settings, make_settings

pytestmark = pytest.mark.unit

FRAMES = (b"\xff\xd8\xffone", b"\xff\xd8\xfftwo", b"\xff\xd8\xffthree")
PROFILE = EmbeddingProfile.from_settings(make_settings())
JUDGMENT = judgment().model_dump(mode="json")


def _request(audio: bool = True) -> UnderstandingRequest:
    status = AudioStatus.COMPLETE if audio else AudioStatus.MISSING
    return build_understanding_request(
        PromptContext(
            frames=FRAMES,
            audio=prepared_audio(status),
            first_second_observed=False,
            observed_ms=12_000,
            values=snapshot(),
            examples=(),
        ),
        prompt_version="judge-v1",
        max_output_tokens=512,
    )


def _generate_response(payload: Any) -> httpx.Response:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": text}]},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        },
    )


def _gemini(handler: Any) -> tuple[GeminiUnderstandingProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = GeminiUnderstandingProvider(
        model_id="gemini-3.5-flash-lite",
        api_key="fake-key",
        timeout_seconds=5,
        max_output_tokens=512,
        client_args={"transport": httpx.MockTransport(record)},
    )
    return provider, seen


async def test_gemini_sends_frames_audio_and_schema_in_one_request() -> None:
    provider, seen = _gemini(lambda _r: _generate_response(JUDGMENT))

    result = await provider.understand(_request())

    assert result.verdict == "aligned" and result.value_ids == ("V3",)
    assert len(seen) == 1
    assert seen[0].url.path.endswith("gemini-3.5-flash-lite:generateContent")
    body = json.loads(seen[0].content)
    parts = body["contents"][0]["parts"]
    mime_types = [p["inlineData"]["mime_type"] for p in parts if "inlineData" in p]
    assert mime_types == ["image/jpeg"] * 3 + ["audio/wav"]
    assert "responseJsonSchema" in body["generationConfig"]
    assert "tools" not in body
    assert body["systemInstruction"]["parts"][0]["text"].startswith("You are the Judge")
    assert provider.model_id == "gemini-3.5-flash-lite"
    assert provider.supports_audio is True


async def test_gemini_request_without_audio_has_no_audio_part() -> None:
    provider, seen = _gemini(lambda _r: _generate_response(JUDGMENT))

    await provider.understand(_request(audio=False))

    parts = json.loads(seen[0].content)["contents"][0]["parts"]
    assert [p["inlineData"]["mime_type"] for p in parts if "inlineData" in p] == [
        "image/jpeg"
    ] * 3


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(429, json={"error": {"message": "quota"}}), "quota"),
        (httpx.Response(503, json={"error": {"message": "down"}}), "transient"),
        (httpx.Response(400, json={"error": {"message": "bad"}}), "permanent"),
        (_generate_response("this is not json"), "schema"),
        (_generate_response({"verdict": "aligned"}), "schema"),
    ],
)
async def test_gemini_failures_are_classified_and_never_retried_by_the_adapter(
    response: httpx.Response, kind: str
) -> None:
    provider, seen = _gemini(lambda _r: response)

    with pytest.raises(ProviderError) as excinfo:
        await provider.understand(_request())

    assert excinfo.value.kind.value == kind
    assert len(seen) == 1
    assert "fake-key" not in str(excinfo.value)


def test_timeouts_and_transport_errors_have_unknown_remote_outcome() -> None:
    timeout = classify_provider_error(httpx.ReadTimeout("slow"))
    network = classify_provider_error(httpx.ConnectError("refused"))

    assert (timeout.kind, timeout.outcome_unknown) == (ProviderErrorKind.TIMEOUT, True)
    assert (network.kind, network.outcome_unknown) == (
        ProviderErrorKind.TRANSIENT,
        True,
    )


async def test_gemini_embedding_pins_model_task_and_dimension() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [{"values": [0.1] * 768}]})

    provider = GeminiEmbeddingProvider(
        profile=PROFILE,
        api_key="fake-key",
        client_args={"transport": httpx.MockTransport(handler)},
    )

    vector = await provider.embed("description\ntranscript")

    request = seen[0]["requests"][0]
    assert len(vector) == 768 and len(seen) == 1
    assert request["model"] == "models/gemini-embedding-001"
    assert request["taskType"] == "SEMANTIC_SIMILARITY"
    assert request["outputDimensionality"] == 768
    assert request["content"]["parts"] == [{"text": "description\ntranscript"}]


async def test_gemini_embedding_rejects_a_dimension_mismatch() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [{"values": [0.1] * 384}]})

    provider = GeminiEmbeddingProvider(
        profile=PROFILE,
        api_key="fake-key",
        client_args={"transport": httpx.MockTransport(handler)},
    )

    with pytest.raises(EmbeddingDimensionError):
        await provider.embed("text")


async def test_local_provider_sends_frames_and_text_but_never_audio() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.2] * 768}]})
        content = json.dumps(JUDGMENT)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    client = httpx.AsyncClient(
        base_url="http://local.test/v1", transport=httpx.MockTransport(handler)
    )
    understanding = LocalUnderstandingProvider(client=client, model="local-vision")
    embedding = LocalEmbeddingProvider(client=client, profile=PROFILE)

    result = await understanding.understand(_request())
    vector = await embedding.embed("text")

    blocks = seen[0]["messages"][1]["content"]
    assert result.verdict == "aligned"
    assert understanding.supports_audio is False
    assert understanding.model_id == "local:local-vision"
    assert [b["type"] for b in blocks].count("image_url") == 3
    assert FAKE_WAV.hex() not in json.dumps(seen[0])
    assert all(b["type"] in {"text", "image_url"} for b in blocks)
    assert len(vector) == 768
    await client.aclose()


async def test_local_provider_maps_bad_output_and_http_errors() -> None:
    responses = [
        httpx.Response(200, json={"choices": [{"message": {"content": "nope"}}]}),
        httpx.Response(500, json={}),
        httpx.Response(200, json={"data": [{"embedding": [0.2] * 10}]}),
    ]
    client = httpx.AsyncClient(
        base_url="http://local.test/v1",
        transport=httpx.MockTransport(lambda _r: responses.pop(0)),
    )
    understanding = LocalUnderstandingProvider(client=client, model="m")

    with pytest.raises(ProviderError) as schema:
        await understanding.understand(_request())
    with pytest.raises(ProviderError) as server:
        await understanding.understand(_request())
    with pytest.raises(EmbeddingDimensionError):
        await LocalEmbeddingProvider(client=client, profile=PROFILE).embed("t")

    assert schema.value.kind is ProviderErrorKind.SCHEMA
    assert server.value.kind is ProviderErrorKind.TRANSIENT
    await client.aclose()


# --- wiring and TLS ---------------------------------------------------------


async def test_development_runtime_uses_explicit_fakes_and_memory() -> None:
    runtime = await create_runtime(make_settings(fake_verdict_pattern="conflict"))
    try:
        assert isinstance(runtime.storage, MemoryStorage)
        assert runtime.understanding.model_id == "fake-understanding"
        assert runtime.settings.provider_mode is ProviderMode.FAKE
    finally:
        await runtime.stop()


async def test_gemini_mode_without_a_key_fails_clearly() -> None:
    settings = make_settings(
        provider_mode=ProviderMode.GEMINI, storage_mode=StorageMode.MEMORY
    )

    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        await create_runtime(settings)


async def test_local_mode_wires_the_local_adapters() -> None:
    settings = make_settings(
        provider_mode=ProviderMode.LOCAL,
        local_provider_base_url="http://local.test/v1",
        local_understanding_model="local-vision",
    )

    runtime = await create_runtime(settings)
    try:
        assert runtime.understanding.model_id == "local:local-vision"
        assert runtime.understanding.supports_audio is False
    finally:
        await runtime.stop()


def test_production_database_url_must_use_tls() -> None:
    plain = "postgresql://judge:fake@db.example.test/judge"

    secured = resolve_conninfo(plain, production=True, statement_timeout_ms=5000)

    assert "sslmode=require" in secured
    assert "statement_timeout=5000" in secured
    with pytest.raises(ConfigurationError, match="TLS"):
        resolve_conninfo(
            plain + "?sslmode=disable", production=True, statement_timeout_ms=5000
        )


def test_development_database_url_may_be_local_without_tls() -> None:
    local = "postgresql://judge@127.0.0.1:5432/judge"

    conninfo = resolve_conninfo(local, production=False, statement_timeout_ms=1000)

    assert "sslmode" not in conninfo
    assert "statement_timeout=1000" in conninfo


def test_production_settings_shape_is_accepted_by_startup_validation() -> None:
    from judge.config import validate_for_startup

    validate_for_startup(make_production_settings(), environ={})
