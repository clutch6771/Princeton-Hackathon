"""Explicit feedback: stored evidence and prompt examples.

Feedback supplies examples to the Judge. It never authorizes the Judge to
change approved values, and storing it triggers no scheduler or conversation.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from judge.contracts.base import CONTRACT_VERSION, Contract, ContractVersion
from judge.contracts.understanding import Verdict


class FeedbackLabel(StrEnum):
    """Explicit user reaction to a verdict."""

    AGREE = "agree"
    DISAGREE = "disagree"
    NOT_FOR_ME = "not_for_me"
    RERATE = "rerate"


class RetrievalMode(StrEnum):
    """How the examples supplied to a judgment were chosen.

    The new reel's text does not exist before the understanding call, so the
    Judge cannot retrieve by similarity to it. Version 1 uses a bounded,
    clearly labeled recent-feedback fallback.
    """

    RECENT_EXPLICIT_FEEDBACK = "recent_explicit_feedback"
    NONE = "none"


class FeedbackRequest(Contract):
    """Body of ``POST /feedback``."""

    contract_version: ContractVersion = CONTRACT_VERSION
    reel_id: UUID
    label: FeedbackLabel
    score: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description=(
            "Optional, client-defined. The PRD defines no range; the Judge "
            "stores it and does not interpret it."
        ),
    )
    comment: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(
        default=None,
        max_length=64,
        description="Where the feedback came from, e.g. 'timeline' or 'share'.",
    )
    client_feedback_id: UUID | None = Field(
        default=None, description="Optional idempotency key chosen by the client."
    )


class FeedbackRecord(Contract):
    """One stored piece of explicit feedback."""

    feedback_id: UUID
    owner_id: str
    reel_id: UUID
    label: FeedbackLabel
    score: float | None = None
    comment: str | None = None
    source: str | None = None
    client_feedback_id: UUID | None = None
    created_at: datetime


class FeedbackExample(Contract):
    """One explicit feedback example placed in a Judge prompt (at most three)."""

    contract_version: ContractVersion = CONTRACT_VERSION
    feedback_id: UUID
    reel_id: UUID
    label: FeedbackLabel
    comment: str | None = None
    reel_description: str
    prior_verdict: Verdict | None = None
    prior_value_ids: tuple[str, ...] = ()
    values_revision: str | None = None
    values_sha256: str | None = None
    created_at: datetime
    retrieval_mode: RetrievalMode
