from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
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
    max_pdf_pages: int = Field(default=8, gt=0)
    max_upload_mb: int = Field(default=15, gt=0)
    max_upload_bytes: int | None = Field(default=None, gt=0)
    max_render_pixels: int = Field(default=40_000_000, gt=0)
    environment: str = "development"
    auth_mode: str = "development"
    jwt_secret: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_audience: str | None = None
    demo_actor_id: str = "local-demo-admin"
    demo_actor_name: str = "Local Demo Admin (development only)"
    demo_actor_role: str = "admin"
    policy_version: str = "2026-07-v1"

    @field_validator("environment", "auth_mode", "demo_actor_role")
    @classmethod
    def normalize_security_mode(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("jwt_algorithm")
    @classmethod
    def normalize_jwt_algorithm(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def vendor_rules_path(self) -> Path:
        return ROOT_DIR / "config" / "vendor_rules.json"

    @property
    def upload_limit_bytes(self) -> int:
        return self.max_upload_bytes or self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
