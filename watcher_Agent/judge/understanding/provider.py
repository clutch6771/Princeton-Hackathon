"""Provider capabilities for understanding and embedding.

These are service interfaces called by deterministic backend code. They are
not tools the model can call: the model receives prepared evidence and
returns structured data, nothing more.
"""

from __future__ import annotations

from typing import Protocol

from judge.contracts.storage import EmbeddingProfile
from judge.contracts.understanding import ModelJudgment
from judge.understanding.prompt import UnderstandingRequest


class UnderstandingProvider(Protocol):
    """One joint understand/transcribe/judge call per request.

    Inputs: a prepared ``UnderstandingRequest`` (frames, optional audio,
    delimited values and examples). Output: one ``ModelJudgment``.
    Timeout: enforced by the caller. Side effects: one remote request, no
    storage. Errors: ``ProviderError`` only.
    """

    @property
    def model_id(self) -> str:
        """Exact model identifier, stored with every judgment."""
        ...

    @property
    def supports_audio(self) -> bool:
        """Whether the adapter can pass an audio clip to the model."""
        ...

    async def understand(self, request: UnderstandingRequest) -> ModelJudgment:
        """Make exactly one remote call. Never retries internally."""
        ...


class EmbeddingProvider(Protocol):
    """One embedding of description plus transcript per judged reel.

    Output length always equals ``profile.dimension``. Errors:
    ``ProviderError`` or ``EmbeddingDimensionError``.
    """

    @property
    def profile(self) -> EmbeddingProfile:
        """The pinned vector space this provider produces."""
        ...

    async def embed(self, text: str) -> tuple[float, ...]:
        """Embed stored text. Never receives media."""
        ...
