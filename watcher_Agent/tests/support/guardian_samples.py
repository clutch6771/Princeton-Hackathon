"""Real Guardian approvals and labeled synthetic examples; no model recognition.

The reference judgments come from Guardian's invented demo data, not held-out
evaluation data. Replaying them verifies integration, never semantic accuracy.
"""

from __future__ import annotations

import io
import json
import math
import textwrap
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import av
import numpy as np
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from PIL import Image, ImageDraw, ImageFont

from guardian.datasource import SEED_DIR, FakeDataSource
from guardian.memory.values import render_values
from guardian.seed.install import install_initial_values
from judge.contracts.record import ReelRecord
from judge.contracts.understanding import ModelJudgment, Verdict
from judge.contracts.values import ApprovedValuesSnapshot
from judge.evaluation import DecisionReference
from judge.values.reader import StoredMemoryItem, build_snapshot
from tests.support.harness import Harness
from tests.support.media import encode_aac

OWNER = "guardian-judge-sample-user"
NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
SAMPLE_IDS = ("rl_013", "rl_014", "rl_015", "rl_016", "rl_017", "rl_019", "rl_020")


def approve_guardian_values(
    store: BaseStore | None = None,
) -> tuple[str, ApprovedValuesSnapshot]:
    """Execute Guardian's proposal/approval path and render its exact output."""
    store = store if store is not None else InMemoryStore()
    install_initial_values(store, FakeDataSource.from_seed(OWNER), OWNER, NOW)
    text = render_values(store, OWNER)
    snapshot = build_snapshot(
        OWNER,
        StoredMemoryItem(value={"content": text, "approval": {"status": "approved"}}),
        max_bytes=16_384,
        require_approval=True,
        now=NOW,
        source="guardian_proposal_approval_execution",
    )
    if not isinstance(snapshot, ApprovedValuesSnapshot):
        raise ValueError("Guardian did not produce usable approved values")
    return text, snapshot


def demo_samples() -> list[dict[str, Any]]:
    records = json.loads((SEED_DIR / "reels.json").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in records}
    return [by_id[key] for key in SAMPLE_IDS]


def reference_for(
    sample: dict[str, Any], values: ApprovedValuesSnapshot
) -> DecisionReference:
    return DecisionReference(
        sample_id=sample["id"],
        verdict=Verdict(sample["verdict"]),
        value_ids=frozenset(sample["value_ids"]),
        values_sha256=values.content_sha256,
    )


def replay_judgment(sample: dict[str, Any], *, audio: bool = False) -> ModelJudgment:
    """A recorded fixture response; deliberately does not recognize content."""
    return ModelJudgment(
        transcript=sample["transcript"] if audio else "",
        audio_observation=(
            ("speech" if sample["transcript"] else "non_speech_music")
            if audio
            else "no_audio_provided"
        ),
        on_screen_text=sample["on_screen_text"],
        description=sample["description"],
        **sample["features"],
        verdict=sample["verdict"],
        confidence=sample["confidence"],
        value_ids=tuple(sample["value_ids"]),
        reason=sample["reason"],
        evidence_basis=("frames", "audio") if audio else ("frames",),
    )


def sample_card(sample: dict[str, Any], *, audio_only: bool = False) -> Image.Image:
    """Synthetic storyboard evidence, with no expected verdict or value IDs."""
    image = Image.new("RGB", (288, 512), (20, 28, 45))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=17)
    draw.text((15, 15), "SYNTHETIC REEL", font=font, fill="white")
    if audio_only:
        draw.ellipse((96, 70, 190, 170), fill=(176, 184, 199))
        draw.rounded_rectangle((65, 180, 220, 370), radius=20, fill=(70, 100, 130))
    else:
        text = sample["on_screen_text"] or sample["description"]
        draw.multiline_text(
            (15, 95),
            "\n".join(textwrap.wrap(text, 23)),
            font=font,
            fill="white",
            spacing=8,
        )
        # The storyboard describes the invented scene, not its expected label.
        draw.multiline_text(
            (15, 250),
            "\n".join(textwrap.wrap(sample["description"], 25)),
            font=font,
            fill=(180, 205, 225),
            spacing=6,
        )
    return image


def jpeg_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=80)
    return output.getvalue()


def synthetic_video(image: Image.Image, *, seconds: int = 6) -> bytes:
    """Encode a short MP4, then tests decode/sample it like external footage."""
    output = io.BytesIO()
    with av.open(output, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=2)
        stream.width, stream.height = image.size
        stream.pix_fmt = "yuv420p"
        for _ in range(seconds * 2):
            for packet in stream.encode(av.VideoFrame.from_image(image)):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


def sample_video(video: bytes) -> list[tuple[int, bytes]]:
    """External-video test adapter: one JPEG every two seconds, never capture."""
    frames = []
    with av.open(io.BytesIO(video), mode="r") as container:
        for frame in container.decode(video=0):
            timestamp = round(float(frame.time or 0) * 1000)
            if timestamp % 2000 == 0:
                frames.append((timestamp, jpeg_bytes(frame.to_image())))
    return frames


def read_speech(path: str) -> np.ndarray:
    """Decode a generated speech file and resample it to the app audio format."""
    samples: list[np.ndarray] = []
    converter = av.AudioResampler(format="s16", layout="mono", rate=16_000)
    with av.open(path) as container:
        for frame in container.decode(audio=0):
            samples.extend(
                out.to_ndarray().reshape(-1) for out in converter.resample(frame)
            )
    samples.extend(out.to_ndarray().reshape(-1) for out in converter.resample(None))
    if not samples:
        raise ValueError("generated speech has no decoded audio samples")
    return np.concatenate(samples).astype(np.int16)


async def execute_stream_sample(
    harness: Harness,
    sample: dict[str, Any],
    pcm: np.ndarray,
) -> ReelRecord:
    """Decode external synthetic video, ingest its JPEG/AAC samples and judge."""
    session_id = uuid4()
    seconds = max(6, math.ceil(len(pcm) / 16_000) + 2)
    padded = np.pad(pcm, (0, seconds * 16_000 - len(pcm)))
    image = sample_card(sample, audio_only=sample["id"] in {"rl_015", "rl_019"})
    video = synthetic_video(image, seconds=seconds)
    frames = sample_video(video)
    events: list[tuple[int, str, Any]] = [
        (timestamp, "frame", data) for timestamp, data in frames
    ]
    events += [
        (min(start + 5, seconds) * 1000, "audio", start)
        for start in range(0, seconds, 5)
    ]
    origin = harness.now_offset_ms() + 1000
    for timestamp, kind, payload in sorted(events, key=lambda event: event[0]):
        if kind == "frame":
            await harness.frame(
                origin + timestamp, 1, session_id=session_id, jpeg=payload
            )
        else:
            start = payload
            chunk = padded[start * 16_000 : min(start + 5, seconds) * 16_000]
            await harness.audio(
                origin + start * 1000,
                seconds=len(chunk) / 16_000,
                session_id=session_id,
                payload=encode_aac(chunk),
            )
    await harness.end_session(session_id)
    records = await harness.reels(session_id)
    if len(records) != 1:
        raise ValueError(f"sample generated {len(records)} exposures, expected one")
    return records[0]
