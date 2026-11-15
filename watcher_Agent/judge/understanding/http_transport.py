"""Shared sanitized HTTP/JSON handling for compatible model services."""

from typing import Any

import httpx

from judge.understanding.gemini import classify_provider_error


async def request_json(client: httpx.AsyncClient, path: str, **kwargs: Any) -> Any:
    """Make one request; never log provider error bodies or retry internally."""
    try:
        response = await client.post(path, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        error.code = error.response.status_code  # type: ignore[attr-defined]
        raise classify_provider_error(error) from error
    except (httpx.HTTPError, ValueError) as error:
        raise classify_provider_error(error) from error
