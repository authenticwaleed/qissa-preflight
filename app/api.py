"""HTTP routes under /api.

POST /api/upload        201 UploadOut, Location: /api/uploads/{id}
GET  /api/uploads/{id}  200 UploadOut, or 404
GET  /api/health        200 HealthOut, or 503

The endpoints are plain functions rather than coroutines on purpose: their work is blocking file
and database I/O, so FastAPI runs them in its thread pool and the event loop stays free.
"""

import logging
import uuid
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import repository, service
from app.config import Settings
from app.errors import NotFound
from app.schemas import ErrorOut, HealthOut, UploadOut, to_upload_out
from app.storage import LocalStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

BOOK_ID_MAX_LENGTH = 100  # uploads.book_id is VARCHAR(100)


def get_session(request: Request) -> Iterator[Session]:
    """One session per request. Closing it rolls back whatever was left uncommitted."""
    with request.app.state.session_factory() as session:
        yield session


def get_storage(request: Request) -> LocalStorage:
    return request.app.state.storage


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDep = Annotated[Session, Depends(get_session)]
StorageDep = Annotated[LocalStorage, Depends(get_storage)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a chapter MP3 and check it",
    responses={
        413: {"model": ErrorOut, "description": "FILE_TOO_LARGE: the file is over the limit."},
        415: {
            "model": ErrorOut,
            "description": "UNSUPPORTED_FILE_TYPE: the filename does not end in .mp3. "
            "INVALID_MP3: the content is not MP3 audio.",
        },
        422: {
            "model": ErrorOut,
            "description": "EMPTY_FILE: the file has no bytes. "
            "VALIDATION_ERROR: `file` is missing or `book_id` is too long.",
        },
    },
)
def upload_chapter(
    file: Annotated[UploadFile, File(description="The chapter, an .mp3 file.")],
    response: Response,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    book_id: Annotated[
        str | None,
        Form(
            max_length=BOOK_ID_MAX_LENGTH,
            description="Optional. The book this chapter belongs to: its duration is then "
            "compared with the book's earlier chapters.",
        ),
    ] = None,
) -> UploadOut:
    """Stores the file and returns its analysis: the duration with an outlier flag, a 1–10
    quality score with the reason for every lost point, and whether the same bytes were uploaded
    before, whatever the filename. A duplicate is not an error: it is recorded as a new upload
    that points at the original, and the file is not stored a second time.
    """
    upload = service.process_upload(
        stream=file.file,
        filename=file.filename,
        book_id=_clean_book_id(book_id),
        session=session,
        storage=storage,
        settings=settings,
    )
    response.headers["Location"] = f"/api/uploads/{upload.id}"
    return to_upload_out(upload)


@router.get(
    "/uploads/{upload_id}",
    summary="Get the results of an earlier upload",
    responses={
        404: {"model": ErrorOut, "description": "NOT_FOUND: no upload has this id."},
        422: {"model": ErrorOut, "description": "VALIDATION_ERROR: the id is not a UUID."},
    },
)
def get_upload(upload_id: uuid.UUID, session: SessionDep) -> UploadOut:
    """The same body that the upload returned."""
    upload = repository.get_upload(session, upload_id)
    if upload is None:
        raise NotFound(f"No upload with id {upload_id}.")
    return to_upload_out(upload)


@router.get(
    "/health",
    summary="Check that the database and the storage are usable",
    responses={
        503: {"model": HealthOut, "description": "The database or the storage is not usable."}
    },
)
def health(response: Response, session: SessionDep, storage: StorageDep) -> HealthOut:
    """For load balancers and container platforms: 200 when both checks pass, otherwise 503
    with the failing check set to "error"."""
    database_ok = _database_responds(session)
    storage_ok = storage.is_writable()
    if not (database_ok and storage_ok):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(
        status=_ok_or_error(database_ok and storage_ok),
        database=_ok_or_error(database_ok),
        storage=_ok_or_error(storage_ok),
    )


def _clean_book_id(book_id: str | None) -> str | None:
    """Trimmed, so " book-1 " and "book-1" are the same book; blank means no book."""
    return (book_id or "").strip() or None


def _database_responds(session: Session) -> bool:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # The class name is enough to act on; driver messages run over several lines.
        logger.warning("Health check: database unavailable (%s)", type(exc).__name__)
        return False
    return True


def _ok_or_error(ok: bool) -> Literal["ok", "error"]:
    return "ok" if ok else "error"
