"""Optional local-provider adapter (OpenAI-compatible HTTP endpoint).

For development against a local model server. It sends frames and text only:
local endpoints generally cannot take audio, so ``supports_audio`` is false
and the pipeline withholds the clip and records the transcript status from
evidence (no speech is ever claimed). Selected only by ``PROVIDER_MODE=local``.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
from pydantic import ValidationError

from judge.contracts.storage import EmbeddingProfile
from judge.contracts.understanding import ModelJudgment
from judge.errors import EmbeddingDimensionError, ProviderError, ProviderErrorKind
from judge.understanding.gemini import classify_provider_error
from judge.understanding.prompt import ImagePart, TextPart, UnderstandingRequest


def _content(request: UnderstandingRequest) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for part in request.parts:
        if isinstance(part, TextPart):
            blocks.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            encoded = base64.b64encode(part.jpeg).decode("ascii")
            url = f"data:{part.mime_type};base64,{encoded}"
            blocks.append({"type": "image_url", "image_url": {"url": url}})
    return blocks


async def _post(client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> Any:
    try:
        response = await client.post(path, json=payload)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        error.code = error.response.status_code  # type: ignore[attr-defined]
        raise classify_provider_error(error) from error
    except (httpx.HTTPError, ValueError) as error:
        raise classify_provider_error(error) from error


class LocalUnderstandingProvider:
    """Frames-and-text judgment through ``/chat/completions``."""

    supports_audio = False

    def __init__(self, *, client: httpx.AsyncClient, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def model_id(self) -> str:
        return f"local:{self._model}"

    async def understand(self, request: UnderstandingRequest) -> ModelJudgment:
        payload = {
            "model": self._model,
            "max_tokens": request.max_output_tokens,
            "messages": [
                {"role": "system", "content": request.system_instruction},
                {"role": "user", "content": _content(request)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "model_judgment",
                    "schema": ModelJudgment.model_json_schema(),
                },
            },
        }
        body = await _post(self._client, "/chat/completions", payload)
        try:
            text = body["choices"][0]["message"]["content"]
            return ModelJudgment.model_validate(json.loads(text))
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as error:
            raise ProviderError(
                ProviderErrorKind.SCHEMA, "local model output failed the schema"
            ) from error


class LocalEmbeddingProvider:
    """Embeddings through ``/embeddings``; the dimension must match the profile."""

    def __init__(self, *, client: httpx.AsyncClient, profile: EmbeddingProfile) -> None:
        self._client = client
        self._profile = profile

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    async def embed(self, text: str) -> tuple[float, ...]:
        payload = {"model": self._profile.model_id, "input": text}
        body = await _post(self._client, "/embeddings", payload)
        try:
            vector = tuple(float(value) for value in body["data"][0]["embedding"])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError(
                ProviderErrorKind.SCHEMA, "local embedding response was malformed"
            ) from error
        if len(vector) != self._profile.dimension:
            msg = "local embedding dimension does not match the pinned profile"
            raise EmbeddingDimensionError(msg)
        return vector
