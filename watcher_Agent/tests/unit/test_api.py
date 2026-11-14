"""HTTP API: contracts, identity, limits, error envelope, OpenAPI."""

from __future__ import annotations

import json
import tempfile
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from judge.api.app_api import create_app
from tests.support.builders import SESSION_ID, T0_MS, audio_meta, frame_meta
from tests.support.harness import Harness, aac_chunk, frame_jpeg
from tests.support.settings import DEMO_USER_ID

pytestmark = pytest.mark.unit

AUTH = {"X-Demo-User-Id": DEMO_USER_ID}
JSON_AUTH = {"X-Demo-User-Id": DEMO_USER_ID, "Content-Type": "application/json"}


@pytest.fixture
async def h() -> AsyncIterator[Harness]:
    harness = await Harness.create()
    yield harness
    await harness.close()


@pytest.fixture
async def client(h: Harness) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(runtime=h.runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://judge") as c:
        yield c


def _frame_files(scene: int = 1, **meta: Any) -> dict[str, Any]:
    return {
        "metadata": (None, json.dumps(frame_meta(**meta)), "application/json"),
        "media": ("frame.jpg", frame_jpeg(scene, scene), "image/jpeg"),
    }


def _audio_files(**meta: Any) -> dict[str, Any]:
    return {
        "metadata": (None, json.dumps(audio_meta(**meta)), "application/json"),
        "media": ("chunk.aac", aac_chunk(5.0), "audio/aac"),
    }


async def _post_frame(
    client: httpx.AsyncClient, scene: int = 1, **meta: Any
) -> httpx.Response:
    return await client.post(
        "/ingest/frame", files=_frame_files(scene, **meta), headers=AUTH
    )


async def test_frame_ingest_acknowledges_with_notify_at_the_response_root(
    client: httpx.AsyncClient,
) -> None:
    response = await _post_frame(client)

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "accepted"
    assert body["session_id"] == str(SESSION_ID)
    assert "notify" in body and body["notify"] is None
    assert "data" not in body and "success" not in body


async def test_audio_ingest_acknowledges_with_notify_at_the_response_root(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/ingest/audio", files=_audio_files(), headers=AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["notify"] is None


async def test_uploads_never_touch_a_temporary_file(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("raw media must not be spooled to disk")

    for name in ("SpooledTemporaryFile", "TemporaryFile", "NamedTemporaryFile"):
        monkeypatch.setattr(tempfile, name, forbidden)
    monkeypatch.setattr(tempfile, "mkstemp", forbidden)

    frame = await _post_frame(client)
    audio = await client.post("/ingest/audio", files=_audio_files(), headers=AUTH)

    assert (frame.status_code, audio.status_code) == (200, 200)


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({}, 401, "unauthenticated"),
        ({"X-Demo-User-Id": "someone-else"}, 403, "forbidden"),
    ],
)
async def test_only_the_configured_demo_identity_is_accepted(
    client: httpx.AsyncClient, headers: dict[str, str], status: int, code: str
) -> None:
    ingest = await client.post("/ingest/frame", files=_frame_files(), headers=headers)
    timeline = await client.get("/timeline", headers=headers)

    for response in (ingest, timeline):
        assert response.status_code == status
        assert response.json() == {
            "success": False,
            "data": None,
            "error": {"code": code, "message": response.json()["error"]["message"]},
        }


async def test_oversized_upload_is_rejected_before_it_is_read(
    client: httpx.AsyncClient,
) -> None:
    files = _frame_files()
    files["media"] = ("frame.jpg", b"\xff\xd8\xff" + b"0" * 500_000, "image/jpeg")

    response = await client.post("/ingest/frame", files=files, headers=AUTH)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_invalid_metadata_is_reported_without_echoing_the_payload(
    client: httpx.AsyncClient,
) -> None:
    secret_marker = "do-not-echo-this-value"
    response = await client.post(
        "/ingest/frame",
        files=_frame_files(source_id=f"{secret_marker} with spaces"),
        headers=AUTH,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "source_id" in response.json()["error"]["message"]
    assert secret_marker not in response.text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": b"{}", "headers": JSON_AUTH},
        {"files": {"media": ("f.jpg", b"\xff\xd8\xff", "image/jpeg")}, "headers": AUTH},
        {
            "files": {
                "metadata": (None, "not json", "application/json"),
                "media": ("f.jpg", b"\xff\xd8\xff", "image/jpeg"),
            },
            "headers": AUTH,
        },
    ],
)
async def test_malformed_uploads_get_a_client_error(
    client: httpx.AsyncClient, kwargs: dict[str, Any]
) -> None:
    response = await client.post("/ingest/frame", **kwargs)

    assert response.status_code in {415, 422}
    assert response.json()["success"] is False


async def test_session_end_is_idempotent_and_unknown_sessions_are_404(
    client: httpx.AsyncClient,
) -> None:
    await _post_frame(client)
    payload = {"contract_version": "1", "session_id": str(SESSION_ID)}

    first = await client.post("/session/end", json=payload, headers=AUTH)
    second = await client.post("/session/end", json=payload, headers=AUTH)
    missing = await client.post(
        "/session/end",
        json={"contract_version": "1", "session_id": str(uuid4())},
        headers=AUTH,
    )
    reuse = await _post_frame(client, ts_ms=T0_MS + 2000)

    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "ended" and "notify" in second.json()
    assert missing.status_code == 404
    assert reuse.status_code == 409
    assert reuse.json()["error"]["code"] == "session_ended"


async def test_timeline_thumbnail_feedback_and_deletion_round_trip(
    h: Harness, client: httpx.AsyncClient
) -> None:
    await h.play_feed(["conflict", "neutral"])

    timeline = await client.get("/timeline", params={"limit": 1}, headers=AUTH)
    body = timeline.json()
    item = body["data"]["items"][0]
    more = await client.get(
        "/timeline",
        params={"limit": 5, "cursor": body["meta"]["next_cursor"]},
        headers=AUTH,
    )
    thumb = await client.get(item["thumbnail_url"], headers=AUTH)
    feedback = await client.post(
        "/feedback",
        json={"contract_version": "1", "reel_id": item["id"], "label": "agree"},
        headers=AUTH,
    )
    deleted = await client.delete(f"/reels/{item['id']}", headers=AUTH)
    gone = await client.get(item["thumbnail_url"], headers=AUTH)

    assert timeline.status_code == 200 and body["success"] is True
    assert item["verdict"] in {"conflict", "neutral"} and item["reason"]
    assert len(more.json()["data"]["items"]) == 1
    assert more.json()["meta"]["next_cursor"] is None
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    assert thumb.content[:3] == b"\xff\xd8\xff"
    assert feedback.status_code == 201
    assert feedback.json()["data"]["label"] == "agree"
    assert deleted.status_code == 200 and gone.status_code == 404


async def test_feedback_validation_and_unknown_reel(client: httpx.AsyncClient) -> None:
    bad_label = await client.post(
        "/feedback",
        json={"contract_version": "1", "reel_id": str(uuid4()), "label": "love"},
        headers=AUTH,
    )
    unknown = await client.post(
        "/feedback",
        json={"contract_version": "1", "reel_id": str(uuid4()), "label": "agree"},
        headers=AUTH,
    )

    assert bad_label.status_code == 422
    assert unknown.status_code == 404


async def test_policy_can_be_read_and_updated_with_validation(
    client: httpx.AsyncClient,
) -> None:
    default = await client.get("/config/notification-policy", headers=AUTH)
    updated = await client.put(
        "/config/notification-policy",
        json={"batch_size": 10, "minimum_conflicts": 2, "cooldown_seconds": 300},
        headers=AUTH,
    )
    invalid = await client.put(
        "/config/notification-policy",
        json={"batch_size": 5, "minimum_conflicts": 6, "cooldown_seconds": 300},
        headers=AUTH,
    )

    assert default.json()["data"] == {
        "batch_size": 15,
        "minimum_conflicts": 3,
        "cooldown_seconds": 600,
        "version": 0,
        "updated_at": None,
    }
    assert updated.json()["data"]["version"] == 1
    assert invalid.status_code == 422


async def test_owner_data_can_be_deleted(h: Harness, client: httpx.AsyncClient) -> None:
    await h.play_feed(["neutral"])

    deleted = await client.delete("/data", headers=AUTH)
    timeline = await client.get("/timeline", headers=AUTH)

    assert deleted.status_code == 200
    assert timeline.json()["data"]["items"] == []


async def test_health_and_readiness_report_capabilities_without_secrets(
    client: httpx.AsyncClient,
) -> None:
    health = await client.get("/healthz")
    ready = await client.get("/readyz")

    assert health.status_code == 200 and health.json() == {"status": "ok"}
    body = ready.json()
    assert ready.status_code == 200
    assert body["storage"]["ok"] is True
    assert body["privacy"] == {"mode": "dev_permissive", "native_gate_required": False}
    assert body["provider"]["model_id"] == "fake-understanding"
    assert body["single_process_required"] is True
    assert "key" not in ready.text.lower() and "password" not in ready.text.lower()


async def test_ingest_is_rate_limited_per_owner() -> None:
    h = await Harness.create(ingest_requests_per_minute=1)
    try:
        transport = httpx.ASGITransport(app=create_app(runtime=h.runtime))
        async with httpx.AsyncClient(transport=transport, base_url="http://j") as c:
            first = await _post_frame(c)
            second = await _post_frame(c, ts_ms=T0_MS + 2000)

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "rate_limited"
        assert "retry-after" in second.headers
    finally:
        await h.close()


async def test_overload_is_a_503_with_retry_after() -> None:
    h = await Harness.create(session_media_budget_bytes=2000)
    try:
        transport = httpx.ASGITransport(app=create_app(runtime=h.runtime))
        async with httpx.AsyncClient(transport=transport, base_url="http://j") as c:
            response = await _post_frame(c)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "overloaded"
        assert "retry-after" in response.headers
    finally:
        await h.close()


async def test_openapi_documents_the_judge_owned_contracts(
    client: httpx.AsyncClient,
) -> None:
    spec = (await client.get("/openapi.json")).json()

    assert {
        "/ingest/frame",
        "/ingest/audio",
        "/session/end",
        "/timeline",
        "/thumbs/{reel_id}.jpg",
        "/feedback",
        "/config/notification-policy",
    } <= set(spec["paths"])
    assert "/share" not in spec["paths"] and "/chat" not in spec["paths"]
    frame_body = spec["paths"]["/ingest/frame"]["post"]["requestBody"]["content"]
    assert "multipart/form-data" in frame_body
    assert "notify" in spec["components"]["schemas"]["IngestResponse"]["properties"]
    assert "not production authentication" in spec["info"]["description"]
