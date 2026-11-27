"""GET /api/health: 200 when the database and the storage folder work, 503 when one does not."""

import shutil
from collections.abc import Callable

from fastapi.testclient import TestClient

from app.config import Settings

# Nothing listens on port 1, so every connection is refused at once.
UNREACHABLE_DATABASE_URL = (
    "postgresql+psycopg://qissa:qissa@127.0.0.1:1/qissa_test?connect_timeout=3"
)


def test_health_is_ok_when_the_database_and_storage_work(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "database": "ok", "storage": "ok"}


def test_health_is_503_when_the_database_is_unreachable(
    make_client: Callable[..., TestClient],
) -> None:
    client = make_client(database_url=UNREACHABLE_DATABASE_URL)

    response = client.get("/api/health")

    assert response.status_code == 503, response.text
    assert response.json() == {"status": "error", "database": "error", "storage": "ok"}


def test_health_is_503_when_the_storage_folder_is_unusable(
    client: TestClient, settings: Settings
) -> None:
    storage = settings.storage_dir
    if storage.exists():
        shutil.rmtree(storage)
    storage.write_bytes(b"")  # a plain file where the storage folder should be

    response = client.get("/api/health")

    assert response.status_code == 503, response.text
    assert response.json() == {"status": "error", "database": "ok", "storage": "error"}
