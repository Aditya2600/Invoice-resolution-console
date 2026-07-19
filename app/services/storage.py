from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_uploaded_pdf(upload: UploadFile, data: bytes) -> tuple[str, str]:
    """Returns (storage_key, sha256). Uses local storage for the MVP."""
    settings = get_settings()
    digest = sha256_bytes(data)
    filename = Path(upload.filename or "invoice.pdf").name
    storage_key = f"{digest[:16]}-{uuid4().hex[:8]}-{filename}"
    destination = settings.storage_dir / storage_key
    destination.write_bytes(data)
    return storage_key, digest


def file_path(storage_key: str) -> Path:
    return get_settings().storage_dir / storage_key


def reset_storage() -> None:
    storage = get_settings().storage_dir
    for child in storage.iterdir():
        if child.name != ".gitkeep":
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

