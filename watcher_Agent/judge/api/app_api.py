"""FastAPI application factory for the Judge backend.

Run (one process only, because media buffers live in this process):

    .venv/bin/uvicorn judge.api.app_api:create_app --factory \
        --app-dir watcher_Agent --workers 1
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from judge import __version__
from judge.api.deps import DemoHeaderResolver, describe_validation
from judge.api.native_api import router as native_router
from judge.api.routes_api import router
from judge.bootstrap import JudgeRuntime
from judge.config import Settings, load_settings, validate_for_startup
from judge.errors import ErrorCode, JudgeError

logger = logging.getLogger(__name__)

_DESCRIPTION = """\
Judge backend of the Values-Aligned Attention Companion.

Receives sampled JPEG frames and app-audio chunks, judges each observed reel
against the user's approved values, and stores verdicts for the app and
Guardian. Capture and notifications belong to the native app. Raw media is
held in volatile memory only.

Identity: every request must carry the `X-Demo-User-Id` header equal to the
one configured demo identity.
This demo header is not production authentication.
"""


def _error(
    status: int, code: str, message: str, retry_after: float | None = None
) -> JSONResponse:
    headers = {"Retry-After": str(max(1, round(retry_after)))} if retry_after else None
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "data": None,
            "error": {"code": code, "message": message},
        },
        headers=headers,
    )


async def _judge_error(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, JudgeError):
        raise error
    return _error(
        error.http_status, error.code.value, error.message, error.retry_after_seconds
    )


async def _request_validation(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    problems = "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors()
    )
    return _error(422, ErrorCode.VALIDATION.value, problems)


async def _model_validation(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, ValidationError):
        raise error
    return _error(422, ErrorCode.VALIDATION.value, describe_validation(error))


async def _unexpected(_request: Request, _error_: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return _error(500, ErrorCode.INTERNAL.value, "internal error")


def create_app(
    settings: Settings | None = None, *, runtime: JudgeRuntime | None = None
) -> FastAPI:
    """Build the app. Pass ``runtime`` to inject prepared services (tests)."""
    config = runtime.settings if runtime is not None else (settings or load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from judge.integration.guardian_source import JudgeDataSource

        if runtime is not None:
            app.state.guardian_datasource = JudgeDataSource(runtime)
            yield
            return
        from judge.runtime_factory import create_runtime

        validate_for_startup(config)
        created = await create_runtime(config)
        app.state.runtime = created
        await created.start(run_ticker=True)
        app.state.guardian_datasource = JudgeDataSource(created)
        logger.info("judge runtime started (single process required)")
        try:
            yield
        finally:
            await created.stop()

    app = FastAPI(
        title="Judge and Reel Transcriber",
        version=__version__,
        description=_DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.user_resolver = DemoHeaderResolver(
        header=config.demo_user_header, demo_user_id=config.demo_user_id or ""
    )
    app.add_exception_handler(JudgeError, _judge_error)
    app.add_exception_handler(RequestValidationError, _request_validation)
    app.add_exception_handler(ValidationError, _model_validation)
    app.add_exception_handler(Exception, _unexpected)
    app.include_router(router)
    app.include_router(native_router)
    return app
