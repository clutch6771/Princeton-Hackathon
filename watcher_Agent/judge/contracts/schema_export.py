"""Export the versioned contracts as JSON Schema for native and GUI teams.

``watcher_Agent/scripts/export_contract_schemas.py`` writes the result to
``watcher_Agent/contracts/v1``. A test fails when the committed files drift
from these models.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel

from judge.contracts.base import CONTRACT_VERSION
from judge.contracts.candidate import ReelCandidate
from judge.contracts.capture import AudioChunk, FrameObservation
from judge.contracts.feedback import FeedbackExample, FeedbackRequest
from judge.contracts.notification import (
    IngestResponse,
    NotificationEnvelope,
    NotificationPolicy,
    NotificationPolicyUpdate,
)
from judge.contracts.record import ReelRecord
from judge.contracts.session import (
    SessionEndRequest,
    SessionEndResponse,
    SessionEvent,
)
from judge.contracts.understanding import ModelJudgment, ReelUnderstandingResult
from judge.contracts.values import ApprovedValuesSnapshot, ValuesStoreItem

SchemaMode = Literal["validation", "serialization"]

JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"

# name -> (model, mode). "validation" for inputs, "serialization" for outputs.
SCHEMA_MODELS: Final[dict[str, tuple[type[BaseModel], SchemaMode]]] = {
    "frame_observation": (FrameObservation, "validation"),
    "audio_chunk": (AudioChunk, "validation"),
    "session_end_request": (SessionEndRequest, "validation"),
    "feedback_request": (FeedbackRequest, "validation"),
    "notification_policy_update": (NotificationPolicyUpdate, "validation"),
    "values_store_item": (ValuesStoreItem, "validation"),
    "reel_candidate": (ReelCandidate, "serialization"),
    "approved_values_snapshot": (ApprovedValuesSnapshot, "serialization"),
    "feedback_example": (FeedbackExample, "serialization"),
    "model_judgment": (ModelJudgment, "serialization"),
    "reel_understanding_result": (ReelUnderstandingResult, "serialization"),
    "reel_record": (ReelRecord, "serialization"),
    "notification_envelope": (NotificationEnvelope, "serialization"),
    "ingest_response": (IngestResponse, "serialization"),
    "session_end_response": (SessionEndResponse, "serialization"),
    "notification_policy": (NotificationPolicy, "serialization"),
    "session_event": (SessionEvent, "serialization"),
}


def build_schemas() -> dict[str, dict[str, Any]]:
    """Return ``{file name: JSON Schema}`` for every exported contract."""
    schemas: dict[str, dict[str, Any]] = {}
    for name, (model, mode) in SCHEMA_MODELS.items():
        schema = model.model_json_schema(mode=mode)
        schemas[f"{name}.schema.json"] = {
            "$schema": JSON_SCHEMA_DIALECT,
            "x-contract-version": CONTRACT_VERSION,
            **schema,
        }
    return schemas
