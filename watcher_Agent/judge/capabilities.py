"""Index of the typed, dependency-injected capabilities of the Judge.

These are service interfaces called by deterministic backend code. None of
them is a tool the model can invoke: the Judge model receives prepared
evidence and returns structured data, with no shell, filesystem, SQL,
browsing or memory-write authority.

Capability            Defined in                          Tests
--------------------  ----------------------------------  -------------------------
FeedEligibilityGate   judge.privacy.eligibility           unit/test_privacy_gate.py
ReelSegmenter         judge.segmentation.segmenter        unit/test_segmentation.py
AudioAssembler        judge.audio.assembler               unit/test_audio.py
AacDecoder            judge.audio.codec                   unit/test_audio.py
ValuesReader          judge.values.reader                 unit/test_values.py
FeedbackRetriever     judge.feedback.retriever            unit/test_services.py
UnderstandingProvider judge.understanding.provider        unit/test_adapters.py
EmbeddingProvider     judge.understanding.provider        unit/test_adapters.py
Reel/Session/Feedback judge.persistence.interfaces        storage/test_repositories.py
  repositories, NotificationRepository (policy and outbox), Storage
RateLimiter           judge.runtime.rate_limiter          unit/test_runtime.py
Clock                 judge.runtime.clock                 unit/test_runtime.py
WorkerQueue           judge.runtime.worker_queue          unit/test_runtime.py

Inputs, outputs, timeouts, side effects and errors of each capability are in
its docstring and in ``watcher_Agent/docs/API_CONTRACT.txt`` (section 9).
"""

from __future__ import annotations

from judge.audio.assembler import AudioAssembler
from judge.audio.codec import AacDecoder
from judge.feedback.retriever import FeedbackRetriever
from judge.persistence.interfaces import (
    FeedbackRepository,
    NotificationRepository,
    PolicyRepository,
    ReelRepository,
    SessionRepository,
    Storage,
)
from judge.privacy.eligibility import BackendEligibilityGate as FeedEligibilityGate
from judge.runtime.clock import Clock
from judge.runtime.rate_limiter import RateLimiter
from judge.runtime.worker_queue import WorkerQueue
from judge.segmentation.segmenter import ReelSegmenter
from judge.understanding.provider import EmbeddingProvider, UnderstandingProvider
from judge.values.reader import ValuesReader

__all__ = [
    "AacDecoder",
    "AudioAssembler",
    "Clock",
    "EmbeddingProvider",
    "FeedEligibilityGate",
    "FeedbackRepository",
    "FeedbackRetriever",
    "NotificationRepository",
    "PolicyRepository",
    "RateLimiter",
    "ReelRepository",
    "ReelSegmenter",
    "SessionRepository",
    "Storage",
    "UnderstandingProvider",
    "ValuesReader",
    "WorkerQueue",
]
