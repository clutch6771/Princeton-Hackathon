"""The Guardian's HTTP routes.

Every route is a plain `def`, so FastAPI runs it in a worker thread: the Guardian's
functions block while they call the model, and the Judge's data source must be
called from a worker thread.

JSON routes (one Guardian function each, its result returned unchanged):

    POST /chat                    {"message": "...", "context": {...} or null}
    POST /chat/resume             {"action": "approve" | "edit" | "reject", "edited_text": ...}
    GET  /status                  -> {"has_values": bool}
    GET  /memory                  -> {"values.md": "...", "brief.md": "...", ...}
    GET  /questions               -> the inbox
    POST /questions/{id}/answer   {"action": "...", "edited_text": ...}

App route (see chat_adapter.py):

    POST /agent2/chat             {"message", "profile", "context"} -> ChatMessage
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, cast

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from guardian import api, chat_flow, config, storage
from guardian.memory import reviews
from guardian.types import Answer, ChatContext, Decision
from guardian_router import background, reset_demo
from guardian_router.chat_adapter import ChatAdapter, Digest, canonical_reel_id

DEFAULT_USER = "demo-user"
USER_HEADER = "X-Demo-User-Id"
SENDER_GUARDIAN = "coachAgent"  # the app's name for the Guardian's side of the chat
TIMESTAMP_FORMAT = (
    "%Y-%m-%dT%H:%M:%SZ"  # no fractional seconds: the app's decoder is strict
)
NO_DATASOURCE = (
    "The Guardian has no data source: set app.state.guardian_datasource before the "
    "first request (the Judge server does this at startup)."
)

router = APIRouter(tags=["guardian"])
_setup_lock = threading.Lock()


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Runtime:
    """What the routes of one app share."""

    clock: Callable[[], datetime]
    launch: background.Launcher
    adapter: ChatAdapter
    # The Guardian's Postgres connection string when it stores to a database; None
    # when it is in memory. Never returned or logged.
    database_url: str | None = None


def new_runtime(
    clock: Callable[[], datetime] | None = None,
    launch: background.Launcher | None = None,
    database_url: str | None = None,
) -> Runtime:
    now = clock or utc_now
    return Runtime(
        clock=now,
        launch=launch or background.thread_launcher,
        adapter=ChatAdapter(now),
        database_url=database_url,
    )


def get_runtime(request: Request) -> Runtime:
    """This app's runtime. In a host server it is created on the first request, which
    also configures the Guardian with the host's data source and storage from the
    environment."""
    state = request.app.state
    existing = getattr(state, "guardian_runtime", None)
    if existing is not None:
        return cast(Runtime, existing)
    with _setup_lock:
        existing = getattr(state, "guardian_runtime", None)
        if existing is None:
            datasource = getattr(state, "guardian_datasource", None)
            if datasource is None:
                raise HTTPException(status_code=503, detail=NO_DATASOURCE)
            api.configure(datasource=datasource)
            background.mark_ready()
            existing = state.guardian_runtime = new_runtime(
                database_url=storage.database_url()
            )
    return cast(Runtime, existing)


def get_user_id(
    x_demo_user_id: Annotated[str | None, Header()] = None,
) -> str:
    return (x_demo_user_id or "").strip() or DEFAULT_USER


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]
UserDep = Annotated[str, Depends(get_user_id)]


def start_review_if_ready(runtime: Runtime, user_id: str) -> None:
    """Start a background review. Until the user has an approved value there is
    nothing for a review to refine, so none is started."""
    if api.has_values(user_id):
        background.start_review(user_id, runtime.launch)


# --- JSON routes --------------------------------------------------------------------------


class ChatBody(BaseModel):
    message: str = ""
    context: dict[str, Any] | None = None


class DecisionBody(BaseModel):
    action: str
    edited_text: str | None = None


class AnswerBody(BaseModel):
    action: str
    edited_text: str | None = None
    ratings: dict[str, str] | None = None


@router.post("/chat")
def chat(body: ChatBody, runtime: RuntimeDep, user_id: UserDep) -> dict[str, Any]:
    # The Guardian validates the user, the context and the message itself.
    context = cast(ChatContext | None, body.context)
    thread = runtime.adapter.thread_id(user_id)
    return dict(api.chat(user_id, thread, body.message, context))


@router.post("/chat/resume")
def resume(body: DecisionBody, runtime: RuntimeDep, user_id: UserDep) -> dict[str, Any]:
    decision = cast(Decision, {"action": body.action, "edited_text": body.edited_text})
    thread = runtime.adapter.thread_id(user_id)
    return dict(api.resume(user_id, thread, decision))


@router.get("/status")
def status(runtime: RuntimeDep, user_id: UserDep) -> dict[str, bool]:
    return {"has_values": api.has_values(user_id)}


@router.get("/memory")
def memory(runtime: RuntimeDep, user_id: UserDep) -> dict[str, str]:
    return api.get_memory(user_id)


@router.get("/questions")
def questions(runtime: RuntimeDep, user_id: UserDep) -> list[dict[str, Any]]:
    start_review_if_ready(runtime, user_id)
    return [
        {**question, "created": question["created"].strftime(TIMESTAMP_FORMAT)}
        for question in api.get_questions(user_id)
    ]


@router.post("/questions/{question_id}/answer")
def answer(
    question_id: str, body: AnswerBody, runtime: RuntimeDep, user_id: UserDep
) -> dict[str, Any]:
    given = cast(Answer, body.model_dump(exclude_none=True))
    return dict(api.answer_question(user_id, question_id, given))


# --- Erasing an account -------------------------------------------------------------------

ALLOW_RESET_VAR = "GUARDIAN_ALLOW_RESET"  # the route answers 403 unless this is "1"
RESET_CONFIRMATION = "DELETE"
RESET_DISABLED = (
    f"Account reset is disabled on this server. Set {ALLOW_RESET_VAR}=1 to allow it."
)
RESET_NEEDS_USER = f"The {USER_HEADER} header is required, with a valid user ID."
RESET_NEEDS_CONFIRMATION = 'To erase this account, send {"confirm": "DELETE"}.'
RESET_WHILE_RECORDING = (
    "A recording session is still open. Stop the recording, then try again."
)
RESET_WHILE_REVIEWING = (
    "A review of the last session is still running. Try again in a few minutes."
)
RESET_FAILED = "The reset failed and nothing was deleted."


class ResetBody(BaseModel):
    confirm: str = ""


def is_recording(request: Request, user_id: str, now: datetime) -> bool:
    """Whether the user has a session that has not ended."""
    datasource = getattr(request.app.state, "guardian_datasource", None)
    if datasource is None:
        return False
    return any(
        not reviews.is_finished(session, now)
        for session in datasource.list_sessions(user_id)
    )


@router.post("/account/reset")
def reset_account(
    body: ResetBody,
    request: Request,
    runtime: RuntimeDep,
    x_demo_user_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Erase everything stored about the header's user: Guardian records and, on a
    database, the Judge's rows. It cannot be undone, so it is off by default, takes
    no default user, and needs an exact confirmation."""
    config.load_env()  # so the switch may be set in the repository's .env
    if os.environ.get(ALLOW_RESET_VAR, "").strip() != "1":
        raise HTTPException(status_code=403, detail=RESET_DISABLED)
    user_id = (x_demo_user_id or "").strip()
    if not chat_flow.is_valid_user_id(user_id):
        raise HTTPException(status_code=400, detail=RESET_NEEDS_USER)
    if body.confirm != RESET_CONFIRMATION:
        raise HTTPException(status_code=400, detail=RESET_NEEDS_CONFIRMATION)
    if is_recording(request, user_id, runtime.clock()):
        raise HTTPException(status_code=409, detail=RESET_WHILE_RECORDING)
    if background.is_reviewing(user_id):
        raise HTTPException(status_code=409, detail=RESET_WHILE_REVIEWING)

    # Wait for any chat turn of this user to finish, and keep new ones out meanwhile.
    with runtime.adapter.exclusive(user_id):
        if runtime.database_url:
            try:
                deleted = reset_demo.reset(
                    runtime.database_url, user_id, confirmed=True
                )
            except psycopg.Error as error:
                raise HTTPException(status_code=500, detail=RESET_FAILED) from error
        else:
            deleted = api.erase_user(user_id)
        runtime.adapter.forget(user_id)
    return {"user": user_id, "deleted": deleted}


# --- The app's chat route -----------------------------------------------------------------


class AppModel(BaseModel):
    """A body sent by the app. Fields the Guardian does not use are ignored."""

    model_config = ConfigDict(extra="ignore")


class Profile(AppModel):
    stated_goals: str = ""


class FlaggedReel(AppModel):
    id: str


class CheckpointDigest(AppModel):
    id: str | None = None
    flagged_reels: list[FlaggedReel] = Field(default_factory=list)


class AppChatBody(AppModel):
    message: str = ""
    profile: Profile = Field(default_factory=Profile)
    context: CheckpointDigest | None = None


class ChatMessage(BaseModel):
    """What the app's chat screen decodes."""

    id: str
    sender: str
    text: str
    timestamp: str
    attached_digest: None = None
    client_action: None = None


def to_digest(context: CheckpointDigest | None) -> Digest | None:
    if context is None:
        return None
    reel_ids = tuple(canonical_reel_id(reel.id) for reel in context.flagged_reels)
    return Digest(key=context.id or ",".join(reel_ids), reel_ids=reel_ids)


@router.post("/agent2/chat")
def app_chat(body: AppChatBody, runtime: RuntimeDep, user_id: UserDep) -> ChatMessage:
    start_review_if_ready(runtime, user_id)
    text = runtime.adapter.reply(
        user_id, body.message, body.profile.stated_goals, to_digest(body.context)
    )
    return ChatMessage(
        id=str(uuid.uuid4()),
        sender=SENDER_GUARDIAN,
        text=text,
        timestamp=runtime.clock().astimezone(UTC).strftime(TIMESTAMP_FORMAT),
    )
