"""HTTP adapter for the current app's completed-reel uploads."""

from fastapi import APIRouter, Request

from judge.api.deps import IngestOwnerDep, RuntimeDep, parse_metadata, read_bounded_body
from judge.contracts.ingest import IngestResponse
from judge.contracts.native import NativeReelUpload
from judge.ingest.native import ingest_native_reel as admit_native_reel

router = APIRouter()


@router.post("/agent1/ingest", response_model=IngestResponse, tags=["capture"])
async def ingest_native_reel(
    request: Request, runtime: RuntimeDep, owner_id: IngestOwnerDep
) -> IngestResponse:
    """Queue one device-segmented reel; retry identity is session ID + reel index."""
    raw = await read_bounded_body(request, runtime.settings.ingest_max_body_bytes)
    payload = parse_metadata(NativeReelUpload, raw)
    return await admit_native_reel(payload, runtime, owner_id)
