"""Builders for synthetic wire payloads (metadata parts of capture uploads)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

# Synthetic capture clock origin (epoch milliseconds on the "device").
T0_MS = 1_800_000_000_000
SESSION_ID = UUID("11111111-1111-4111-8111-111111111111")
SOURCE_ID = "ios-broadcast-synthetic"


def privacy(
    *,
    eligibility: str = "eligible",
    capability: str = "native_gate",
    surface: str = "instagram_reels",
) -> dict[str, Any]:
    """Privacy provenance as a native eligibility gate would report it."""
    return {
        "gate_id": "feed-eligibility-gate",
        "gate_version": "1.0.0",
        "capability": capability,
        "eligibility": eligibility,
        "surface": surface,
        "method": "on_device_classifier",
    }


def frame_meta(
    *,
    ts_ms: int = T0_MS,
    seq: int = 0,
    session_id: UUID = SESSION_ID,
    event_id: UUID | None = None,
    width: int = 288,
    height: int = 512,
    **overrides: Any,
) -> dict[str, Any]:
    """Metadata for one sampled JPEG frame."""
    meta: dict[str, Any] = {
        "contract_version": "1",
        "event_id": str(event_id or uuid4()),
        "session_id": str(session_id),
        "source_id": SOURCE_ID,
        "source_seq": seq,
        "capture_ts_ms": ts_ms,
        "mime_type": "image/jpeg",
        "width": width,
        "height": height,
        "privacy": privacy(),
    }
    return {**meta, **overrides}


def audio_meta(
    *,
    start_ms: int = T0_MS,
    end_ms: int | None = None,
    seq: int = 0,
    session_id: UUID = SESSION_ID,
    event_id: UUID | None = None,
    eligible: list[tuple[int, int]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Metadata for one app-audio AAC chunk (nominally five seconds)."""
    end = end_ms if end_ms is not None else start_ms + 5000
    intervals = eligible if eligible is not None else [(start_ms, end)]
    meta: dict[str, Any] = {
        "contract_version": "1",
        "event_id": str(event_id or uuid4()),
        "session_id": str(session_id),
        "source_id": SOURCE_ID,
        "source_seq": seq,
        "capture_start_ms": start_ms,
        "capture_end_ms": end,
        "codec": "aac",
        "container": "adts",
        "sample_rate_hz": 16000,
        "channels": 1,
        "priming_samples": 1024,
        "capture_state": "available",
        "eligible_intervals": [
            {"start_ms": start, "end_ms": stop} for start, stop in intervals
        ],
        "privacy": privacy(),
    }
    return {**meta, **overrides}
