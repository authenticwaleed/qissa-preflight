"""Response models: the API's contract.

UploadOut has one block per client requirement: duration (2), quality (3) and duplicate (4).
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import Upload


class FileOut(BaseModel):
    size_bytes: int
    sha256: str = Field(description="SHA-256 of the file's bytes, in hex.")


class DurationOut(BaseModel):
    seconds: float = Field(description="Duration in seconds, rounded to 0.01.", examples=[1441.3])
    formatted: str = Field(description="mm:ss, or h:mm:ss from one hour.", examples=["24:01"])
    is_outlier: bool
    outlier_reason: str | None = Field(
        default=None, examples=["4.0× the median chapter of this book (24:01)"]
    )


class DeductionOut(BaseModel):
    code: str = Field(examples=["NOT_CBR"])
    points: int = Field(examples=[2])
    message: str


class QualityOut(BaseModel):
    score: int = Field(ge=1, le=10, description="10 means the file meets the ACX encoding spec.")
    bitrate_kbps: int
    bitrate_mode: Literal["CBR", "VBR", "ABR", "UNKNOWN"]
    sample_rate_hz: int
    channels: int
    deductions: list[DeductionOut] = Field(description="Why points were lost, one per problem.")


class OriginalUploadOut(BaseModel):
    id: uuid.UUID
    filename: str
    book_id: str | None
    uploaded_at: datetime
    url: str = Field(examples=["/api/uploads/1d2e9c4a-5b6f-4a7e-9d8c-0b1a2c3d4e5f"])


class DuplicateOut(BaseModel):
    is_duplicate: bool
    original: OriginalUploadOut | None = Field(
        default=None, description="The first upload of the same bytes, when this is a duplicate."
    )


class UploadOut(BaseModel):
    id: uuid.UUID
    filename: str
    book_id: str | None
    uploaded_at: datetime
    file: FileOut
    duration: DurationOut
    quality: QualityOut
    duplicate: DuplicateOut


class ErrorDetail(BaseModel):
    code: str = Field(examples=["INVALID_MP3"])
    message: str


class ErrorOut(BaseModel):
    error: ErrorDetail


class HealthOut(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["ok", "error"]
    storage: Literal["ok", "error"]


def to_upload_out(upload: Upload) -> UploadOut:
    """Build the response for an Upload row (its .audio and .duplicate_of must be loadable).

    POST /api/upload and GET /api/uploads/{id} both use this, so the two always match.
    """
    raise NotImplementedError
