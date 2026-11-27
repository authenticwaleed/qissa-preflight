"""Settings, read from environment variables or a local .env file (see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://qissa:qissa@localhost:5433/qissa"
    storage_dir: Path = Path("data/audio")
    # Above ACX's 170 MB per-file limit on purpose: an oversized chapter gets a clear quality
    # deduction instead of a bare 413. 120 minutes at 320 kbps is 288 MB.
    max_upload_mb: int = Field(default=300, ge=1, description="Largest accepted upload, decimal MB")
    log_level: str = "INFO"

    # Duration outlier rule: see app/outliers.py and the README.
    outlier_min_seconds: float = Field(default=30.0, ge=0)
    outlier_max_seconds: float = Field(default=7200.0, gt=0)  # ACX: at most 120 minutes per file
    outlier_ratio: float = Field(default=3.0, gt=1)
    outlier_min_chapters: int = Field(default=3, ge=1)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
