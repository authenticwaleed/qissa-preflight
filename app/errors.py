"""Errors that the API returns as {"error": {"code": ..., "message": ...}}.

Each error carries its HTTP status and a stable code that clients can rely on. The API layer adds
two more codes: VALIDATION_ERROR (422, a missing or invalid form field or path parameter) and
INTERNAL_ERROR (500, never with a stack trace).
"""


class AppError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedFileType(AppError):
    """The filename does not end in .mp3."""

    status_code = 415
    code = "UNSUPPORTED_FILE_TYPE"


class InvalidMp3(AppError):
    """The bytes are not MP3 audio (no MPEG Layer III frames could be read)."""

    status_code = 415
    code = "INVALID_MP3"


class EmptyFile(AppError):
    status_code = 422
    code = "EMPTY_FILE"


class FileTooLarge(AppError):
    status_code = 413
    code = "FILE_TOO_LARGE"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"
