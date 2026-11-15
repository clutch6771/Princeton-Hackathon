"""Understand, transcribe and judge one exposure, then commit one outcome.

Normal path: one understanding call per reel. There is no preliminary speech
recognition, no captioning call and no repair loop. A transient failure may
be retried once inside the job deadline; every attempt is counted.

Raw media is released in a ``finally`` block: on success, failure, timeout
and cancellation alike.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from judge.config import Settings
from judge.contracts.feedback import FeedbackExample
from judge.contracts.record import EvaluationStatus, ReelCompletion, StageTimings
from judge.contracts.storage import ThumbnailRecord
from judge.contracts.understanding import ModelJudgment
from judge.contracts.values import (
    ApprovedValuesSnapshot,
    ValuesReadResult,
    ValuesUnavailable,
)
from judge.errors import (
    OutputValidationError,
    ProviderError,
    ProviderErrorKind,
    ReelErrorCode,
)
from judge.feedback.retriever import FeedbackRetriever
from judge.pipeline.completion import ReelCompletionService
from judge.pipeline.job import JudgeJob, make_thumbnail
from judge.pipeline.outcomes import failed_outcome, judged_outcome
from judge.runtime.clock import Clock
from judge.runtime.media_budget import MediaBudget
from judge.runtime.rate_limiter import DailyQuota, RateLimiter
from judge.transcriber.prepare import PreparedAudio
from judge.understanding.prompt import (
    PromptContext,
    UnderstandingRequest,
    build_understanding_request,
    estimate_input_tokens,
)
from judge.understanding.provider import UnderstandingProvider
from judge.understanding.validation import validate_judgment
from judge.values.reader import ValuesReader

logger = logging.getLogger(__name__)
_MS = 1000
_RETRY_PAUSE_SECONDS = 1.0


class _Failed(Exception):
    """Internal: the job ends in a non-judged terminal state."""

    def __init__(
        self,
        status: EvaluationStatus,
        code: ReelErrorCode,
        *,
        attempts: int = 0,
        values: ApprovedValuesSnapshot | None = None,
    ) -> None:
        super().__init__(code.value)
        self.status = status
        self.code = code
        self.attempts = attempts
        self.values = values


class ValuesGate:
    """Bounded wait for approved values.

    While values are unavailable a job waits in RAM, polling, for at most
    ``wait_seconds`` measured from when they first became unavailable for
    that owner. After that every job gets one read and is skipped, so an
    outage cannot stall the queue. Discarded media cannot be recovered by a
    later approval.
    """

    def __init__(
        self,
        reader: ValuesReader,
        *,
        clock: Clock,
        wait_seconds: float,
        poll_seconds: float,
    ) -> None:
        self.reader = reader
        self._clock = clock
        self._wait = wait_seconds
        self._poll = poll_seconds
        self._unavailable_since: dict[str, float] = {}

    def invalidate(self, owner_id: str) -> None:
        """Forget cached state for an owner (values changed or were deleted)."""
        self._unavailable_since.pop(owner_id, None)
        invalidate = getattr(self.reader, "invalidate", None)
        if callable(invalidate):
            invalidate(owner_id)

    async def acquire(self, owner_id: str, *, deadline: float) -> ValuesReadResult:
        """Return a pinned snapshot, or the reason none became available."""
        result = await self.reader.read(owner_id)
        while isinstance(result, ValuesUnavailable):
            now = self._clock.monotonic()
            since = self._unavailable_since.setdefault(owner_id, now)
            give_up_at = min(since + self._wait, deadline)
            if now + self._poll > give_up_at:
                return result
            await self._clock.sleep(self._poll)
            result = await self.reader.read(owner_id)
        self._unavailable_since.pop(owner_id, None)
        return result


@dataclass(frozen=True, slots=True)
class _Marks:
    """Monotonic timestamps of the stages of one job."""

    started: float
    context_done: float = 0.0
    inference_done: float = 0.0
    validated: float = 0.0


class JudgePipeline:
    """Runs steps 6 to 10 of the workflow for one job."""

    def __init__(
        self,
        *,
        settings: Settings,
        clock: Clock,
        provider: UnderstandingProvider,
        values_gate: ValuesGate,
        retriever: FeedbackRetriever,
        completion: ReelCompletionService,
        budget: MediaBudget,
        limiter: RateLimiter,
        daily_quota: DailyQuota,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._provider = provider
        self.values_gate = values_gate
        self._retriever = retriever
        self._completion = completion
        self._budget = budget
        self._limiter = limiter
        self._daily_quota = daily_quota

    async def run(self, job: JudgeJob) -> None:
        """Process one job and commit exactly one outcome for it."""
        started = self._clock.monotonic()
        thumbnail: ThumbnailRecord | None = None
        marks = _Marks(started=started)
        try:
            outcome, thumbnail, marks = await self._judge(job, started)
        except _Failed as failure:
            outcome = failed_outcome(
                job,
                failure.status,
                failure.code,
                now=self._clock.now(),
                attempts=failure.attempts,
                values=failure.values,
                model_id=self._provider.model_id,
                prompt_version=self._settings.understanding_prompt_version,
            )
        except Exception:
            # An unexpected fault still ends in an explicit, non-neutral state.
            logger.exception("judge job failed unexpectedly")
            outcome = failed_outcome(
                job,
                EvaluationStatus.FAILED_PROVIDER,
                ReelErrorCode.INTERNAL,
                now=self._clock.now(),
            )
        finally:
            # Final consumer of the media is done: release it on every path.
            self._budget.release(job.candidate.session_id, job.reserved_bytes)
        committed = await self._completion.complete(
            outcome, thumbnail=thumbnail, timings=self._timings(job, marks)
        )
        if not committed:
            logger.info(
                "late outcome discarded; the reel was already finalized",
                extra={"reel_id": str(job.candidate.candidate_id)},
            )

    def _deadline(self, job: JudgeJob) -> float:
        age = (self._clock.now() - job.created_at).total_seconds()
        return self._clock.monotonic() + self._settings.job_deadline_seconds - age

    async def _judge(
        self, job: JudgeJob, started: float
    ) -> tuple[ReelCompletion, ThumbnailRecord | None, _Marks]:
        deadline = self._deadline(job)
        if self._clock.monotonic() >= deadline:
            raise _Failed(EvaluationStatus.FAILED_TIMEOUT, ReelErrorCode.JOB_DEADLINE)
        observed_frames = job.candidate.coverage.frame_count
        if not job.frames or observed_frames < self._settings.min_judgeable_frames:
            raise _Failed(
                EvaluationStatus.INSUFFICIENT_EVIDENCE, ReelErrorCode.TOO_FEW_FRAMES
            )
        values = await self.values_gate.acquire(
            job.candidate.owner_id, deadline=deadline
        )
        if isinstance(values, ValuesUnavailable):
            raise _Failed(EvaluationStatus.VALUES_UNAVAILABLE, values.reel_error_code)
        examples = await self._examples(job.candidate.owner_id, values)
        audio = self._audio_for_provider(job.audio)
        request = build_understanding_request(
            PromptContext(
                frames=tuple(frame.jpeg for frame in job.frames),
                audio=audio,
                first_second_observed=job.candidate.first_second_observed,
                observed_ms=job.candidate.coverage.observed_ms,
                values=values,
                examples=examples,
                native_transcript=job.native_transcript,
            ),
            prompt_version=self._settings.understanding_prompt_version,
            max_output_tokens=self._settings.understanding_max_output_tokens,
        )
        context_done = self._clock.monotonic()
        judgment, attempts = await self._call(request, audio, deadline, values)
        inference_done = self._clock.monotonic()
        try:
            result = validate_judgment(
                judgment,
                snapshot=values,
                audio=audio,
                first_second_observed=job.candidate.first_second_observed,
                transcript_max_chars=self._settings.transcript_max_chars,
                native_transcript=job.native_transcript,
            )
        except OutputValidationError as error:
            raise _Failed(
                EvaluationStatus.FAILED_VALIDATION,
                error.code,
                attempts=attempts,
                values=values,
            ) from error
        thumbnail = await asyncio.to_thread(make_thumbnail, job, self._settings)
        outcome = judged_outcome(
            job,
            result,
            values=values,
            examples=examples,
            model_id=self._provider.model_id,
            prompt_version=self._settings.understanding_prompt_version,
            attempts=attempts,
            now=self._clock.now(),
        )
        marks = _Marks(started, context_done, inference_done, self._clock.monotonic())
        return outcome, thumbnail, marks

    def _audio_for_provider(self, audio: PreparedAudio) -> PreparedAudio:
        if self._provider.supports_audio or not audio.supplied:
            return audio
        # The adapter cannot pass audio: withhold the clip, keep the evidence.
        return PreparedAudio(
            wav=None,
            status=audio.status,
            window_ms=audio.window_ms,
            kept_ms=audio.kept_ms,
            uncertified_ms=audio.uncertified_ms,
            protected_ms=audio.protected_ms,
            corrupt_ms=audio.corrupt_ms,
            missing_ms=audio.missing_ms,
        )

    async def _examples(
        self, owner_id: str, values: ApprovedValuesSnapshot
    ) -> tuple[FeedbackExample, ...]:
        try:
            return await self._retriever.retrieve(owner_id, values)
        except Exception:
            # Zero examples is a valid input; a retrieval fault must not
            # cost the judgment.
            logger.exception("feedback retrieval failed; judging without examples")
            return ()

    async def _call(
        self,
        request: UnderstandingRequest,
        audio: PreparedAudio,
        deadline: float,
        values: ApprovedValuesSnapshot,
    ) -> tuple[ModelJudgment, int]:
        failed = EvaluationStatus.FAILED_PROVIDER
        estimate = estimate_input_tokens(request, audio_ms=audio.kept_ms)
        if estimate > self._settings.understanding_max_input_tokens:
            raise _Failed(failed, ReelErrorCode.INPUT_TOKEN_BUDGET, values=values)
        attempts = 0
        while True:
            await self._reserve_capacity(deadline, attempts, values)
            attempts += 1
            try:
                timeout = min(
                    self._settings.provider_timeout_seconds,
                    max(0.001, deadline - self._clock.monotonic()),
                )
                return (
                    await asyncio.wait_for(self._provider.understand(request), timeout),
                    attempts,
                )
            except TimeoutError as error:
                last = ProviderError(
                    ProviderErrorKind.TIMEOUT, "timed out", outcome_unknown=True
                )
                last.__cause__ = error
            except ProviderError as error:
                last = error
            exhausted = attempts >= self._settings.provider_max_attempts
            if not last.retryable or exhausted:
                raise _Failed(
                    failed, last.reel_error_code, attempts=attempts, values=values
                )
            await self._clock.sleep(_RETRY_PAUSE_SECONDS)

    async def _reserve_capacity(
        self, deadline: float, attempts: int, values: ApprovedValuesSnapshot
    ) -> None:
        limited = _Failed(
            EvaluationStatus.FAILED_PROVIDER,
            ReelErrorCode.LOCAL_RATE_LIMIT,
            attempts=attempts,
            values=values,
        )
        if not self._daily_quota.try_consume():
            raise limited
        remaining = deadline - self._clock.monotonic()
        wait = self._limiter.try_reserve(max_wait_seconds=max(0.0, remaining))
        if wait is None:
            raise limited
        if wait > 0:
            await self._clock.sleep(wait)

    def _timings(self, job: JudgeJob, marks: _Marks) -> StageTimings:
        def ms(later: float, earlier: float) -> int | None:
            return int((later - earlier) * _MS) if later and earlier else None

        detect = self._span_ms(job.observed_end_at, job.created_at)
        ready_to_now = ms(self._clock.monotonic(), job.closed_monotonic) or 0
        return StageTimings(
            detect_ms=detect,
            audio_wait_ms=ms(job.ready_monotonic, job.closed_monotonic) or 0,
            queue_ms=ms(marks.started, job.ready_monotonic) or 0,
            context_ms=ms(marks.context_done, marks.started),
            inference_ms=ms(marks.inference_done, marks.context_done),
            validation_ms=ms(marks.validated, marks.inference_done),
            total_ms=None if detect is None else detect + ready_to_now,
        )

    @staticmethod
    def _span_ms(start: datetime | None, end: datetime) -> int | None:
        if start is None:
            return None
        return max(0, int((end - start).total_seconds() * _MS))
