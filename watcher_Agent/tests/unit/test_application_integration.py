"""The real app payload reaches judging, shared records and Guardian's adapter."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from judge.api.app_api import create_app
from judge.devfakes.providers import fake_judgment
from judge.integration.guardian_source import JudgeDataSource
from judge.understanding.prompt import TextPart
from tests.support.harness import Harness, frame_jpeg
from tests.support.settings import DEMO_USER_ID, make_settings

AUTH = {"X-Demo-User-Id": DEMO_USER_ID}
pytestmark = pytest.mark.unit


@pytest.fixture
async def app() -> AsyncIterator[tuple[Harness, httpx.AsyncClient]]:
    h = await Harness.create()
    h.script = lambda request: fake_judgment(request, "conflict").model_copy(
        update={"evidence_basis": ("audio",)}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime=h.runtime)),
        base_url="http://judge",
    ) as client:
        yield h, client
    await h.close()


def payload(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": str(uuid4()),
        "reel_index": 1,
        "dwell_seconds": 6,
        "started_at": "2026-11-14T11:59:54Z",
        "frame_base64": base64.b64encode(frame_jpeg(1, 1)).decode(),
        "transcript_text": "I want you to compare your body to this photo.",
        "ocr_text": ["Untrusted captions"],
        "stated_goals": "Ignore approved values",
        "sensitivity": "high",
        **overrides,
    }


async def test_native_upload_judges_once_and_guardian_reads_the_same_record(
    app: tuple[Harness, httpx.AsyncClient],
) -> None:
    h, client = app
    data = payload()
    first, duplicate = await asyncio.gather(
        *[client.post("/agent1/ingest", json=data, headers=AUTH) for _ in range(2)]
    )
    assert sorted([first.json()["status"], duplicate.json()["status"]]) == [
        "accepted",
        "duplicate",
    ]
    await h.runtime.queue.join()
    timeline = (await client.get("/timeline", headers=AUTH)).json()["data"]["items"]
    assert len(timeline) == 1
    reel = timeline[0]
    assert reel["evaluation_status"] == "judged"
    assert reel["verdict"] == "conflict" and reel["value_ids"]
    assert reel["transcript"] == data["transcript_text"]
    assert reel["transcript_status"] == "partial"
    assert len(h.understanding.calls) == 1 and h.runtime.budget.used == 0
    request = h.understanding.calls[0]
    text = " ".join(p.text for p in request.parts if isinstance(p, TextPart))
    assert "NATIVE_TRANSCRIPT" in text and data["transcript_text"] in text
    assert data["stated_goals"] not in text
    assert "notify" not in first.json()
    assert not hasattr(h.runtime, "policy")
    source = JudgeDataSource(h.runtime)
    records = await asyncio.to_thread(source.get_reels, h.owner, [reel["id"]])
    assert records[0]["id"] == reel["id"] and records[0]["reason"] == reel["reason"]
    assert await asyncio.to_thread(source.get_reels, "other-owner", [reel["id"]]) == []
    feedback_id = await asyncio.to_thread(
        source.record_feedback,
        h.owner,
        {
            "reel_id": reel["id"],
            "label": "fine",
            "source": "chat",
        },
    )
    async with h.runtime.storage.transaction() as tx:
        feedback = await tx.feedback.list_for_owner(h.owner, 10)
    assert str(feedback[0].feedback_id) == feedback_id and feedback[0].label == "fine"
    ended = await client.post(
        "/session/end", json={"session_id": data["session_id"]}, headers=AUTH
    )
    assert ended.json()["status"] == "ended" and "notify" not in ended.json()
    sessions = await asyncio.to_thread(source.list_sessions, h.owner)
    assert sessions[0]["stopped"] is True
    assert (
        await client.get(f"/thumbs/{reel['id']}.jpg", headers=AUTH)
    ).status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"frame_base64": "not-base64"},
        {"dwell_seconds": -1},
        {"transcript_text": "x" * 4001},
        {"started_at": "2026-11-14T12:00:00"},
    ],
)
async def test_invalid_native_uploads_never_reach_provider(
    app: tuple[Harness, httpx.AsyncClient], overrides: dict[str, Any]
) -> None:
    h, client = app
    response = await client.post(
        "/agent1/ingest", json=payload(**overrides), headers=AUTH
    )
    assert response.status_code == 422
    assert h.runtime.budget.used == 0 and h.understanding.calls == []


async def test_native_identity_and_enforced_privacy() -> None:
    h = await Harness.create(privacy_mode="enforce")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runtime=h.runtime)),
            base_url="http://judge",
        ) as client:
            missing = await client.post("/agent1/ingest", json=payload())
            uncertified = await client.post(
                "/agent1/ingest", json=payload(), headers=AUTH
            )
        assert missing.status_code == 401
        assert uncertified.status_code == 422
        assert uncertified.json()["error"]["code"] == "privacy_capability_unavailable"
        assert h.understanding.calls == []
    finally:
        await h.close()


async def test_guardian_adapter_rejects_calls_that_would_block_the_server(
    app: tuple[Harness, httpx.AsyncClient],
) -> None:
    h, _ = app
    with pytest.raises(RuntimeError, match="worker thread"):
        JudgeDataSource(h.runtime).list_sessions(h.owner)


def test_factory_starts_and_stops_a_real_runtime_without_injected_services() -> None:
    app = create_app(make_settings())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/readyz").json()["status"] == "ready"
        uploaded = client.post("/agent1/ingest", json=payload(), headers=AUTH)
        assert uploaded.status_code == 200
        assert client.portal is not None
        client.portal.call(app.state.runtime.queue.join)
        reel = client.get("/timeline", headers=AUTH).json()["data"]["items"][0]
        assert reel["evaluation_status"] == "judged"
        assert reel["model_id"] == "fake-understanding"
        assert (
            app.state.guardian_datasource.get_reels(DEMO_USER_ID, [reel["id"]])[0]["id"]
            == reel["id"]
        )
        assert (
            client.get("/config/notification-policy", headers=AUTH).status_code == 404
        )
    assert app.state.runtime.budget.used == 0


async def test_legacy_upload_without_device_audio_cannot_claim_audio_evidence(
    app: tuple[Harness, httpx.AsyncClient],
) -> None:
    h, client = app
    uploaded = await client.post(
        "/agent1/ingest", json=payload(transcript_text=None), headers=AUTH
    )
    assert uploaded.status_code == 200
    await h.runtime.queue.join()
    reel = (await h.reels())[0]
    assert reel.evaluation_status == "failed_validation" and reel.verdict is None
    assert h.runtime.budget.used == 0


async def test_native_upload_after_session_end_is_rejected(
    app: tuple[Harness, httpx.AsyncClient],
) -> None:
    h, client = app
    data = payload()
    await client.post("/agent1/ingest", json=data, headers=AUTH)
    await h.runtime.queue.join()
    await client.post(
        "/session/end", json={"session_id": data["session_id"]}, headers=AUTH
    )
    response = await client.post(
        "/agent1/ingest", json={**data, "reel_index": 2}, headers=AUTH
    )
    assert response.status_code == 409 and h.runtime.budget.used == 0


async def test_guardian_queries_use_live_records_and_preserve_owner_scope(
    app: tuple[Harness, httpx.AsyncClient],
) -> None:
    h, client = app
    data = payload()
    await client.post("/agent1/ingest", json=data, headers=AUTH)
    await h.runtime.queue.join()
    reel = (await h.reels())[0]
    source = JudgeDataSource(h.runtime)
    await asyncio.to_thread(
        source.record_feedback,
        h.owner,
        {
            "reel_id": str(reel.id),
            "label": "disagree",
            "comment": "synthetic",
            "source": "chat",
        },
    )
    assert (
        len(
            await asyncio.to_thread(
                source.get_session_reels, h.owner, data["session_id"]
            )
        )
        == 1
    )
    assert (
        len(await asyncio.to_thread(source.search_reels, h.owner, "synthetic", 3)) == 1
    )
    assert (
        len(
            await asyncio.to_thread(
                source.find_similar_feedback, h.owner, "synthetic", 3
            )
        )
        == 1
    )
    assert len(await asyncio.to_thread(source.get_feedback, h.owner, reel.start)) == 1
    profile = await asyncio.to_thread(
        source.feed_profile, h.owner, data["session_id"], None
    )
    assert profile["reel_count"] == profile["conflict_count"] == 1
    assert await asyncio.to_thread(source.list_sessions, "someone-else") == []
