"""Audio preparation: turn assembled PCM into the clip given to the model."""

from __future__ import annotations

from dataclasses import dataclass

from judge.audio.assembler import AssembledAudio
from judge.audio.wav import pcm_to_wav
from judge.contracts.candidate import AudioStatus


@dataclass(frozen=True, slots=True)
class PreparedAudio:
    """The clip for one exposure, or the evidence for why there is none."""

    wav: bytes | None
    status: AudioStatus
    window_ms: int
    kept_ms: int
    uncertified_ms: int
    protected_ms: int
    corrupt_ms: int
    missing_ms: int

    @property
    def supplied(self) -> bool:
        """Whether an audio clip is given to the model."""
        return self.wav is not None

    @property
    def nbytes(self) -> int:
        """Bytes of raw media held by this clip."""
        return len(self.wav) if self.wav is not None else 0

    @property
    def coverage_ratio(self) -> float:
        """Kept audio as a fraction of the attributable window."""
        if self.window_ms <= 0:
            return 0.0
        return min(1.0, self.kept_ms / self.window_ms)


def prepare_audio(
    assembled: AssembledAudio, *, sample_rate: int, min_ms_for_model: int
) -> PreparedAudio:
    """Build the in-memory WAV clip, or withhold one that is too short."""
    enough = assembled.kept_ms > 0 and assembled.kept_ms >= min_ms_for_model
    wav = pcm_to_wav(assembled.samples, sample_rate) if enough else None
    return PreparedAudio(
        wav=wav,
        status=assembled.status,
        window_ms=assembled.window_ms,
        kept_ms=assembled.kept_ms,
        uncertified_ms=assembled.uncertified_ms,
        protected_ms=assembled.protected_ms,
        corrupt_ms=assembled.corrupt_ms,
        missing_ms=assembled.missing_ms,
    )
