"""Typed errors and sanitized error codes for the Judge backend.

Two families:

* ``JudgeError`` subclasses are expected, client-safe failures. Each maps to an
  HTTP status and a stable code. Messages never contain media, transcripts,
  values text or secrets.
* ``ReelErrorCode`` values are operational codes stored on a reel record when
  an observation could not be judged.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar


class ErrorCode(StrEnum):
    """Stable error codes returned to API clients."""

    VALIDATION = "validation_error"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    LENGTH_REQUIRED = "length_required"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_MEDIA = "unsupported_media"
    PRIVACY_CAPABILITY_UNAVAILABLE = "privacy_capability_unavailable"
    SESSION_ENDED = "session_ended"
    SOURCE_CONFLICT = "source_conflict"
    CLOCK_SKEW = "clock_skew"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    INTERNAL = "internal_error"


class JudgeError(Exception):
    """Base class for expected failures that are safe to report to a client."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL
    http_status: ClassVar[int] = 500

    def __init__(
        self, message: str, *, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds


class ValidationFailure(JudgeError):
    """The request payload or metadata is invalid."""

    code = ErrorCode.VALIDATION
    http_status = 422


class AuthenticationError(JudgeError):
    """The request carries no usable identity."""

    code = ErrorCode.UNAUTHENTICATED
    http_status = 401


class ForbiddenError(JudgeError):
    """The presented identity is not the configured demo identity."""

    code = ErrorCode.FORBIDDEN
    http_status = 403


class NotFoundError(JudgeError):
    """The resource does not exist for this owner."""

    code = ErrorCode.NOT_FOUND
    http_status = 404


class LengthRequiredError(JudgeError):
    """Uploads must declare Content-Length so the cap is enforced up front."""

    code = ErrorCode.LENGTH_REQUIRED
    http_status = 411


class PayloadTooLargeError(JudgeError):
    """The upload exceeds a configured size limit."""

    code = ErrorCode.PAYLOAD_TOO_LARGE
    http_status = 413


class UnsupportedMediaError(JudgeError):
    """The media type, codec or container is not accepted."""

    code = ErrorCode.UNSUPPORTED_MEDIA
    http_status = 415


class PrivacyCapabilityUnavailableError(JudgeError):
    """No trusted eligibility gate certified this upload."""

    code = ErrorCode.PRIVACY_CAPABILITY_UNAVAILABLE
    http_status = 422


class SessionEndedError(JudgeError):
    """The session was already ended; start a new session id."""

    code = ErrorCode.SESSION_ENDED
    http_status = 409


class SourceConflictError(JudgeError):
    """A second capture source tried to use a bound session."""

    code = ErrorCode.SOURCE_CONFLICT
    http_status = 409


class ClockSkewError(JudgeError):
    """A capture timestamp is implausible for this session's clock mapping."""

    code = ErrorCode.CLOCK_SKEW
    http_status = 422


class RateLimitedError(JudgeError):
    """Too many requests from this owner."""

    code = ErrorCode.RATE_LIMITED
    http_status = 429


class OverloadedError(JudgeError):
    """The bounded in-memory media budget or queue is full; back off."""

    code = ErrorCode.OVERLOADED
    http_status = 503


class StorageUnavailableError(JudgeError):
    """The database could not be reached."""

    code = ErrorCode.STORAGE_UNAVAILABLE
    http_status = 503


class ConfigurationError(Exception):
    """Startup configuration is missing or unsafe. Never names secret values."""


class ReelErrorCode(StrEnum):
    """Sanitized operational codes stored on reels that were not judged."""

    # Evidence
    NO_USABLE_FRAMES = "no_usable_frames"
    TOO_FEW_FRAMES = "too_few_frames"
    # Approved values
    VALUES_MISSING = "values_missing"
    VALUES_EMPTY = "values_empty"
    VALUES_INVALID = "values_invalid"
    VALUES_HASH_MISMATCH = "values_hash_mismatch"
    VALUES_UNAPPROVED = "values_unapproved"
    VALUES_DELETED = "values_deleted"
    VALUES_OVER_CAP = "values_over_cap"
    VALUES_STORE_ERROR = "values_store_error"
    # Provider
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_QUOTA = "provider_quota"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    PROVIDER_SCHEMA = "provider_schema"
    LOCAL_RATE_LIMIT = "local_rate_limit"
    INPUT_TOKEN_BUDGET = "input_token_budget"  # noqa: S105  # nosec B105
    # Output validation
    VALUE_IDS_UNKNOWN = "value_ids_unknown"
    VALUE_IDS_MISSING = "value_ids_missing"
    REASON_MISSING = "reason_missing"
    DESCRIPTION_MISSING = "description_missing"
    CONFIDENCE_OUT_OF_RANGE = "confidence_out_of_range"
    TRANSCRIPT_WITHOUT_AUDIO = "transcript_without_audio"
    TRANSCRIPT_INCONSISTENT = "transcript_inconsistent"
    TRANSCRIPT_TOO_LONG = "transcript_too_long"
    AUDIO_OBSERVATION_INCONSISTENT = "audio_observation_inconsistent"
    SPEECH_EVIDENCE_UNSUPPORTED = "speech_evidence_unsupported"
    EVIDENCE_BASIS_EMPTY = "evidence_basis_empty"
    # Operations
    JOB_DEADLINE = "job_deadline"
    QUEUE_FULL = "queue_full"
    MEDIA_BUDGET = "media_budget"
    RESTART = "restart"
    INTERNAL = "internal_error"


class ProviderErrorKind(StrEnum):
    """How a provider call failed, as far as the adapter can tell."""

    TIMEOUT = "timeout"
    QUOTA = "quota"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    SCHEMA = "schema"


_RETRYABLE_KINDS = frozenset(
    {ProviderErrorKind.TIMEOUT, ProviderErrorKind.QUOTA, ProviderErrorKind.TRANSIENT}
)

_REEL_CODE_BY_KIND: dict[ProviderErrorKind, ReelErrorCode] = {
    ProviderErrorKind.TIMEOUT: ReelErrorCode.PROVIDER_TIMEOUT,
    ProviderErrorKind.QUOTA: ReelErrorCode.PROVIDER_QUOTA,
    ProviderErrorKind.TRANSIENT: ReelErrorCode.PROVIDER_UNAVAILABLE,
    ProviderErrorKind.PERMANENT: ReelErrorCode.PROVIDER_REJECTED,
    ProviderErrorKind.SCHEMA: ReelErrorCode.PROVIDER_SCHEMA,
}


class ProviderError(Exception):
    """A model or embedding provider call failed.

    ``outcome_unknown`` is true when the request may have reached the provider
    (for example a timeout), so remote billing cannot be ruled out.
    """

    def __init__(
        self,
        kind: ProviderErrorKind,
        message: str,
        *,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.outcome_unknown = outcome_unknown

    @property
    def retryable(self) -> bool:
        """Whether one bounded retry may succeed."""
        return self.kind in _RETRYABLE_KINDS

    @property
    def reel_error_code(self) -> ReelErrorCode:
        """The sanitized code stored on the reel record."""
        return _REEL_CODE_BY_KIND[self.kind]


class OutputValidationError(Exception):
    """The model's structured output failed deterministic validation."""

    def __init__(self, code: ReelErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class AudioDecodeError(Exception):
    """An audio chunk could not be decoded by the codec adapter."""


class EmbeddingDimensionError(Exception):
    """An embedding does not match the pinned dimension of its profile."""
