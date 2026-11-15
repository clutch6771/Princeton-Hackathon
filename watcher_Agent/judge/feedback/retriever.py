"""Bounded retrieval of explicit feedback examples for the Judge prompt.

Retrieval ordering, stated honestly: the new reel's transcript and
description do not exist before the understanding call, so the Judge cannot
retrieve feedback by similarity to the reel it is about to judge. Version 1
therefore uses a clearly labeled fallback, ``recent_explicit_feedback``: the
most recent explicit feedback, one item per reel, at most three. No extra
captioning call or per-reel embedding is added to build a query.

Examples are bound to the approved-values revision their reel was judged
under. When the values change, older examples are no longer supplied. Zero
examples is a valid result.
"""

from __future__ import annotations

from typing import Protocol

from judge.contracts.feedback import (
    FeedbackExample,
    FeedbackLabel,
    RetrievalMode,
)
from judge.contracts.storage import FeedbackExampleRow
from judge.contracts.values import ApprovedValuesSnapshot
from judge.persistence.interfaces import Storage

_OVERFETCH_FACTOR = 4


class FeedbackRetriever(Protocol):
    """Capability: up to three explicit feedback examples for a judgment.

    Read-only. Raises storage errors; the pipeline treats a failure as zero
    examples.
    """

    async def retrieve(
        self, owner_id: str, values: ApprovedValuesSnapshot
    ) -> tuple[FeedbackExample, ...]:
        """Return at most the configured number of examples."""
        ...

    def invalidate(self, owner_id: str) -> None:
        """Drop cached examples after new feedback or a deletion."""
        ...


class RecentFeedbackRetriever:
    """The labeled ``recent_explicit_feedback`` fallback."""

    def __init__(
        self, *, storage: Storage, max_examples: int, comment_max_chars: int
    ) -> None:
        self._storage = storage
        self._max_examples = max_examples
        self._comment_max_chars = comment_max_chars
        self._cache: dict[tuple[str, str], tuple[FeedbackExample, ...]] = {}

    def invalidate(self, owner_id: str) -> None:
        for key in [k for k in self._cache if k[0] == owner_id]:
            self._cache.pop(key)

    async def retrieve(
        self, owner_id: str, values: ApprovedValuesSnapshot
    ) -> tuple[FeedbackExample, ...]:
        if self._max_examples <= 0:
            return ()
        key = (owner_id, values.content_sha256)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        async with self._storage.transaction() as tx:
            rows = await tx.feedback.list_example_rows(
                owner_id,
                values.content_sha256,
                self._max_examples * _OVERFETCH_FACTOR,
            )
        examples = tuple(self._example(row) for row in rows if self._usable(row))[
            : self._max_examples
        ]
        self._cache[key] = examples
        return examples

    def _usable(self, row: FeedbackExampleRow) -> bool:
        comment = (row.comment or "").strip()
        if len(comment) > self._comment_max_chars:
            # Dropped whole: the user's words are never cut mid-clause.
            return False
        return bool(comment) or row.label is not FeedbackLabel.RERATE

    @staticmethod
    def _example(row: FeedbackExampleRow) -> FeedbackExample:
        return FeedbackExample(
            feedback_id=row.feedback_id,
            reel_id=row.reel_id,
            label=row.label,
            comment=(row.comment or "").strip() or None,
            reel_description=row.reel_description,
            prior_verdict=row.prior_verdict,
            prior_value_ids=row.prior_value_ids,
            values_revision=row.values_revision,
            values_sha256=row.values_sha256,
            created_at=row.created_at,
            retrieval_mode=RetrievalMode.RECENT_EXPLICIT_FEEDBACK,
        )
