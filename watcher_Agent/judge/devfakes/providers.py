"""Development fakes for the model, embedding and values capabilities.

Wired only by ``PROVIDER_MODE=fake`` with ``APP_ENV=development``. Records
produced with them carry ``model_id = "fake-understanding"`` so they can never
be mistaken for real judgments.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from itertools import cycle
from typing import Final

from judge.contracts.storage import EmbeddingProfile
from judge.contracts.understanding import ModelJudgment
from judge.contracts.values import ValuesReadResult
from judge.understanding.prompt import TextPart, UnderstandingRequest

FAKE_MODEL_ID: Final = "fake-understanding"
_VALUE_HEADING: Final = re.compile(r"^## ([A-Za-z][A-Za-z0-9_-]*)\s*:", re.MULTILINE)

Script = Callable[[UnderstandingRequest], ModelJudgment | Exception]


def first_value_id(request: UnderstandingRequest) -> str | None:
    """The first approved value id visible in the request, if any."""
    text = "\n".join(p.text for p in request.parts if isinstance(p, TextPart))
    found = _VALUE_HEADING.search(text)
    return found.group(1) if found else None


def fake_judgment(request: UnderstandingRequest, verdict: str) -> ModelJudgment:
    """A structurally valid synthetic judgment for ``request``."""
    value_id = first_value_id(request)
    cites = verdict != "neutral" and value_id is not None
    heard = request.has_audio
    return ModelJudgment(
        transcript="synthetic development transcript" if heard else "",
        audio_observation="speech" if heard else "no_audio_provided",
        on_screen_text="",
        description="Synthetic development judgment (fake provider).",
        topics=("synthetic",),
        format="synthetic",
        tone="neutral",
        hook="",
        emotional_pull="",
        is_ad=False,
        verdict=verdict if cites or verdict == "neutral" else "neutral",  # type: ignore[arg-type]
        confidence=0.5,
        value_ids=(value_id,) if cites and value_id else (),
        reason="Synthetic reason from the development fake provider.",
        evidence_basis=("frames", "audio") if heard else ("frames",),
    )


class FakeUnderstandingProvider:
    """Scripted understanding provider that records every request."""

    def __init__(
        self, script: Script | None = None, *, verdict_pattern: str = "neutral"
    ) -> None:
        verdicts = [v.strip() for v in verdict_pattern.split(",") if v.strip()]
        self._verdicts = cycle(verdicts or ["neutral"])
        self.script = script
        self.calls: list[UnderstandingRequest] = []
        self.supports_audio = True

    @property
    def model_id(self) -> str:
        return FAKE_MODEL_ID

    async def understand(self, request: UnderstandingRequest) -> ModelJudgment:
        self.calls.append(request)
        if self.script is None:
            return fake_judgment(request, next(self._verdicts))
        outcome = self.script(request)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeEmbeddingProvider:
    """Deterministic, normalized pseudo-embeddings derived from the text."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        self._profile = profile
        self.calls: list[str] = []
        self.failures: list[Exception] = []
        self.dimension_override: int | None = None

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    async def embed(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        if self.failures:
            raise self.failures.pop(0)
        dimension = self.dimension_override or self._profile.dimension
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[i % len(digest)] / 255.0 + 0.01 for i in range(dimension)]
        norm = math.sqrt(sum(value * value for value in raw))
        return tuple(value / norm for value in raw)


class StaticValuesReader:
    """Values reader that returns whatever it is currently set to."""

    def __init__(self, result: ValuesReadResult) -> None:
        self.result = result
        self.reads = 0

    async def read(self, owner_id: str) -> ValuesReadResult:
        self.reads += 1
        return self.result
