"""Request dependencies: runtime, demo identity, rate limits, upload parsing.

Identity: the ``X-Demo-User-Id`` header must equal the one configured demo
identity. This is a demo mechanism, not production authentication. The
resolver is a small protocol so a real one can replace it.

Uploads are parsed entirely in memory. The size cap is checked against
``Content-Length`` before the body is read, and the streaming parser used here
never spools to a temporary file.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Final, Protocol

from fastapi import Depends, Request
from pydantic import BaseModel, ValidationError
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

from judge.bootstrap import JudgeRuntime
from judge.errors import (
    AuthenticationError,
    ForbiddenError,
    LengthRequiredError,
    PayloadTooLargeError,
    RateLimitedError,
    UnsupportedMediaError,
    ValidationFailure,
)

_METADATA_PART: Final = "metadata"
_MEDIA_PART: Final = "media"
_SECONDS_PER_MINUTE: Final = 60.0


class UserContextResolver(Protocol):
    """Resolves the owner of a request. Replaceable by real authentication."""

    def resolve(self, request: Request) -> str:
        """Return the owner id or raise an authentication error."""
        ...


class DemoHeaderResolver:
    """Accepts exactly one configured demo identity from a header."""

    def __init__(self, *, header: str, demo_user_id: str) -> None:
        self._header = header
        self._demo_user_id = demo_user_id

    def resolve(self, request: Request) -> str:
        presented = request.headers.get(self._header)
        if not presented:
            msg = f"missing {self._header} header"
            raise AuthenticationError(msg)
        if not hmac.compare_digest(presented.encode(), self._demo_user_id.encode()):
            msg = "this identity is not allowed"
            raise ForbiddenError(msg)
        return self._demo_user_id


def get_runtime(request: Request) -> JudgeRuntime:
    """The running Judge services."""
    runtime: JudgeRuntime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[JudgeRuntime, Depends(get_runtime)]


def get_owner(request: Request) -> str:
    """The validated demo identity."""
    resolver: UserContextResolver = request.app.state.user_resolver
    return resolver.resolve(request)


def _limited(per_minute: int) -> RateLimitedError:
    return RateLimitedError(
        "too many requests", retry_after_seconds=_SECONDS_PER_MINUTE / per_minute
    )


def ingest_owner(
    runtime: RuntimeDep, owner_id: Annotated[str, Depends(get_owner)]
) -> str:
    """Owner of an ingest request, within the ingest rate limit."""
    if not runtime.ingest_limiter.allow(owner_id):
        raise _limited(runtime.settings.ingest_requests_per_minute)
    return owner_id


def api_owner(runtime: RuntimeDep, owner_id: Annotated[str, Depends(get_owner)]) -> str:
    """Owner of an app request, within the API rate limit."""
    if not runtime.api_limiter.allow(owner_id):
        raise _limited(runtime.settings.api_requests_per_minute)
    return owner_id


IngestOwnerDep = Annotated[str, Depends(ingest_owner)]
ApiOwnerDep = Annotated[str, Depends(api_owner)]


def describe_validation(error: ValidationError) -> str:
    """Field names and rules only. Never echoes submitted values."""
    problems = (
        f"{'.'.join(str(part) for part in item['loc']) or 'body'}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    )
    return "; ".join(problems)


def parse_metadata[T: BaseModel](model: type[T], raw: bytes) -> T:
    """Validate the JSON metadata part of an upload."""
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        raise ValidationFailure(describe_validation(error)) from error


def _boundary(request: Request) -> bytes:
    content_type, options = parse_options_header(
        request.headers.get("content-type", "")
    )
    boundary = options.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        msg = "uploads must be multipart/form-data with 'metadata' and 'media' parts"
        raise UnsupportedMediaError(msg)
    return boundary


async def read_bounded_body(request: Request, max_bytes: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is None or not declared.isdigit():
        msg = "Content-Length is required for uploads"
        raise LengthRequiredError(msg)
    if int(declared) > max_bytes:
        msg = f"upload exceeds {max_bytes} bytes"
        raise PayloadTooLargeError(msg)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            msg = f"upload exceeds {max_bytes} bytes"
            raise PayloadTooLargeError(msg)
    return bytes(body)


def _split_parts(boundary: bytes, body: bytes) -> dict[str, bytes]:
    parts: dict[str, bytes] = {}
    header_name, header_value, disposition = bytearray(), bytearray(), b""
    data = bytearray()

    def on_header_end() -> None:
        nonlocal disposition
        if bytes(header_name).lower() == b"content-disposition":
            disposition = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def on_part_end() -> None:
        _, options = parse_options_header(disposition)
        name = options.get(b"name", b"").decode("utf-8", "replace")
        parts[name] = bytes(data)
        data.clear()

    parser = MultipartParser(
        boundary,
        {
            "on_header_field": lambda d, s, e: header_name.extend(d[s:e]),
            "on_header_value": lambda d, s, e: header_value.extend(d[s:e]),
            "on_header_end": on_header_end,
            "on_part_data": lambda d, s, e: data.extend(d[s:e]),
            "on_part_end": on_part_end,
        },
    )
    parser.write(body)
    parser.finalize()
    return parts


async def read_upload(request: Request, max_bytes: int) -> tuple[bytes, bytes]:
    """Return ``(metadata JSON, media bytes)`` of a capture upload."""
    boundary = _boundary(request)
    body = await read_bounded_body(request, max_bytes)
    try:
        parts = _split_parts(boundary, body)
    except MultipartParseError as error:
        msg = "upload is not valid multipart/form-data"
        raise ValidationFailure(msg) from error
    if _METADATA_PART not in parts or _MEDIA_PART not in parts:
        msg = "upload needs a 'metadata' part and a 'media' part"
        raise ValidationFailure(msg)
    return parts[_METADATA_PART], parts[_MEDIA_PART]
