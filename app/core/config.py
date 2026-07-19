from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://postgres:postgres@localhost:5432/invoice_resolution"
    storage_dir: Path = ROOT_DIR / "storage"

    job_lease_seconds: int = 300
    job_max_attempts: int = 3
    worker_poll_seconds: float = 1.0

    medha_api_url: str | None = None
    medha_api_key: str | None = None
    medha_model: str = "Medha"
    medha_timeout_seconds: int = 120
    medha_max_retries: int = 1
    medha_json_mode: bool = True

    enable_medha: bool = True
    enable_paddle_ocr: bool = False
    native_text_min_chars: int = 80
    max_pdf_pages: int = 8
    max_upload_mb: int = 15
    policy_version: str = "2026-07-v1"

    @property
    def vendor_rules_path(self) -> Path:
        return ROOT_DIR / "config" / "vendor_rules.json"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings

