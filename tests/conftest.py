"""Shared test setup.

Integration tests (everything under tests/integration) run against a real PostgreSQL database,
the same engine as production, and a temporary storage folder. Start the database with
`docker compose up -d db`; the tests create and migrate the `qissa_test` database themselves.
Unit tests need neither: `uv run pytest -m "not integration"`.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://qissa:qissa@localhost:5433/qissa_test"
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def database_url() -> str:
    """Create the test database if needed, reset its schema and apply the migrations."""
    url = make_url(TEST_DATABASE_URL)
    if not (url.database or "").endswith("_test"):
        pytest.fail(
            f"TEST_DATABASE_URL must name a *_test database, got {url.database!r}", pytrace=False
        )

    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError as exc:
        pytest.fail(
            f"PostgreSQL is not reachable at {url.render_as_string(hide_password=True)}. "
            f"Start it with `docker compose up -d db`. ({exc.orig})",
            pytrace=False,
        )
    finally:
        admin.dispose()

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    rendered = url.render_as_string(hide_password=False)
    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")
    return rendered


@pytest.fixture
def db_engine(database_url: str) -> Iterator[Engine]:
    """An engine on the migrated test database. Both tables are emptied after each test."""
    engine = create_engine(database_url)
    yield engine
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE uploads, audio_files"))
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    with Session(db_engine) as session:
        yield session
        session.rollback()


@pytest.fixture
def settings(tmp_path: Path, database_url: str) -> Settings:
    """Test settings: the test database, temp storage, and the 4-second fixtures allowed."""
    return Settings(
        database_url=database_url,
        storage_dir=tmp_path / "audio",
        max_upload_mb=5,
        outlier_min_seconds=1.0,
    )


@pytest.fixture
def client(settings: Settings, db_engine: Engine) -> Iterator[TestClient]:
    """The real app over HTTP, wired to the test database and temp storage."""
    from app.main import create_app

    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def make_client(settings: Settings, db_engine: Engine) -> Iterator[Callable[..., TestClient]]:
    """Like `client`, with some settings changed: make_client(max_upload_mb=1)."""
    from app.main import create_app

    opened: list[TestClient] = []

    def make(**overrides: Any) -> TestClient:
        test_client = TestClient(create_app(settings.model_copy(update=overrides)))
        test_client.__enter__()
        opened.append(test_client)
        return test_client

    yield make
    for test_client in opened:
        test_client.__exit__(None, None, None)


@pytest.fixture
def fixture_file() -> Callable[[str], Path]:
    """Path to a file in tests/fixtures (described in tests/fixtures/README.md)."""

    def get(name: str) -> Path:
        path = FIXTURES / name
        if not path.is_file():
            raise FileNotFoundError(f"missing test fixture: {path}")
        return path

    return get
