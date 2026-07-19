from __future__ import annotations

import errno
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings


class UploadTooLarge(ValueError):
    pass


def quarantine_upload(upload: UploadFile) -> tuple[Path, str, int]:
    """Stream an untrusted upload to an OS temporary file while calculating its hash."""
    settings = get_settings()
    handle = tempfile.NamedTemporaryFile(prefix="invoice-upload-", suffix=".quarantine", delete=False)
    digest = hashlib.sha256()
    size = 0
    try:
        with handle:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.upload_limit_bytes:
                    raise UploadTooLarge(f"PDF exceeds {settings.upload_limit_bytes} byte limit.")
                digest.update(chunk)
                handle.write(chunk)
        return Path(handle.name), digest.hexdigest(), size
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise


def commit_quarantined_pdf(path: Path, digest: str) -> str:
    """Move a validated file into managed storage under a server-generated name."""
    storage_key = f"{digest[:16]}-{uuid4().hex}.pdf"
    destination = file_path(storage_key)
    try:
        os.replace(path, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        staged = destination.with_suffix(".part")
        try:
            shutil.copyfile(path, staged)
            os.replace(staged, destination)
            path.unlink(missing_ok=True)
        except Exception:
            staged.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
    return storage_key


def file_path(storage_key: str) -> Path:
    storage = get_settings().storage_dir.resolve()
    normalized = storage_key.replace("\\", "/")
    safe_key = normalized.rsplit("/", 1)[-1]
    if not safe_key or safe_key in {".", ".."} or safe_key != storage_key:
        raise ValueError("Invalid managed storage key.")
    candidate = (storage / safe_key).resolve()
    if candidate.parent != storage:
        raise ValueError("Invalid managed storage key.")
    return candidate


def reset_storage() -> None:
    storage = get_settings().storage_dir
    for child in storage.iterdir():
        if child.name != ".gitkeep":
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
