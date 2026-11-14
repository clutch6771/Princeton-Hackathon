"""Gemini adapters (via LangChain) for understanding and embedding.

One ``generateContent`` request carries the frames, the audio clip, the
delimited text and the response schema, and returns the structured judgment.
The adapters never retry: the pipeline owns the single bounded retry so that
every attempt is counted.

Provider-side processing is outside this code's control. Releasing media
locally does not establish provider-side zero retention; see PRIVACY.txt.
SDK tracing must stay disabled (startup refuses to run otherwise).
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from google.genai import errors as genai_errors
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from pydantic import SecretStr, ValidationError

from judge.contracts.storage import EmbeddingProfile
from judge.contracts.understanding import ModelJudgment
from judge.errors import EmbeddingDimensionError, ProviderError, ProviderErrorKind
from judge.understanding.prompt import (
    AudioPart,
    ImagePart,
    TextPart,
    UnderstandingRequest,
)

_QUOTA_STATUS = 429
_SERVER_ERROR_FLOOR = 500


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def to_messages(request: UnderstandingRequest) -> list[BaseMessage]:
    """Translate the provider-neutral request into LangChain messages."""
    blocks: list[str | dict[Any, Any]] = []
    for part in request.parts:
        if isinstance(part, TextPart):
            blocks.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            url = f"data:{part.mime_type};base64,{_b64(part.jpeg)}"
            blocks.append({"type": "image_url", "image_url": {"url": url}})
        elif isinstance(part, AudioPart):
            blocks.append(
                {"type": "media", "mime_type": part.mime_type, "data": _b64(part.wav)}
            )
    return [SystemMessage(request.system_instruction), HumanMessage(content=blocks)]


def _chain(error: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def classify_provider_error(error: BaseException) -> ProviderError:
    """Map SDK and transport failures to a sanitized ``ProviderError``."""
    for item in _chain(error):
        code = getattr(item, "code", None)
        if isinstance(item, TimeoutError | httpx.TimeoutException):
            return ProviderError(
                ProviderErrorKind.TIMEOUT, "provider timed out", outcome_unknown=True
            )
        if code == _QUOTA_STATUS:
            return ProviderError(ProviderErrorKind.QUOTA, "provider quota exceeded")
        if isinstance(item, genai_errors.ServerError) or (
            isinstance(code, int) and code >= _SERVER_ERROR_FLOOR
        ):
            return ProviderError(
                ProviderErrorKind.TRANSIENT,
                "provider unavailable",
                outcome_unknown=True,
            )
        if isinstance(item, httpx.TransportError):
            return ProviderError(
                ProviderErrorKind.TRANSIENT, "network failure", outcome_unknown=True
            )
        if isinstance(item, ValidationError):
            return ProviderError(ProviderErrorKind.SCHEMA, "output failed the schema")
    return ProviderError(ProviderErrorKind.PERMANENT, "provider rejected the request")


class GeminiUnderstandingProvider:
    """Joint understand/transcribe/judge call on a Gemini Flash-class model."""

    supports_audio = True

    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        timeout_seconds: float,
        max_output_tokens: int,
        client_args: dict[str, Any] | None = None,
    ) -> None:
        self._model_id = model_id
        model = ChatGoogleGenerativeAI(
            model=model_id,
            api_key=api_key,
            max_retries=0,
            timeout=timeout_seconds,
            max_output_tokens=max_output_tokens,
            client_args=client_args,
        )
        self._structured = model.with_structured_output(
            ModelJudgment, method="json_schema", include_raw=True
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    async def understand(self, request: UnderstandingRequest) -> ModelJudgment:
        try:
            # No callbacks: prompts (frames, audio, values) are never traced.
            result = await self._structured.ainvoke(
                to_messages(request), config={"callbacks": []}
            )
        except Exception as error:
            raise classify_provider_error(error) from error
        parsed = result.get("parsed") if isinstance(result, dict) else result
        if not isinstance(parsed, ModelJudgment):
            raise ProviderError(
                ProviderErrorKind.SCHEMA, "model returned no structured judgment"
            )
        return parsed


class GeminiEmbeddingProvider:
    """One embedding of description plus transcript, in a pinned profile."""

    def __init__(
        self,
        *,
        profile: EmbeddingProfile,
        api_key: str,
        client_args: dict[str, Any] | None = None,
    ) -> None:
        self._profile = profile
        self._embeddings = GoogleGenerativeAIEmbeddings(
            model=profile.model_id,
            api_key=SecretStr(api_key),
            task_type=profile.task_type,
            output_dimensionality=profile.dimension,
            client_args=client_args,
        )

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    async def embed(self, text: str) -> tuple[float, ...]:
        try:
            vector = await self._embeddings.aembed_query(text)
        except Exception as error:
            raise classify_provider_error(error) from error
        if len(vector) != self._profile.dimension:
            msg = (
                f"embedding dimension {len(vector)} does not match the pinned "
                f"dimension {self._profile.dimension}"
            )
            raise EmbeddingDimensionError(msg)
        return tuple(float(value) for value in vector)
