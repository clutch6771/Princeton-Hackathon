"""Judge-owned HTTP routes.

Media ingest returns acknowledgements; app endpoints use the standard envelope.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from pydantic import BaseModel

from guardian_router import review_sessions
from judge.api.deps import (
    ApiOwnerDep,
    IngestOwnerDep,
    RuntimeDep,
    parse_metadata,
    read_upload,
)
from judge.contracts.capture import AudioChunk, FrameObservation
from judge.contracts.feedback import FeedbackRecord, FeedbackRequest
from judge.contracts.ingest import IngestResponse
from judge.contracts.record import ReelRecord
from judge.contracts.session import SessionEndRequest, SessionEndResponse
from judge.errors import NotFoundError

router = APIRouter()

MAX_TIMELINE_LIMIT = 100
DEFAULT_TIMELINE_LIMIT = 50


class Envelope[T](BaseModel):
    """Consistent envelope for app-facing (non-ingest) endpoints."""

    success: bool = True
    data: T | None = None
    error: dict[str, str] | None = None
    meta: dict[str, Any] | None = None


class TimelinePage(BaseModel):
    """One page of stored reels, newest first."""

    items: list[ReelRecord]


class Deleted(BaseModel):
    """Result of a deletion hook."""

    deleted: bool = True


def _upload_body(schema_file: str, media_type: str, note: str) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["metadata", "media"],
                        "properties": {
                            "metadata": {
                                "type": "string",
                                "contentMediaType": "application/json",
                                "description": (
                                    "JSON document matching "
                                    f"contracts/v1/{schema_file}."
                                ),
                            },
                            "media": {
                                "type": "string",
                                "format": "binary",
                                "contentMediaType": media_type,
                                "description": note,
                            },
                        },
                    }
                }
            },
        }
    }


def _reel_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as error:
        msg = "reel not found"
        raise NotFoundError(msg) from error


@router.post(
    "/ingest/frame",
    response_model=IngestResponse,
    tags=["capture"],
    summary="Upload one sampled JPEG frame",
    openapi_extra=_upload_body(
        "frame_observation.schema.json",
        "image/jpeg",
        "One JPEG, longest edge 512 px. Held in volatile memory only.",
    ),
)
async def ingest_frame(
    request: Request, runtime: RuntimeDep, owner_id: IngestOwnerDep
) -> IngestResponse:
    """Acknowledge promptly; judgment is asynchronous."""
    raw, media = await read_upload(request, runtime.settings.ingest_max_body_bytes)
    meta = parse_metadata(FrameObservation, raw)
    return await runtime.ingest.ingest_frame(owner_id, meta, media)


@router.post(
    "/ingest/audio",
    response_model=IngestResponse,
    tags=["capture"],
    summary="Upload one app-audio chunk",
    openapi_extra=_upload_body(
        "audio_chunk.schema.json",
        "audio/aac",
        "16 kHz mono AAC (ADTS or MP4), nominally 5 s. Volatile memory only.",
    ),
)
async def ingest_audio(
    request: Request, runtime: RuntimeDep, owner_id: IngestOwnerDep
) -> IngestResponse:
    """Acknowledge promptly; judgment is asynchronous."""
    raw, media = await read_upload(request, runtime.settings.ingest_max_body_bytes)
    meta = parse_metadata(AudioChunk, raw)
    return await runtime.ingest.ingest_audio(owner_id, meta, media)


@router.post("/session/end", response_model=SessionEndResponse, tags=["capture"])
async def end_session(
    body: SessionEndRequest,
    runtime: RuntimeDep,
    owner_id: IngestOwnerDep,
    background_tasks: BackgroundTasks,
) -> SessionEndResponse:
    """Idempotently end a session and flush its bounded tail."""
    ended = await runtime.lifecycle.end_session(owner_id, body)
    # Guardian's Reflector reviews the finished session; it never blocks the reply.
    background_tasks.add_task(review_sessions, owner_id)
    return ended


@router.get("/timeline", response_model=Envelope[TimelinePage], tags=["app"])
async def timeline(
    runtime: RuntimeDep,
    owner_id: ApiOwnerDep,
    session_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_TIMELINE_LIMIT)] = DEFAULT_TIMELINE_LIMIT,
    cursor: str | None = None,
) -> Envelope[TimelinePage]:
    """Owner-scoped, paginated reel records, newest first."""
    items, next_cursor = await runtime.timeline.list_reels(
        owner_id, session_id=session_id, limit=limit, cursor=cursor
    )
    return Envelope(
        data=TimelinePage(items=items),
        meta={"next_cursor": next_cursor, "limit": limit},
    )


@router.get(
    "/thumbs/{reel_id}.jpg",
    tags=["app"],
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def thumbnail(
    reel_id: str, runtime: RuntimeDep, owner_id: ApiOwnerDep
) -> Response:
    """The reel's single retained, privacy-screened thumbnail."""
    record = await runtime.timeline.thumbnail(owner_id, _reel_id(reel_id))
    return Response(
        content=record.jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post(
    "/feedback",
    response_model=Envelope[FeedbackRecord],
    status_code=201,
    tags=["app"],
)
async def feedback(
    body: FeedbackRequest, runtime: RuntimeDep, owner_id: ApiOwnerDep
) -> Envelope[FeedbackRecord]:
    """Store explicit feedback. No scheduler, conversation or values edit."""
    return Envelope(data=await runtime.feedback.submit(owner_id, body))


@router.delete("/reels/{reel_id}", response_model=Envelope[Deleted], tags=["data"])
async def delete_reel(
    reel_id: UUID, runtime: RuntimeDep, owner_id: ApiOwnerDep
) -> Envelope[Deleted]:
    """Delete one reel with its thumbnail, vectors and feedback."""
    await runtime.deletion.delete_reel(owner_id, reel_id)
    return Envelope(data=Deleted())


@router.delete(
    "/sessions/{session_id}", response_model=Envelope[Deleted], tags=["data"]
)
async def delete_session(
    session_id: UUID, runtime: RuntimeDep, owner_id: ApiOwnerDep
) -> Envelope[Deleted]:
    """Delete one session and everything recorded for it."""
    await runtime.deletion.delete_session(owner_id, session_id)
    return Envelope(data=Deleted())


@router.delete("/data", response_model=Envelope[Deleted], tags=["data"])
async def delete_owner_data(
    runtime: RuntimeDep, owner_id: ApiOwnerDep
) -> Envelope[Deleted]:
    """Delete everything the Judge stores for the caller."""
    await runtime.deletion.delete_owner_data(owner_id)
    return Envelope(data=Deleted())


@router.get("/health", tags=["ops"], include_in_schema=False)
@router.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    """Liveness."""
    return {"status": "ok"}


@router.get("/readyz", tags=["ops"])
async def readyz(runtime: RuntimeDep, response: Response) -> dict[str, Any]:
    """Readiness and capability report. Contains no secrets."""
    storage = await runtime.storage.health()
    if not storage.ok:
        response.status_code = 503
    return {
        "status": "ready" if storage.ok else "unavailable",
        "storage": storage.model_dump(mode="json"),
        "privacy": runtime.gate.describe(),
        "provider": {
            "mode": runtime.settings.provider_mode.value,
            "model_id": runtime.understanding.model_id,
            "embedding_profile": runtime.settings.embedding_profile_id,
            "indexing_enabled": runtime.settings.embedding_indexing_enabled,
        },
        "queue_depth": runtime.queue.depth,
        "media_bytes_held": runtime.budget.used,
        "single_process_required": True,
    }
