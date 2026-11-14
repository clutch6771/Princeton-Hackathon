"""Bounded selection of representative frames for one open exposure.

Only a handful of frames are ever held per exposure: the first, the latest,
and at most four evenly thinned frames in between. When the reservoir fills,
every other intermediate frame is dropped and the stride doubles, so memory
stays constant however long the exposure runs. At finalization the Judge
normally uses two or three of them: first, the one nearest the middle, last.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

MAX_INTERMEDIATE_FRAMES: Final = 4
_LATE_INDEX: Final = -1


@dataclass(frozen=True, slots=True)
class KeptFrame:
    """One privacy-screened JPEG held in volatile memory."""

    event_id: UUID
    ts_ms: int
    jpeg: bytes
    width: int
    height: int

    @property
    def nbytes(self) -> int:
        """Bytes of raw media this frame holds."""
        return len(self.jpeg)


@dataclass(frozen=True, slots=True)
class FrameSelection:
    """Immutable reservoir of frames for one exposure."""

    first: KeptFrame
    last: KeptFrame | None = None
    intermediates: tuple[tuple[int, KeptFrame], ...] = ()
    stride: int = 1
    next_index: int = 1

    def add(self, frame: KeptFrame) -> tuple[FrameSelection, tuple[KeptFrame, ...]]:
        """Add the next in-order frame; return the new selection and drops."""
        if self.last is None:
            return (
                FrameSelection(self.first, frame, (), self.stride, 2),
                (),
            )
        previous_index = self.next_index - 1
        intermediates = self.intermediates
        dropped: list[KeptFrame] = []
        if previous_index % self.stride == 0:
            intermediates = (*intermediates, (previous_index, self.last))
        else:
            dropped.append(self.last)
        stride = self.stride
        if len(intermediates) > MAX_INTERMEDIATE_FRAMES:
            stride *= 2
            dropped.extend(f for i, f in intermediates if i % stride != 0)
            intermediates = tuple(p for p in intermediates if p[0] % stride == 0)
        selection = FrameSelection(
            self.first, frame, intermediates, stride, self.next_index + 1
        )
        return selection, tuple(dropped)

    def offer_late(
        self, frame: KeptFrame
    ) -> tuple[FrameSelection, tuple[KeptFrame, ...]]:
        """Offer an out-of-order frame as extra evidence if there is room."""
        if self.last is None or len(self.intermediates) >= MAX_INTERMEDIATE_FRAMES:
            return self, (frame,)
        intermediates = (*self.intermediates, (_LATE_INDEX, frame))
        selection = FrameSelection(
            self.first, self.last, intermediates, self.stride, self.next_index
        )
        return selection, ()

    def frames(self, max_frames: int) -> tuple[KeptFrame, ...]:
        """Up to ``max_frames`` representative frames in capture order."""
        if self.last is None or max_frames <= 1:
            return (self.first,)
        if max_frames == 2 or not self.intermediates:
            return (self.first, self.last)
        midpoint = (self.first.ts_ms + self.last.ts_ms) / 2
        middle = min(
            (frame for _, frame in self.intermediates),
            key=lambda frame: abs(frame.ts_ms - midpoint),
        )
        return (self.first, middle, self.last)

    def all_frames(self) -> tuple[KeptFrame, ...]:
        """Every frame currently held."""
        held = [self.first, *(frame for _, frame in self.intermediates)]
        if self.last is not None:
            held.append(self.last)
        return tuple(held)

    @property
    def held_count(self) -> int:
        """Number of frames currently held in memory."""
        return len(self.all_frames())

    @property
    def nbytes(self) -> int:
        """Bytes of raw media currently held."""
        return sum(frame.nbytes for frame in self.all_frames())
