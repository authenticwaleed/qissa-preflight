"""App factory. Run with: uvicorn app.main:create_app --factory"""

from fastapi import FastAPI

from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app: routes, JSON error handlers, the upload size guard, and the database
    engine, session factory and storage on app.state (the engine is disposed on shutdown).

    Uses get_settings() when ``settings`` is None.
    """
    raise NotImplementedError
