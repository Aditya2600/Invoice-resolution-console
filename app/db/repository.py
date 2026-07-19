from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from app.core.config import get_settings
from app.core.schemas import JobStatus, PurchaseOrder, StageStatus
from app.db.pg import connection
from app.pipeline.normalizer import normalize_invoice_number, normalize_name


def _json(value: Any) -> Jsonb:
    def default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        raise TypeError(f"Cannot JSON-serialize {type(item)!r}")

    return Jsonb(value, dumps=lambda object_: json.dumps(object_, default=default))


def create_document_and_job(
    *,
    file_name: str,
    storage_key: str,
    sha256: str,
    content_type: str,
    byte_size: int,
    page_count: int | None,
) -> tuple[dict[str, Any], bool]:
    """Create the document/job, or return the existing job for the same file hash."""
    settings = get_settings()
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT j.*, d.file_name
                FROM invoice_documents d
                JOIN invoice_jobs j ON j.document_id = d.document_id
                WHERE d.sha256 = %s
                ORDER BY j.created_at DESC
                LIMIT 1
                """,
                (sha256,),
            )
            existing = cur.fetchone()
            if existing:
                return existing, False

            document_id = str(uuid.uuid4())
            job_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO invoice_documents
                  (document_id, file_name, storage_key, sha256, content_type, byte_size, page_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (document_id, file_name, storage_key, sha256, content_type, byte_size, page_count),
            )
            cur.execute(
                """
                INSERT INTO invoice_jobs
                  (job_id, document_id, policy_version, status, max_attempts)
                VALUES (%s, %s, %s, 'PENDING', %s)
                """,
                (job_id, document_id, settings.policy_version, settings.job_max_attempts),
            )
            conn.commit()
            return {
                "job_id": job_id,
                "document_id": document_id,
                "file_name": file_name,
                "status": JobStatus.PENDING,
                "attempts": 0,
                "created_at": datetime.now().astimezone(),
                "updated_at": datetime.now().astimezone(),
            }, True


def claim_one_job() -> dict[str, Any] | None:
    settings = get_settings()
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH candidate AS (
                    SELECT job_id
                    FROM invoice_jobs
                    WHERE status = 'PENDING'
                       OR (status = 'PROCESSING' AND lease_until < now())
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE invoice_jobs j
                SET status = 'PROCESSING',
                    attempts = attempts + 1,
                    lease_until = now() + make_interval(secs => %s),
                    updated_at = now()
                FROM candidate
                WHERE j.job_id = candidate.job_id
                RETURNING j.*
                """,
                (settings.job_lease_seconds,),
            )
            job = cur.fetchone()
            conn.commit()
            return job


def get_document(document_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoice_documents WHERE document_id = %s", (document_id,))
            return cur.fetchone()


def get_job(job_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT j.*, d.file_name, d.storage_key, d.sha256, d.page_count
                FROM invoice_jobs j JOIN invoice_documents d ON d.document_id = j.document_id
                WHERE j.job_id = %s
                """,
                (job_id,),
            )
            return cur.fetchone()


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT j.job_id, j.document_id, d.file_name, j.status, j.decision_status,
                       j.attempts, j.created_at, j.updated_at
                FROM invoice_jobs j JOIN invoice_documents d ON d.document_id = j.document_id
                ORDER BY j.created_at DESC LIMIT %s
                """,
                (limit,),
            )
            return list(cur.fetchall())


def get_job_detail(job_id: str) -> dict[str, Any] | None:
    job = get_job(job_id)
    if not job:
        return None
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoice_events WHERE job_id = %s ORDER BY id", (job_id,))
            events = list(cur.fetchall())
            cur.execute("SELECT * FROM invoice_results WHERE job_id = %s", (job_id,))
            result = cur.fetchone()
    return {"job": job, "events": events, "result": result}


def log_event(
    job_id: str,
    stage: str,
    status: StageStatus | str,
    *,
    reason: str | None = None,
    ms: float | None = None,
    metrics: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO invoice_events (job_id, stage, status, reason, ms, metrics, data)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (job_id, stage, str(status), reason, ms, _json(metrics or {}), _json(data or {})),
            )
        conn.commit()


def complete_job(
    *,
    job_id: str,
    document_id: str,
    decision_status: str,
    extraction: dict[str, Any],
    matched_po: dict[str, Any] | None,
    reasons: list[str],
    rule_checks: dict[str, Any],
    model_name: str | None,
    model_latency_ms: float | None,
) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO invoice_results
                  (document_id, job_id, decision_status, extraction, matched_po, reasons,
                   rule_checks, model_name, model_latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (document_id) DO UPDATE SET
                  decision_status = EXCLUDED.decision_status,
                  extraction = EXCLUDED.extraction,
                  matched_po = EXCLUDED.matched_po,
                  reasons = EXCLUDED.reasons,
                  rule_checks = EXCLUDED.rule_checks,
                  model_name = EXCLUDED.model_name,
                  model_latency_ms = EXCLUDED.model_latency_ms,
                  updated_at = now()
                """,
                (
                    document_id,
                    job_id,
                    decision_status,
                    _json(extraction),
                    _json(matched_po) if matched_po else None,
                    _json(reasons),
                    _json(rule_checks),
                    model_name,
                    model_latency_ms,
                ),
            )
            cur.execute(
                """
                UPDATE invoice_jobs
                SET status = 'COMPLETED', decision_status = %s, lease_until = NULL,
                    last_error = NULL, updated_at = now()
                WHERE job_id = %s
                """,
                (decision_status, job_id),
            )
        conn.commit()


def fail_job(job_id: str, error: str) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT attempts, max_attempts FROM invoice_jobs WHERE job_id = %s", (job_id,))
            job = cur.fetchone()
            if not job:
                return
            status = "FAILED" if job["attempts"] >= job["max_attempts"] else "PENDING"
            cur.execute(
                """
                UPDATE invoice_jobs
                SET status = %s, lease_until = NULL, last_error = %s, updated_at = now()
                WHERE job_id = %s
                """,
                (status, error[:2000], job_id),
            )
        conn.commit()


def extend_lease(job_id: str) -> None:
    settings = get_settings()
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE invoice_jobs
                SET lease_until = now() + make_interval(secs => %s), updated_at = now()
                WHERE job_id = %s AND status = 'PROCESSING'
                """,
                (settings.job_lease_seconds, job_id),
            )
        conn.commit()


def import_purchase_orders(csv_bytes: bytes) -> dict[str, int]:
    rows = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")))
    imported = 0
    with connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                po_number = (row.get("po_number") or "").strip()
                vendor_name = (row.get("vendor_name") or "").strip()
                if not po_number or not vendor_name:
                    continue
                cur.execute(
                    """
                    INSERT INTO purchase_orders
                      (po_number, vendor_name, vendor_normalized, currency,
                       total_amount, consumed_amount, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (po_number) DO UPDATE SET
                      vendor_name = EXCLUDED.vendor_name,
                      vendor_normalized = EXCLUDED.vendor_normalized,
                      currency = EXCLUDED.currency,
                      total_amount = EXCLUDED.total_amount,
                      consumed_amount = EXCLUDED.consumed_amount,
                      status = EXCLUDED.status,
                      updated_at = now()
                    """,
                    (
                        po_number.upper(),
                        vendor_name,
                        normalize_name(vendor_name),
                        (row.get("currency") or "INR").upper().strip(),
                        Decimal(row.get("total_amount") or "0"),
                        Decimal(row.get("consumed_amount") or "0"),
                        (row.get("status") or "OPEN").upper().strip(),
                    ),
                )
                imported += 1
        conn.commit()
    return {"imported": imported}


def find_purchase_orders(*, po_number: str | None, vendor_name: str | None) -> list[PurchaseOrder]:
    with connection() as conn:
        with conn.cursor() as cur:
            if po_number:
                cur.execute("SELECT * FROM purchase_orders WHERE po_number = %s", (po_number.upper(),))
            elif vendor_name:
                cur.execute(
                    """
                    SELECT * FROM purchase_orders
                    WHERE vendor_normalized = %s AND status = 'OPEN'
                    ORDER BY created_at
                    """,
                    (normalize_name(vendor_name),),
                )
            else:
                return []
            return [
                PurchaseOrder(
                    po_number=row["po_number"],
                    vendor_name=row["vendor_name"],
                    currency=row["currency"],
                    total_amount=row["total_amount"],
                    consumed_amount=row["consumed_amount"],
                    status=row["status"],
                )
                for row in cur.fetchall()
            ]


def reserve_po_amount(po_number: str, amount: Decimal) -> bool:
    """Atomically reserve the approved amount to avoid concurrent over-allocation."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE purchase_orders
                SET consumed_amount = consumed_amount + %s, updated_at = now()
                WHERE po_number = %s
                  AND status = 'OPEN'
                  AND total_amount - consumed_amount >= %s
                RETURNING po_number
                """,
                (amount, po_number, amount),
            )
            reserved = cur.fetchone() is not None
        conn.commit()
        return reserved


def claim_semantic_invoice(
    *, document_id: str, vendor_name: str, invoice_number: str, total: Decimal | None
) -> dict[str, Any] | None:
    """Return a prior invoice identity when this is a semantic duplicate; otherwise claim it."""
    vendor = normalize_name(vendor_name)
    invoice = normalize_invoice_number(invoice_number)
    if not vendor or not invoice:
        return None
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO invoice_duplicates
                  (vendor_normalized, invoice_number_normalized, first_document_id, first_total)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (vendor_normalized, invoice_number_normalized) DO NOTHING
                RETURNING first_document_id, first_total
                """,
                (vendor, invoice, document_id, total),
            )
            inserted = cur.fetchone()
            if inserted:
                conn.commit()
                return None
            cur.execute(
                """
                SELECT first_document_id, first_total
                FROM invoice_duplicates
                WHERE vendor_normalized = %s AND invoice_number_normalized = %s
                """,
                (vendor, invoice),
            )
            prior = cur.fetchone()
        conn.commit()
        if prior and prior["first_document_id"] == document_id:
            return None
        return prior
