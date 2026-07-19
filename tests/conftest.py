"""Fixtures for the tests that exercise real PostgreSQL transactions.

These tests need the database from docker-compose; they are skipped when it is unreachable.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.pipeline.normalizer import normalize_name


@pytest.fixture(scope="session", autouse=True)
def schema():
    pg = pytest.importorskip("app.db.pg")
    try:
        pg.init_schema()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"PostgreSQL is unavailable: {exc}")


@pytest.fixture
def db():
    from app.db.pg import connection

    return connection


@pytest.fixture
def make_po(db):
    def _make(total: str = "1000", consumed: str = "0", status: str = "OPEN", vendor: str = "Globex Ltd") -> str:
        po_number = f"PO-TEST-{uuid.uuid4().hex[:10].upper()}"
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO purchase_orders
                      (po_number, vendor_name, vendor_normalized, currency, total_amount, consumed_amount, status)
                    VALUES (%s, %s, %s, 'INR', %s, %s, %s)
                    """,
                    (po_number, vendor, normalize_name(vendor), Decimal(total), Decimal(consumed), status),
                )
            conn.commit()
        return po_number

    return _make


@pytest.fixture
def make_job(db):
    """A queued document/job pair, bypassing file upload."""

    def _make() -> tuple[str, str]:
        document_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO invoice_documents
                      (document_id, file_name, storage_key, sha256, content_type, byte_size, page_count)
                    VALUES (%s, 'test.pdf', %s, %s, 'application/pdf', 1024, 1)
                    """,
                    (document_id, f"test/{document_id}.pdf", uuid.uuid4().hex),
                )
                cur.execute(
                    """
                    INSERT INTO invoice_jobs (job_id, document_id, policy_version, status)
                    VALUES (%s, %s, %s, 'PROCESSING')
                    """,
                    (job_id, document_id, get_settings().policy_version),
                )
            conn.commit()
        return job_id, document_id

    return _make


@pytest.fixture
def fail_terminally(db):
    """Drive a job to FAILED: fail_job only gives up once attempts reach max_attempts."""

    def _fail(job_id: str, error: str = "MEDHA timed out") -> None:
        from app.db import repository

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE invoice_jobs SET attempts = max_attempts WHERE job_id = %s", (job_id,)
                )
            conn.commit()
        repository.fail_job(job_id, error)

    return _fail


@pytest.fixture
def po_row(db):
    def _read(po_number: str) -> dict:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM purchase_orders WHERE po_number = %s", (po_number,))
            return cur.fetchone()

    return _read


@pytest.fixture
def allocations(db):
    def _read(po_number: str) -> list[dict]:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM po_invoice_allocations WHERE po_number = %s ORDER BY created_at",
                (po_number,),
            )
            return list(cur.fetchall())

    return _read
