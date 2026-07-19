from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import fitz
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.core import observability
from app.core import auth
from app.core.config import Settings
from app.db import repository
from app.services import pdf, storage
from tests.test_finalization import extraction


TEST_SECRET = "test-only-secret-with-at-least-32-bytes"


def bearer(role: str, *, subject: str | None = None, name: str | None = None) -> dict[str, str]:
    actor_id = subject or f"{role}-123"
    token = jwt.encode(
        {
            "sub": actor_id,
            "name": name or f"Test {role.title()}",
            "role": role,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def make_pdf(*, pages: int = 1, width: int = 100, height: int = 100) -> bytes:
    document = fitz.open()
    for _ in range(pages):
        document.new_page(width=width, height=height)
    data = document.tobytes()
    document.close()
    return data


def make_encrypted_pdf() -> bytes:
    document = fitz.open()
    document.new_page(width=100, height=100)
    data = document.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )
    document.close()
    return data


@pytest.fixture
def security_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    managed_storage = tmp_path / "managed"
    managed_storage.mkdir()
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    settings = Settings(
        _env_file=None,
        environment="production",
        auth_mode="jwt",
        jwt_secret=TEST_SECRET,
        storage_dir=managed_storage,
        max_upload_bytes=20_000,
        max_pdf_pages=2,
        max_render_pixels=1_000_000,
    )

    for module in (auth, routes, storage, pdf):
        monkeypatch.setattr(module, "get_settings", lambda: settings)

    real_temporary_file = tempfile.NamedTemporaryFile

    def quarantined_file(**kwargs: Any):
        return real_temporary_file(dir=quarantine, **kwargs)

    monkeypatch.setattr(storage.tempfile, "NamedTemporaryFile", quarantined_file)

    application = FastAPI()
    application.include_router(routes.router)
    with TestClient(application) as client:
        yield client, managed_storage, quarantine


def test_jwt_role_enforcement(security_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = security_client
    monkeypatch.setattr(routes.repository, "list_jobs", lambda limit: [])
    monkeypatch.setattr(routes.repository, "import_purchase_orders", lambda data: {"imported": 1})
    monkeypatch.setattr(
        routes.repository,
        "create_document_and_job",
        lambda **kwargs: (
            {
                "job_id": "job-1",
                "document_id": "document-1",
                "file_name": kwargs["file_name"],
                "status": "PENDING",
                "attempts": 0,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
            True,
        ),
    )
    monkeypatch.setattr(
        routes.review,
        "resolve_review",
        lambda **kwargs: {"decision_status": "REJECTED", "reasons": [], "allocation_id": None},
    )

    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/jobs", headers=bearer("viewer")).status_code == 200
    assert client.post(
        "/api/invoices/upload",
        headers=bearer("viewer"),
        files={"file": ("invoice.pdf", make_pdf(), "text/plain")},
    ).status_code == 403
    assert client.post(
        "/api/invoices/upload",
        headers=bearer("operator"),
        files={"file": ("invoice.pdf", make_pdf(), "text/plain")},
    ).status_code == 202
    assert client.post(
        "/api/jobs/job-1/review/resolve",
        headers=bearer("operator"),
        json={"action": "REJECT", "note": "No."},
    ).status_code == 403
    assert client.post(
        "/api/jobs/job-1/review/resolve",
        headers=bearer("reviewer"),
        json={"action": "REJECT", "note": "No."},
    ).status_code == 200
    assert client.post(
        "/api/purchase-orders/import",
        headers=bearer("reviewer"),
        files={"file": ("purchase-orders.csv", b"po_number\nPO-1\n", "text/csv")},
    ).status_code == 403
    assert client.post(
        "/api/purchase-orders/import",
        headers=bearer("admin"),
        files={"file": ("purchase-orders.csv", b"po_number\nPO-1\n", "text/csv")},
    ).status_code == 200


def test_spoofed_audit_names_are_ignored(security_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = security_client
    captured_review: dict[str, Any] = {}
    captured_retry: dict[str, Any] = {}

    def resolve_review(**kwargs: Any) -> dict[str, Any]:
        captured_review.update(kwargs)
        return {"decision_status": "REJECTED", "reasons": [], "allocation_id": None}

    def retry_job(job_id: str, **kwargs: Any) -> dict[str, Any]:
        captured_retry.update({"job_id": job_id, **kwargs})
        return {"job_id": job_id, "retry_generation": 1, "manual_retry_count": 1}

    monkeypatch.setattr(routes.review, "resolve_review", resolve_review)
    monkeypatch.setattr(routes.repository, "retry_job", retry_job)
    headers = bearer("reviewer", subject="trusted-42", name="Trusted Reviewer")

    review_response = client.post(
        "/api/jobs/job-1/review/resolve",
        headers=headers,
        json={"action": "REJECT", "note": "Rejected.", "reviewer_name": "Spoofed Name"},
    )
    retry_response = client.post(
        "/api/jobs/job-1/retry",
        headers=headers,
        json={"note": "Retry.", "requested_by": "Spoofed Operator"},
    )

    assert review_response.status_code == 200
    assert retry_response.status_code == 200
    assert captured_review["reviewer_name"] == "Trusted Reviewer"
    assert captured_review["actor_id"] == "trusted-42"
    assert captured_review["actor_role"] == "reviewer"
    assert captured_retry["requested_by"] == "Trusted Reviewer"
    assert captured_retry["actor_id"] == "trusted-42"
    assert captured_retry["actor_role"] == "reviewer"


def test_authenticated_actor_is_persisted_for_review_and_retry(
    security_client,
    make_job,
    fail_terminally,
) -> None:
    client, _, _ = security_client
    headers = bearer("reviewer", subject="trusted-99", name="Stored Reviewer")

    review_job_id, review_document_id = make_job()
    repository.finalize_invoice_decision(
        job_id=review_job_id,
        document_id=review_document_id,
        decision_status="NEEDS_REVIEW",
        extraction=extraction("100"),
        matched_po=None,
        reasons=["A reviewer must decide."],
        rule_checks={},
    )
    review_response = client.post(
        f"/api/jobs/{review_job_id}/review/resolve",
        headers=headers,
        json={"action": "REJECT", "note": "Rejected.", "reviewer_name": "Spoofed Name"},
    )

    retry_job_id, _ = make_job()
    fail_terminally(retry_job_id)
    retry_response = client.post(
        f"/api/jobs/{retry_job_id}/retry",
        headers=headers,
        json={"note": "Retry.", "requested_by": "Spoofed Operator"},
    )

    assert review_response.status_code == 200
    assert retry_response.status_code == 200
    action = repository.get_job_detail(review_job_id)["review_actions"][-1]
    retried = repository.get_job(retry_job_id)
    assert (action["reviewer_name"], action["actor_id"], action["actor_role"]) == (
        "Stored Reviewer",
        "trusted-99",
        "reviewer",
    )
    assert (
        retried["last_retry_by"],
        retried["last_retry_actor_id"],
        retried["last_retry_actor_role"],
    ) == ("Stored Reviewer", "trusted-99", "reviewer")


def persistent_counts(db) -> dict[str, int]:
    tables = (
        "invoice_documents",
        "invoice_jobs",
        "invoice_events",
        "invoice_identity_claims",
        "po_invoice_allocations",
    )
    with db() as connection, connection.cursor() as cursor:
        return {
            table: cursor.execute(f"SELECT count(*) AS count FROM {table}").fetchone()["count"]
            for table in tables
        }


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        (b"x" * 20_001, 413),
        (b"definitely not a PDF", 422),
        (b"%PDF-1.7\ntruncated", 422),
        (make_encrypted_pdf(), 422),
        (make_pdf(pages=3), 422),
        (make_pdf(width=1_000, height=1_000), 422),
    ],
    ids=["oversized", "fake", "malformed", "encrypted", "over-page", "over-render"],
)
def test_rejected_uploads_create_zero_state(
    security_client,
    monkeypatch: pytest.MonkeyPatch,
    db,
    payload: bytes,
    expected_status: int,
) -> None:
    client, managed_storage, quarantine = security_client
    before = persistent_counts(db)
    persist_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        routes.repository,
        "create_document_and_job",
        lambda **kwargs: persist_calls.append(kwargs),
    )

    response = client.post(
        "/api/invoices/upload",
        headers=bearer("operator"),
        files={"file": ("../../invoice.exe", payload, "application/pdf")},
    )

    assert response.status_code == expected_status
    assert persist_calls == []
    assert persistent_counts(db) == before
    assert list(managed_storage.iterdir()) == []
    assert list(quarantine.iterdir()) == []


def test_path_traversal_filename_stays_inside_managed_storage(
    security_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, managed_storage, _ = security_client
    captured: dict[str, Any] = {}

    def create(**kwargs: Any):
        captured.update(kwargs)
        return (
            {
                "job_id": "job-safe",
                "document_id": "document-safe",
                "file_name": kwargs["file_name"],
                "status": "PENDING",
                "attempts": 0,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
            True,
        )

    monkeypatch.setattr(routes.repository, "create_document_and_job", create)
    response = client.post(
        "/api/invoices/upload",
        headers=bearer("operator"),
        files={"file": ("../../outside/escape", make_pdf(), "text/plain")},
    )

    assert response.status_code == 202
    assert captured["file_name"] == "escape.pdf"
    assert captured["content_type"] == "application/pdf"
    stored = storage.file_path(captured["storage_key"])
    assert stored.parent == managed_storage.resolve()
    assert stored.exists()
    assert not (managed_storage.parent / "escape.pdf").exists()


def test_document_download_requires_viewer_and_has_safe_headers(
    security_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, managed_storage, _ = security_client
    stored = managed_storage / "server-generated.pdf"
    stored.write_bytes(make_pdf())
    monkeypatch.setattr(
        routes.repository,
        "get_document",
        lambda document_id: {
            "document_id": document_id,
            "storage_key": stored.name,
            "file_name": "../../invoice\r\nX-Injected: true",
            "content_type": "text/html",
        },
    )

    assert client.get("/api/documents/document-1/file").status_code == 401
    response = client.get("/api/documents/document-1/file", headers=bearer("viewer"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert "X-Injected" not in response.headers
    assert ".." not in response.headers["content-disposition"]


def test_development_auth_is_refused_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        auth_mode="development",
        storage_dir=Path(tempfile.gettempdir()) / "invoice-security-test",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="allowed only"):
        auth.validate_auth_configuration()


def test_structured_logging_redacts_tokens_and_document_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: list[str] = []
    monkeypatch.setattr(observability.logger, "info", records.append)

    observability.log(
        "security_test",
        Authorization="Bearer secret.jwt.value",
        refresh_token="also-secret",
        upload_bytes=b"%PDF-secret",
        nested={"raw_text": "invoice contents", "model_output": "{\"secret\": true}"},
        safe_field="kept",
    )

    assert len(records) == 1
    assert "secret.jwt.value" not in records[0]
    assert "invoice contents" not in records[0]
    assert '"safe_field": "kept"' in records[0]
    assert records[0].count("[REDACTED]") == 5
