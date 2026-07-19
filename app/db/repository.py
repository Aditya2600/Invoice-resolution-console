from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from app.core import observability
from app.core.config import get_settings
from app.core.schemas import JobStatus, PurchaseOrder, StageStatus, merge_corrections
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
                       j.attempts, j.created_at, j.updated_at,
                       r.extraction->>'vendor_name' AS vendor_name,
                       r.extraction->>'total'       AS total,
                       r.extraction->>'currency'    AS currency
                FROM invoice_jobs j
                JOIN invoice_documents d ON d.document_id = j.document_id
                -- LEFT so in-flight runs, which have no result row yet, still list.
                LEFT JOIN invoice_results r ON r.job_id = j.job_id
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
            cur.execute("SELECT * FROM invoice_review_actions WHERE job_id = %s ORDER BY id", (job_id,))
            review_actions = list(cur.fetchall())
            cur.execute(
                "SELECT * FROM po_invoice_allocations WHERE job_id = %s ORDER BY created_at",
                (job_id,),
            )
            allocations = list(cur.fetchall())
    # The original model extraction stays on the result row untouched; this is the merged view
    # the last reviewer actually validated against.
    corrections = review_actions[-1]["corrections"] if review_actions else {}
    return {
        "job": job,
        "events": events,
        "result": result,
        "effective_extraction": merge_corrections(result["extraction"], corrections) if result else None,
        "review_actions": review_actions,
        "allocations": allocations,
    }


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
    observability.record_stage(job_id=job_id, stage=stage, outcome=str(status), duration_ms=ms)


class ReviewConflict(RuntimeError):
    """The job is no longer awaiting review, so this resolution cannot be applied."""


def _insert_event(cur, job_id: str, stage: str, status: str, reason: str | None, ms: float | None, data: dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO invoice_events (job_id, stage, status, reason, ms, metrics, data)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (job_id, stage, status, reason, ms, _json({}), _json(data)),
    )
    # Recorded from inside the finalize transaction. A rollback loses the row but keeps the
    # counter, which is the harmless direction: metrics over-report, the audit trail never does.
    observability.record_stage(job_id=job_id, stage=stage, outcome=status, duration_ms=ms)


def finalize_invoice_decision(
    *,
    job_id: str,
    document_id: str,
    decision_status: str,
    extraction: dict[str, Any],
    matched_po: dict[str, Any] | None,
    reasons: list[str],
    rule_checks: dict[str, Any],
    model_name: str | None = None,
    model_latency_ms: float | None = None,
    allocation_amount: Decimal | None = None,
    total_ms: float | None = None,
    review_action: dict[str, Any] | None = None,
    policy_snapshot: dict[str, Any] | None = None,
    policy_hash: str | None = None,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Consume PO balance, write the result, close the job and its audit trail in one transaction.

    When the purchase-order balance no longer covers the invoice at commit time, the decision is
    downgraded to NEEDS_REVIEW: no allocation is created and no balance is consumed. Either the
    whole finalization lands or none of it does.
    """
    final_status = decision_status
    final_reasons = list(reasons)
    checks = dict(rule_checks)
    allocation_id: str | None = None

    with connection() as conn:
        with conn.cursor() as cur:
            if review_action is not None:
                cur.execute(
                    "SELECT decision_status FROM invoice_jobs WHERE job_id = %s FOR UPDATE",
                    (job_id,),
                )
                current = cur.fetchone()
                if not current:
                    raise ReviewConflict("Job not found.")
                if current["decision_status"] != "NEEDS_REVIEW":
                    raise ReviewConflict(
                        f"This invoice is already resolved as {current['decision_status']}."
                    )

            # Corrected vendor/invoice number re-claims the identity in the same transaction as
            # the allocation, so an invoice can never be approved onto an identity it lost.
            if identity is not None:
                _migrate_identity_claim(
                    cur,
                    document_id,
                    job_id=job_id,
                    vendor_normalized=identity["vendor_normalized"],
                    invoice_number_normalized=identity["invoice_number_normalized"],
                    total=identity.get("total"),
                )

            if final_status == "APPROVED" and matched_po and allocation_amount is not None:
                cur.execute(
                    "SELECT total_amount, consumed_amount, status FROM purchase_orders WHERE po_number = %s FOR UPDATE",
                    (matched_po["po_number"],),
                )
                po = cur.fetchone()
                cur.execute(
                    "SELECT allocation_id FROM po_invoice_allocations WHERE document_id = %s AND status = 'ACTIVE'",
                    (document_id,),
                )
                existing = cur.fetchone()
                if existing:
                    # Already consumed by an earlier finalization; never double-consume.
                    allocation_id = existing["allocation_id"]
                    checks["atomic_po_reservation"] = {"passed": True, "allocation_id": allocation_id}
                elif not po or po["status"].upper() != "OPEN" or po["total_amount"] - po["consumed_amount"] < allocation_amount:
                    final_status = "NEEDS_REVIEW"
                    final_reasons = [
                        *final_reasons,
                        "Purchase-order balance changed while processing; reviewer must recheck the invoice.",
                    ]
                    checks["atomic_po_reservation"] = {"passed": False}
                else:
                    allocation_id = str(uuid.uuid4())
                    cur.execute(
                        """
                        INSERT INTO po_invoice_allocations
                          (allocation_id, po_number, document_id, job_id, amount, status)
                        VALUES (%s, %s, %s, %s, %s, 'ACTIVE')
                        """,
                        (allocation_id, matched_po["po_number"], document_id, job_id, allocation_amount),
                    )
                    cur.execute(
                        """
                        UPDATE purchase_orders
                        SET consumed_amount = consumed_amount + %s, updated_at = now()
                        WHERE po_number = %s
                        """,
                        (allocation_amount, matched_po["po_number"]),
                    )
                    checks["atomic_po_reservation"] = {"passed": True, "allocation_id": allocation_id}

            _write_result_and_close(
                cur,
                job_id=job_id,
                document_id=document_id,
                decision_status=final_status,
                extraction=extraction,
                matched_po=matched_po,
                reasons=final_reasons,
                rule_checks=checks,
                model_name=model_name,
                model_latency_ms=model_latency_ms,
                policy_snapshot=policy_snapshot,
                policy_hash=policy_hash,
            )

            # APPROVED locks the identity for good; a rejection frees it for a corrected
            # re-upload; NEEDS_REVIEW keeps the claim PENDING until a human resolves it.
            if final_status == "APPROVED":
                _settle_identity_claim(cur, document_id, state="FINAL", reason=None)
            elif final_status == "REJECTED":
                _settle_identity_claim(
                    cur, document_id, state="RELEASED", reason=f"Invoice {final_status.lower()}."
                )

            if review_action is not None:
                cur.execute(
                    """
                    INSERT INTO invoice_review_actions
                      (job_id, reviewer_name, action, selected_po_number, corrections, note,
                       decision_before, decision_after)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        review_action["reviewer_name"],
                        review_action["action"],
                        review_action.get("selected_po_number"),
                        _json(review_action.get("corrections") or {}),
                        review_action["note"],
                        review_action.get("decision_before"),
                        final_status,
                    ),
                )
                _insert_event(
                    cur,
                    job_id,
                    "stage_review_resolve",
                    "PASS",
                    f"{review_action['reviewer_name']} chose to {review_action['action'].lower()} this invoice.",
                    None,
                    {
                        "reviewer_name": review_action["reviewer_name"],
                        "action": review_action["action"],
                        "note": review_action["note"],
                        "selected_po_number": review_action.get("selected_po_number"),
                        "corrections": review_action.get("corrections") or {},
                        # Field-by-field before/after, so a correction is auditable without
                        # having to diff the result row against the review action.
                        "corrections_detail": review_action.get("corrections_detail") or [],
                        "reviewer_attestation": review_action.get("reviewer_attestation", False),
                        "decision_before": review_action.get("decision_before"),
                        "decision_after": final_status,
                        "policy_version": (policy_snapshot or {}).get("policy_version"),
                        "policy_hash": policy_hash,
                    },
                )

            _insert_event(
                cur,
                job_id,
                "stage_policy_validate",
                "PASS" if final_status == "APPROVED" else "INFO",
                "; ".join(final_reasons),
                None,
                {
                    "decision": {
                        "status": final_status,
                        "reasons": final_reasons,
                        "matched_po": matched_po,
                        "rule_checks": checks,
                    },
                    "allocation_id": allocation_id,
                    "policy_version": (policy_snapshot or {}).get("policy_version"),
                    "policy_hash": policy_hash,
                },
            )
            _insert_event(
                cur,
                job_id,
                "invoice_closed",
                "PASS",
                f"Invoice processing completed as {final_status}.",
                total_ms,
                {},
            )
        conn.commit()

    observability.count("invoice_decisions_total", decision=final_status)
    return {
        "decision_status": final_status,
        "reasons": final_reasons,
        "rule_checks": checks,
        "allocation_id": allocation_id,
    }


def list_allocations(job_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM po_invoice_allocations WHERE job_id = %s ORDER BY created_at",
                (job_id,),
            )
            return list(cur.fetchall())


def list_review_actions(job_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM invoice_review_actions WHERE job_id = %s ORDER BY id",
                (job_id,),
            )
            return list(cur.fetchall())


def _write_result_and_close(
    cur,
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
    policy_snapshot: dict[str, Any] | None = None,
    policy_hash: str | None = None,
) -> None:
    cur.execute(
                """
                INSERT INTO invoice_results
                  (document_id, job_id, decision_status, extraction, matched_po, reasons,
                   rule_checks, model_name, model_latency_ms, policy_snapshot, policy_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (document_id) DO UPDATE SET
                  decision_status = EXCLUDED.decision_status,
                  extraction = EXCLUDED.extraction,
                  matched_po = EXCLUDED.matched_po,
                  reasons = EXCLUDED.reasons,
                  rule_checks = EXCLUDED.rule_checks,
                  model_name = EXCLUDED.model_name,
                  model_latency_ms = EXCLUDED.model_latency_ms,
                  policy_snapshot = EXCLUDED.policy_snapshot,
                  policy_hash = EXCLUDED.policy_hash,
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
                    _json(policy_snapshot) if policy_snapshot else None,
                    policy_hash,
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


def fail_job(job_id: str, error: str) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT attempts, max_attempts, document_id FROM invoice_jobs WHERE job_id = %s",
                (job_id,),
            )
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
            if status == "FAILED":
                # The run never reached a decision, so it must not hold the identity hostage.
                _settle_identity_claim(
                    cur, job["document_id"], state="RELEASED", reason="Processing failed."
                )
        conn.commit()
    if status == "FAILED":
        observability.count("invoice_jobs_failed_total")
        observability.log(
            "job_failed", job_id=job_id, document_id=job["document_id"], outcome="FAILED",
            error_code=error[:120],
        )


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


def claim_semantic_invoice(
    *,
    document_id: str,
    job_id: str | None = None,
    vendor_name: str,
    invoice_number: str,
    total: Decimal | None,
) -> dict[str, Any] | None:
    """Claim the vendor/invoice-number identity, or describe the live claim that blocks it.

    The partial unique index on (vendor, invoice number) WHERE state IN ('PENDING','FINAL') is what
    makes this race-safe: two workers racing on the same identity, only one insert survives and the
    loser reads the winner's claim.
    """
    vendor = normalize_name(vendor_name)
    invoice = normalize_invoice_number(invoice_number)
    if not vendor or not invoice:
        return None
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO invoice_identity_claims
                  (claim_id, vendor_normalized, invoice_number_normalized, document_id, job_id,
                   invoice_total, state)
                VALUES (%s, %s, %s, %s, %s, %s, 'PENDING')
                ON CONFLICT (vendor_normalized, invoice_number_normalized)
                    WHERE state IN ('PENDING', 'FINAL')
                DO NOTHING
                RETURNING claim_id
                """,
                (str(uuid.uuid4()), vendor, invoice, document_id, job_id, total),
            )
            inserted = cur.fetchone()
            if inserted:
                conn.commit()
                return None
            cur.execute(
                """
                SELECT document_id AS first_document_id, invoice_total AS first_total, state, claim_id
                FROM invoice_identity_claims
                WHERE vendor_normalized = %s AND invoice_number_normalized = %s
                  AND state IN ('PENDING', 'FINAL')
                """,
                (vendor, invoice),
            )
            prior = cur.fetchone()
        conn.commit()
        if prior and prior["first_document_id"] == document_id:
            # This document already holds the identity, e.g. a retried run. Not a duplicate.
            return None
        return prior


class IdentityConflict(RuntimeError):
    """Another live invoice already holds the corrected vendor + invoice number."""


def _migrate_identity_claim(
    cur,
    document_id: str,
    *,
    job_id: str,
    vendor_normalized: str,
    invoice_number_normalized: str,
    total: Decimal | None,
) -> None:
    """Move this document's claim onto a corrected identity: acquire first, release afterwards.

    Ordering matters. The new claim is taken before the old one is freed, so a failure anywhere
    leaves the document still holding its original identity rather than holding none. The partial
    unique index means a racing worker either loses the insert or is seen by the FOR UPDATE read.
    """
    if not vendor_normalized or not invoice_number_normalized:
        return

    cur.execute(
        """
        SELECT vendor_normalized, invoice_number_normalized FROM invoice_identity_claims
        WHERE document_id = %s AND state IN ('PENDING', 'FINAL')
        FOR UPDATE
        """,
        (document_id,),
    )
    held = cur.fetchone()
    if not held:
        # This document holds no identity, so there is nothing to migrate. Claiming one for the
        # first time belongs to the processing path, not to review resolution.
        return
    if (held["vendor_normalized"], held["invoice_number_normalized"]) == (
        vendor_normalized,
        invoice_number_normalized,
    ):
        return

    cur.execute(
        """
        SELECT claim_id, document_id FROM invoice_identity_claims
        WHERE vendor_normalized = %s AND invoice_number_normalized = %s
          AND state IN ('PENDING', 'FINAL')
        FOR UPDATE
        """,
        (vendor_normalized, invoice_number_normalized),
    )
    holder = cur.fetchone()
    if holder and holder["document_id"] != document_id:
        raise IdentityConflict(
            "Another invoice is already recorded with this vendor and invoice number."
        )
    if not holder:
        cur.execute(
            """
            INSERT INTO invoice_identity_claims
              (claim_id, vendor_normalized, invoice_number_normalized, document_id, job_id,
               invoice_total, state)
            VALUES (%s, %s, %s, %s, %s, %s, 'PENDING')
            ON CONFLICT (vendor_normalized, invoice_number_normalized)
                WHERE state IN ('PENDING', 'FINAL')
            DO NOTHING
            RETURNING claim_id
            """,
            (str(uuid.uuid4()), vendor_normalized, invoice_number_normalized, document_id, job_id, total),
        )
        if cur.fetchone() is None:
            # Lost the race to a worker that claimed this identity between the read and the insert.
            raise IdentityConflict(
                "Another invoice is already recorded with this vendor and invoice number."
            )

    cur.execute(
        """
        UPDATE invoice_identity_claims
        SET state = 'RELEASED', released_at = now(), release_reason = 'Reviewer corrected the invoice identity.'
        WHERE document_id = %s AND state IN ('PENDING', 'FINAL')
          AND NOT (vendor_normalized = %s AND invoice_number_normalized = %s)
        """,
        (document_id, vendor_normalized, invoice_number_normalized),
    )


def _settle_identity_claim(cur, document_id: str, *, state: str, reason: str | None) -> None:
    """Move this document's live claim to FINAL or RELEASED. NEEDS_REVIEW keeps it PENDING."""
    if state == "FINAL":
        cur.execute(
            """
            UPDATE invoice_identity_claims
            SET state = 'FINAL', finalized_at = now(), released_at = NULL, release_reason = NULL
            WHERE document_id = %s AND state = 'PENDING'
            """,
            (document_id,),
        )
        return
    cur.execute(
        """
        UPDATE invoice_identity_claims
        SET state = 'RELEASED', released_at = now(), release_reason = %s
        WHERE document_id = %s AND state IN ('PENDING', 'FINAL')
        """,
        (reason, document_id),
    )


def release_identity_claim(document_id: str, reason: str) -> None:
    """Free the identity so a corrected re-upload can claim it."""
    with connection() as conn:
        with conn.cursor() as cur:
            _settle_identity_claim(cur, document_id, state="RELEASED", reason=reason)
        conn.commit()


def get_identity_claim(document_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM invoice_identity_claims
                WHERE document_id = %s ORDER BY created_at DESC LIMIT 1
                """,
                (document_id,),
            )
            return cur.fetchone()


class RetryConflict(RuntimeError):
    """Only a FAILED job may be retried."""


def retry_job(job_id: str, *, requested_by: str, note: str | None = None) -> dict[str, Any]:
    """Re-queue a failed job under a new retry generation, preserving all history.

    Attempts reset so the new run gets a full budget; retry_generation and manual_retry_count
    only ever increase, so the audit trail of how often a job was re-run survives.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, decision_status, retry_generation FROM invoice_jobs WHERE job_id = %s FOR UPDATE",
                (job_id,),
            )
            job = cur.fetchone()
            if not job:
                raise LookupError("Job not found.")
            if job["status"] != "FAILED":
                raise RetryConflict(
                    f"Only a failed run can be retried; this one is {job['decision_status'] or job['status']}."
                )
            cur.execute(
                """
                UPDATE invoice_jobs
                SET status = 'PENDING',
                    attempts = 0,
                    retry_generation = retry_generation + 1,
                    manual_retry_count = manual_retry_count + 1,
                    last_retry_at = now(),
                    last_retry_by = %s,
                    lease_until = NULL,
                    last_error = NULL,
                    updated_at = now()
                WHERE job_id = %s
                RETURNING retry_generation, manual_retry_count
                """,
                (requested_by, job_id),
            )
            updated = cur.fetchone()
            _insert_event(
                cur,
                job_id,
                "stage_retry_requested",
                "INFO",
                f"{requested_by} re-queued this invoice for processing.",
                None,
                {
                    "requested_by": requested_by,
                    "note": note,
                    "retry_generation": updated["retry_generation"],
                    "manual_retry_count": updated["manual_retry_count"],
                },
            )
        conn.commit()
    return {"job_id": job_id, **updated}


def ops_overview(window_hours: int = 24) -> dict[str, Any]:
    """Operational aggregates, read straight from Postgres.

    Postgres is the only honest source here: the API and the worker are separate processes, so
    an in-memory counter would only ever describe whichever one answered the request. Every
    query below is bounded by the window and by a LIMIT, so the endpoint stays cheap to poll.
    """
    hours = min(max(window_hours, 1), 168)
    internal = list(observability.STAGE_MAP)
    friendly = [observability.STAGE_MAP[stage] for stage in internal]

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT status,
                   count(*)::int AS jobs,
                   count(*) FILTER (WHERE decision_status = 'NEEDS_REVIEW')::int AS awaiting_review
            FROM invoice_jobs
            GROUP BY status
            """
        )
        queue_rows = cur.fetchall()

        cur.execute(
            """
            SELECT decision_status, count(*)::int AS jobs
            FROM invoice_jobs
            WHERE decision_status IS NOT NULL
              AND updated_at >= now() - make_interval(hours => %s)
            GROUP BY decision_status
            """,
            (hours,),
        )
        decision_rows = cur.fetchall()

        cur.execute(
            """
            SELECT count(*) FILTER (WHERE status = 'FAILED')::int AS failed_jobs,
                   coalesce(sum(manual_retry_count), 0)::int AS manual_retries,
                   count(*) FILTER (WHERE manual_retry_count > 0)::int AS retried_jobs,
                   count(*)::int AS jobs
            FROM invoice_jobs
            WHERE updated_at >= now() - make_interval(hours => %s)
            """,
            (hours,),
        )
        reliability = cur.fetchone()

        # The friendly names are joined in rather than mapped afterwards, so the two internal
        # stages that share "text_or_ocr" produce one honest percentile instead of two halves.
        cur.execute(
            """
            WITH friendly AS (
                SELECT * FROM unnest(%s::text[], %s::text[]) AS t(internal, name)
            )
            SELECT f.name AS stage,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY e.ms) AS p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY e.ms) AS p95_ms,
                   count(*)::int AS samples
            FROM invoice_events e
            JOIN friendly f ON f.internal = e.stage
            WHERE e.ms IS NOT NULL AND e.ts >= now() - make_interval(hours => %s)
            GROUP BY f.name
            ORDER BY f.name
            """,
            (internal, friendly, hours),
        )
        stage_rows = cur.fetchall()

        cur.execute(
            """
            SELECT count(*) FILTER (WHERE data->>'used_ocr_fallback' = 'true')::int AS fallbacks,
                   count(*)::int AS documents
            FROM invoice_events
            WHERE stage = 'stage_text_extract' AND ts >= now() - make_interval(hours => %s)
            """,
            (hours,),
        )
        ocr = cur.fetchone()

        cur.execute(
            """
            SELECT count(*) FILTER (WHERE status = 'PASS')::int AS success,
                   count(*) FILTER (WHERE data->>'error_code' = 'medha_timeout')::int AS timeout,
                   count(*) FILTER (
                       WHERE status = 'FAIL' AND coalesce(data->>'error_code', '') <> 'medha_timeout'
                   )::int AS error,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY ms) AS p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY ms) AS p95_ms
            FROM invoice_events
            WHERE stage = 'stage_medha_extract' AND ts >= now() - make_interval(hours => %s)
            """,
            (hours,),
        )
        medha = cur.fetchone()

        cur.execute(
            """
            SELECT reason, count(*)::int AS occurrences
            FROM invoice_events
            WHERE status = 'FAIL' AND reason IS NOT NULL AND reason <> ''
              AND ts >= now() - make_interval(hours => %s)
            GROUP BY reason
            ORDER BY occurrences DESC, reason
            LIMIT 5
            """,
            (hours,),
        )
        failure_rows = cur.fetchall()

    end_to_end = next((row for row in stage_rows if row["stage"] == "completed"), None)
    return {
        "window_hours": hours,
        "queue": {row["status"]: row["jobs"] for row in queue_rows},
        "awaiting_review": sum(row["awaiting_review"] for row in queue_rows),
        "decisions": {row["decision_status"]: row["jobs"] for row in decision_rows},
        "reliability": dict(reliability or {}),
        "processing_ms": {
            "p50": end_to_end["p50_ms"] if end_to_end else None,
            "p95": end_to_end["p95_ms"] if end_to_end else None,
            "samples": end_to_end["samples"] if end_to_end else 0,
        },
        "stages": stage_rows,
        "ocr": {
            **dict(ocr or {}),
            "fallback_rate": (ocr["fallbacks"] / ocr["documents"]) if ocr and ocr["documents"] else None,
        },
        "medha": dict(medha or {}),
        "top_failures": failure_rows,
    }
