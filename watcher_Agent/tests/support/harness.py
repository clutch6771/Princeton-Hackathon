"""End-to-end test harness: the whole Judge runtime on fakes.

Everything is synthetic and deterministic: in-memory storage, a fake clock,
scripted providers, generated JPEG frames and AAC chunks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any
from uuid import UUID

from judge.bootstrap import JudgeRuntime, build_runtime
from judge.contracts.capture import AudioChunk, FrameObservation
from judge.contracts.notification import IngestResponse
from judge.contracts.record import ReelRecord
from judge.contracts.session import SessionEndRequest, SessionEndResponse
from judge.contracts.storage import EmbeddingProfile
from judge.contracts.understanding import ModelJudgment
from judge.contracts.values import ValuesReadResult
from judge.devfakes.clock import FakeClock
from judge.devfakes.memory_storage import MemoryStorage
from judge.devfakes.providers import (
    FakeEmbeddingProvider,
    FakeUnderstandingProvider,
    StaticValuesReader,
    fake_judgment,
)
from judge.persistence.interfaces import Storage
from judge.understanding.prompt import ImagePart, UnderstandingRequest
from tests.support.builders import SESSION_ID, T0_MS, audio_meta, frame_meta
from tests.support.media import encode_aac, make_frame_jpeg, sine
from tests.support.samples import snapshot
from tests.support.settings import DEMO_USER_ID, make_settings

SERVER_START = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
UPLOAD_LAG_SECONDS = 0.2
FRAME_INTERVAL_MS = 2000
TRAILING_SCENE = 9_999

Script = Callable[[UnderstandingRequest], ModelJudgment | Exception]


@lru_cache(maxsize=256)
def frame_jpeg(scene: int, overlay: int, variant: int = 0) -> bytes:
    """Cached synthetic frame."""
    return make_frame_jpeg(scene=scene, overlay=overlay, variant=variant)


@lru_cache(maxsize=16)
def aac_chunk(seconds: float) -> bytes:
    """Cached synthetic AAC chunk (a sine tone)."""
    return encode_aac(sine(seconds))


class Harness:
    """A running Judge runtime plus helpers to drive it like a device."""

    def __init__(
        self,
        runtime: JudgeRuntime,
        clock: FakeClock,
        understanding: FakeUnderstandingProvider,
        embedding: FakeEmbeddingProvider,
        values: StaticValuesReader,
    ) -> None:
        self.runtime = runtime
        self.clock = clock
        self.understanding = understanding
        self.embedding = embedding
        self.values = values
        self.owner = DEMO_USER_ID
        self.verdicts: dict[int, str] = {}
        self.script: Script | None = None
        self.scene_by_jpeg: dict[bytes, int] = {}
        self._seq = 0

    @classmethod
    async def create(
        cls,
        *,
        values: ValuesReadResult | None = None,
        storage: Storage | None = None,
        **settings_overrides: Any,
    ) -> Harness:
        settings = make_settings(**{"worker_concurrency": 1, **settings_overrides})
        clock = FakeClock(SERVER_START)
        understanding = FakeUnderstandingProvider()
        embedding = FakeEmbeddingProvider(EmbeddingProfile.from_settings(settings))
        reader = StaticValuesReader(values if values is not None else snapshot())
        runtime = build_runtime(
            settings,
            clock=clock,
            storage=storage or MemoryStorage(),
            understanding=understanding,
            embedding=embedding,
            values=reader,
        )
        harness = cls(runtime, clock, understanding, embedding, reader)
        understanding.script = harness._respond
        await runtime.start()
        return harness

    async def close(self) -> None:
        await self.runtime.stop()

    # --- scripted model -----------------------------------------------------

    def _respond(self, request: UnderstandingRequest) -> ModelJudgment | Exception:
        if self.script is not None:
            return self.script(request)
        return fake_judgment(
            request, self.verdicts.get(self.scene_of(request), "neutral")
        )

    def scene_of(self, request: UnderstandingRequest) -> int:
        """Which synthetic scene a model request is about."""
        first = next(p for p in request.parts if isinstance(p, ImagePart))
        return self.scene_by_jpeg[first.jpeg]

    def verdict(self, scene: int, verdict: str) -> None:
        """Script the verdict the fake model returns for a scene."""
        self.verdicts[scene] = verdict

    # --- device simulation --------------------------------------------------

    def _sync_clock(self, offset_ms: float) -> None:
        target = SERVER_START.timestamp() + offset_ms / 1000 + UPLOAD_LAG_SECONDS
        behind = target - self.clock.now().timestamp()
        if behind > 0:
            self.clock.advance(behind)

    def now_offset_ms(self) -> int:
        """Capture offset that corresponds to the current server time."""
        return int((self.clock.now() - SERVER_START).total_seconds() * 1000)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def frame(
        self,
        offset_ms: int,
        scene: int,
        *,
        overlay: int | None = None,
        session_id: UUID = SESSION_ID,
        acks: Iterable[UUID] = (),
        jpeg: bytes | None = None,
        **meta: Any,
    ) -> IngestResponse:
        """Upload one sampled frame captured ``offset_ms`` after the origin."""
        data = jpeg if jpeg is not None else frame_jpeg(scene, overlay or scene)
        self.scene_by_jpeg[data] = scene
        observation = FrameObservation.model_validate(
            frame_meta(
                ts_ms=T0_MS + offset_ms,
                seq=self._next_seq(),
                session_id=session_id,
                acknowledged_notification_ids=[str(a) for a in acks],
                **meta,
            )
        )
        self._sync_clock(offset_ms)
        return await self.runtime.ingest.ingest_frame(self.owner, observation, data)

    async def audio(
        self,
        offset_ms: int,
        *,
        seconds: float = 5.0,
        eligible: list[tuple[int, int]] | None = None,
        payload: bytes | None = None,
        session_id: UUID = SESSION_ID,
        acks: Iterable[UUID] = (),
        **meta: Any,
    ) -> IngestResponse:
        """Upload one audio chunk starting ``offset_ms`` after the origin."""
        start = T0_MS + offset_ms
        end = start + int(seconds * 1000)
        intervals = (
            None
            if eligible is None
            else [(T0_MS + first, T0_MS + last) for first, last in eligible]
        )
        chunk = AudioChunk.model_validate(
            audio_meta(
                start_ms=start,
                end_ms=end,
                seq=self._next_seq(),
                session_id=session_id,
                eligible=intervals,
                acknowledged_notification_ids=[str(a) for a in acks],
                **meta,
            )
        )
        data = payload if payload is not None else aac_chunk(seconds)
        self._sync_clock(offset_ms + seconds * 1000)
        return await self.runtime.ingest.ingest_audio(self.owner, chunk, data)

    async def play_reel(
        self,
        scene: int,
        start_ms: int,
        *,
        frames: int = 3,
        audio: bool = True,
        session_id: UUID = SESSION_ID,
    ) -> int:
        """Show one reel; returns the capture offset where the next begins."""
        for index in range(frames):
            await self.frame(
                start_ms + index * FRAME_INTERVAL_MS, scene, session_id=session_id
            )
        duration_ms = frames * FRAME_INTERVAL_MS
        if audio:
            await self.audio(
                start_ms, seconds=duration_ms / 1000, session_id=session_id
            )
        return start_ms + duration_ms

    async def play_feed(
        self,
        verdicts: list[str],
        *,
        start_ms: int = 0,
        first_scene: int = 1,
        audio: bool = False,
        session_id: UUID = SESSION_ID,
    ) -> int:
        """Show one reel per verdict, then a frame that closes the last one."""
        offset = start_ms
        for index, verdict in enumerate(verdicts):
            scene = first_scene + index
            self.verdict(scene, verdict)
            offset = await self.play_reel(
                scene, offset, audio=audio, session_id=session_id
            )
        await self.frame(offset, TRAILING_SCENE + first_scene, session_id=session_id)
        await self.settle()
        return offset + FRAME_INTERVAL_MS

    async def settle(self, wait_seconds: float = 7.0) -> None:
        """Let final-audio waits elapse and run all queued work."""
        self.clock.advance(wait_seconds)
        await self.runtime.maintenance.tick()
        await self.runtime.queue.join()
        await self.runtime.maintenance.tick()

    async def end_session(
        self, session_id: UUID = SESSION_ID, acks: Iterable[UUID] = ()
    ) -> SessionEndResponse:
        response = await self.runtime.lifecycle.end_session(
            self.owner,
            SessionEndRequest(
                session_id=session_id, acknowledged_notification_ids=tuple(acks)
            ),
        )
        await self.runtime.queue.join()
        return response

    async def reels(self, session_id: UUID | None = None) -> list[ReelRecord]:
        """Stored reels, oldest first."""
        page, _ = await self.runtime.timeline.list_reels(
            self.owner, session_id=session_id, limit=100, cursor=None
        )
        return list(reversed(page))
