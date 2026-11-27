"""App factory. Run with: uvicorn app.main:create_app --factory"""

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app import api, db
from app.config import Settings, get_settings
from app.errors import AppError, FileTooLarge
from app.schemas import ErrorDetail, ErrorOut
from app.storage import LocalStorage

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
UPLOAD_PATH = "/api/upload"
# Room for the multipart boundary and part headers around the file. The service checks the
# file's exact size while storing it, so this margin only has to be generous.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app: routes, JSON error handlers, the upload size guard, and the database
    engine, session factory and storage on app.state (the engine is disposed on shutdown).

    Uses get_settings() when ``settings`` is None.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Qissa preflight",
        version="0.1.0",
        lifespan=lifespan,
        exception_handlers={
            AppError: handle_app_error,
            RequestValidationError: handle_validation_error,
            StarletteHTTPException: handle_http_error,
            Exception: handle_unexpected_error,
        },
        middleware=[
            Middleware(
                UploadSizeGuard,
                max_body_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
                max_upload_mb=settings.max_upload_mb,
            )
        ],
    )
    app.state.settings = settings
    app.state.engine = db.make_engine(settings.database_url)
    app.state.session_factory = db.make_session_factory(app.state.engine)
    app.state.storage = LocalStorage(settings.storage_dir)
    app.include_router(api.router)
    return app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Everything is built in create_app, so the app is complete as soon as it exists; shutdown
    # only has to close the pooled database connections.
    yield
    app.state.engine.dispose()


def configure_logging(level: str) -> None:
    """One line per record on stderr. Does nothing when logging is already set up, for example
    by a test runner, so the host keeps its own configuration."""
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT)


class UploadSizeGuard:
    """Refuses an upload whose Content-Length is already over the limit, before any of the body
    is read, so a narrator is not left sending hundreds of MB only to be refused at the end.

    A request without a Content-Length (chunked) passes: the service counts its bytes while
    storing them.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int, max_upload_mb: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_upload_mb = max_upload_mb

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._declares_too_much(scope):
            response = error_response(
                FileTooLarge.status_code,
                FileTooLarge.code,
                f"The file is larger than the {self.max_upload_mb} MB limit.",
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _declares_too_much(self, scope: Scope) -> bool:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != UPLOAD_PATH:
            return False
        try:
            return int(Headers(scope=scope).get("content-length", "")) > self.max_body_bytes
        except ValueError:
            return False  # no usable Content-Length: left to the service's own count


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(422, "VALIDATION_ERROR", describe_validation_errors(exc.errors()))


async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Errors the framework raises itself, such as an unknown path (404), a wrong method (405)
    or a body that is not valid multipart (400), so that they share the error shape too."""
    code = HTTPStatus(exc.status_code).name
    return error_response(exc.status_code, code, str(exc.detail), exc.headers)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # The traceback goes to the log only: a client never sees the internals.
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "INTERNAL_ERROR", "Something went wrong.")


def error_response(
    status_code: int, code: str, message: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    body = ErrorOut(error=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(), status_code=status_code, headers=headers)


def describe_validation_errors(errors: Sequence[Any]) -> str:
    """One readable line, such as "file: Field required". The first part of each location
    (body, path or query) is left out: the field name is what a client can act on."""
    return "; ".join(f"{_field_name(error['loc'])}: {error['msg']}" for error in errors)


def _field_name(loc: Sequence[str | int]) -> str:
    return ".".join(str(part) for part in loc[1:] or loc)
