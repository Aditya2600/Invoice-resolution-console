"""Evidence-grounded reviewer corrections: the effective extraction is re-validated, the model
extraction is preserved, and a corrected identity moves atomically."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db import repository
from app.main import app
from app.pipeline import review
from app.pipeline.decision import build_policy_snapshot, policy_hash
from tests.test_finalization import approve, extraction


def unique_invoice_number() -> str:
    return f"INV-{uuid.uuid4().hex[:10].upper()}"


def review_job(make_job, *, overrides: dict | None = None, snapshot: dict | None = None):
    """A COMPLETED/NEEDS_REVIEW job with a unique invoice identity, ready for resolution."""
    job_id, document_id = make_job()
    data = {**extraction("100"), "invoice_number": unique_invoice_number(), **(overrides or {})}
    repository.finalize_invoice_decision(
        job_id=job_id,
        document_id=document_id,
        decision_status="NEEDS_REVIEW",
        extraction=data,
        matched_po=None,
        reasons=["A reviewer must choose a purchase order."],
        rule_checks={},
        policy_snapshot=snapshot,
        policy_hash=policy_hash(snapshot) if snapshot else None,
    )
    return job_id, document_id, data


def claim(document_id: str, job_id: str, invoice_number: str, total: str = "100"):
    return repository.claim_semantic_invoice(
        document_id=document_id,
        job_id=job_id,
        vendor_name="Globex Ltd",
        invoice_number=invoice_number,
        total=Decimal(total),
    )


def live_claims(db, document_id: str) -> dict[str, str]:
    """Every claim this document has ever held, keyed by normalized invoice number."""
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT invoice_number_normalized, state FROM invoice_identity_claims WHERE document_id = %s",
            (document_id,),
        )
        return {row["invoice_number_normalized"]: row["state"] for row in cur.fetchall()}


def test_corrected_po_number_enables_approval(make_po, make_job, allocations, po_row) -> None:
    po_number = make_po(total="500")
    job_id, _, _ = review_job(make_job)

    outcome = review.resolve_review(
        job_id=job_id,
        action="APPROVE",
        reviewer_name="Priya",
        note="The PO number was misread from a smudged scan.",
        corrections={"po_number": po_number},
    )

    assert outcome["decision_status"] == "APPROVED"
    assert len(allocations(po_number)) == 1
    assert po_row(po_number)["consumed_amount"] == Decimal("100")


def test_original_extraction_and_model_confidence_survive_a_correction(make_po, make_job) -> None:
    po_number = make_po(total="500")
    job_id, _, original = review_job(make_job, overrides={"extraction_confidence": 0.42})

    review.resolve_review(
        job_id=job_id,
        action="APPROVE",
        reviewer_name="Priya",
        note="Total was read off the wrong line.",
        selected_po_number=po_number,
        corrections={"total": "120", "subtotal": "105", "vendor_name": "GLOBEX LTD."},
    )

    detail = repository.get_job_detail(job_id)
    stored = detail["result"]["extraction"]
    assert stored["total"] == original["total"] == "100"
    assert stored["vendor_name"] == "Globex Ltd"
    # A human reading the document is an attestation, never a confidence upgrade.
    assert stored["extraction_confidence"] == 0.42
    checks = detail["result"]["rule_checks"]
    assert checks["extraction_confidence"] == {
        "passed": True,
        "value": 0.42,
        "minimum": checks["extraction_confidence"]["minimum"],
        "reviewer_attested": True,
    }
    assert detail["effective_extraction"]["total"] == "120"
    assert detail["effective_extraction"]["extraction_confidence"] == 0.42


@pytest.mark.parametrize(
    "corrections",
    [
        {"nonsense_field": "anything"},
        {"invoice_date": "not-a-date"},
        {"total": "-5"},
        {"currency": "rupees"},
    ],
)
def test_invalid_correction_is_refused_without_side_effects(make_po, make_job, allocations, corrections) -> None:
    po_number = make_po(total="500")
    job_id, _, _ = review_job(make_job)

    with TestClient(app) as client:
        response = client.post(
            f"/api/jobs/{job_id}/review/resolve",
            json={
                "action": "APPROVE",
                "reviewer_name": "Priya",
                "note": "Fixing the invoice.",
                "selected_po_number": po_number,
                "corrections": corrections,
            },
        )

    assert response.status_code == 422
    assert allocations(po_number) == []
    assert repository.list_review_actions(job_id) == []
    assert repository.get_job(job_id)["decision_status"] == "NEEDS_REVIEW"


def test_corrected_identity_conflicting_with_a_final_claim_is_blocked(
    make_po, make_job, allocations, db
) -> None:
    po_number = make_po(total="500")
    taken = unique_invoice_number()
    holder_job, holder_document = make_job()
    claim(holder_document, holder_job, taken)
    approve(holder_job, holder_document, po_number, "100")

    job_id, document_id, original = review_job(make_job)
    claim(document_id, job_id, original["invoice_number"])

    with pytest.raises(repository.IdentityConflict):
        review.resolve_review(
            job_id=job_id,
            action="APPROVE",
            reviewer_name="Priya",
            note="Same invoice number as the earlier one.",
            selected_po_number=po_number,
            corrections={"invoice_number": taken},
        )

    assert len(allocations(po_number)) == 1  # only the holder's
    assert repository.list_review_actions(job_id) == []
    assert repository.get_job(job_id)["decision_status"] == "NEEDS_REVIEW"
    assert live_claims(db, document_id) == {original["invoice_number"].replace("-", ""): "PENDING"}


def test_corrected_identity_migrates_the_pending_claim(make_po, make_job, db) -> None:
    po_number = make_po(total="500")
    job_id, document_id, original = review_job(make_job)
    claim(document_id, job_id, original["invoice_number"])
    corrected = unique_invoice_number()

    review.resolve_review(
        job_id=job_id,
        action="APPROVE",
        reviewer_name="Priya",
        note="Invoice number was transposed.",
        selected_po_number=po_number,
        corrections={"invoice_number": corrected},
    )

    assert live_claims(db, document_id) == {
        original["invoice_number"].replace("-", ""): "RELEASED",
        corrected.replace("-", ""): "FINAL",
    }


def test_two_reviewers_cannot_double_consume_a_purchase_order(make_po, make_job, allocations, po_row) -> None:
    po_number = make_po(total="500")
    job_id, _, _ = review_job(make_job)

    def resolve(reviewer: str):
        try:
            return review.resolve_review(
                job_id=job_id,
                action="APPROVE",
                reviewer_name=reviewer,
                note="Approving against the signed PO.",
                selected_po_number=po_number,
            )
        except (repository.ReviewConflict, review.ReviewError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(resolve, ["Priya", "Arjun"]))

    approved = [item for item in outcomes if isinstance(item, dict)]
    assert len(approved) == 1
    assert len(allocations(po_number)) == 1
    assert po_row(po_number)["consumed_amount"] == Decimal("100")
    assert len(repository.list_review_actions(job_id)) == 1


def test_review_uses_the_persisted_policy_snapshot(make_po, make_job) -> None:
    po_number = make_po(total="500")
    frozen = {**build_policy_snapshot("Globex Ltd"), "allowed_currencies": ["INR"], "require_po_number": False}
    job_id, _, _ = review_job(make_job, snapshot=frozen)

    rules_path = get_settings().vendor_rules_path
    saved = rules_path.read_text()
    try:
        # Today's config would reject this invoice outright; the frozen snapshot must win.
        changed = json.loads(saved)
        changed["__default__"]["allowed_currencies"] = ["USD"]
        changed["__default__"]["require_po_number"] = True
        rules_path.write_text(json.dumps(changed))

        outcome = review.resolve_review(
            job_id=job_id,
            action="APPROVE",
            reviewer_name="Priya",
            note="Confirmed against the signed PO.",
            selected_po_number=po_number,
        )
    finally:
        rules_path.write_text(saved)

    assert outcome["decision_status"] == "APPROVED"
    result = repository.get_job_detail(job_id)["result"]
    assert result["policy_snapshot"] == frozen
    assert result["policy_hash"] == policy_hash(frozen)
