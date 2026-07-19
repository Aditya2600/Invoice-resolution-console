"""Lifecycle of the vendor + invoice-number identity claim."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.db import repository
from app.pipeline import review
from app.pipeline.decision import evaluate_decision
from app.core.schemas import InvoiceExtraction, MatchResult
from tests.test_finalization import extraction
from tests.test_review import needs_review_job


def identity() -> str:
    """A vendor/invoice pair no other test is using."""
    return f"INV-{uuid.uuid4().hex[:10].upper()}"


def claim(document_id: str, job_id: str, invoice_number: str, total: str = "100"):
    return repository.claim_semantic_invoice(
        document_id=document_id,
        job_id=job_id,
        vendor_name="Globex Ltd",
        invoice_number=invoice_number,
        total=Decimal(total),
    )


def test_failed_job_releases_pending_claim(make_job, fail_terminally) -> None:
    job_id, document_id = make_job()
    invoice_number = identity()
    assert claim(document_id, job_id, invoice_number) is None
    assert repository.get_identity_claim(document_id)["state"] == "PENDING"

    fail_terminally(job_id)
    assert repository.get_job(job_id)["status"] == "FAILED"
    assert repository.get_identity_claim(document_id)["state"] == "RELEASED"


def test_review_pending_job_retains_claim(make_job) -> None:
    job_id, document_id = needs_review_job(make_job)
    invoice_number = identity()
    claim(document_id, job_id, invoice_number)

    repository.finalize_invoice_decision(
        job_id=job_id,
        document_id=document_id,
        decision_status="NEEDS_REVIEW",
        extraction=extraction("100"),
        matched_po=None,
        reasons=["A reviewer must choose a purchase order."],
        rule_checks={},
    )
    assert repository.get_identity_claim(document_id)["state"] == "PENDING"


def test_reviewer_rejection_releases_claim(make_job) -> None:
    job_id, document_id = needs_review_job(make_job)
    claim(document_id, job_id, identity())

    review.resolve_review(
        job_id=job_id, action="REJECT", reviewer_name="Priya", note="Wrong entity billed."
    )
    assert repository.get_identity_claim(document_id)["state"] == "RELEASED"


def test_reviewer_approval_finalizes_claim(make_po, make_job) -> None:
    po_number = make_po(total="500")
    job_id, document_id = needs_review_job(make_job)
    claim(document_id, job_id, identity())

    review.resolve_review(
        job_id=job_id, action="APPROVE", reviewer_name="Priya", note="ok", selected_po_number=po_number
    )
    assert repository.get_identity_claim(document_id)["state"] == "FINAL"


def test_corrected_reupload_can_claim_a_released_identity(make_job) -> None:
    invoice_number = identity()
    first_job, first_document = make_job()
    claim(first_document, first_job, invoice_number)
    repository.release_identity_claim(first_document, "Reviewer rejected the invoice.")

    second_job, second_document = make_job()
    assert claim(second_document, second_job, invoice_number) is None
    assert repository.get_identity_claim(second_document)["state"] == "PENDING"


def test_same_amount_final_identity_is_rejected(make_job) -> None:
    invoice_number = identity()
    first_job, first_document = make_job()
    claim(first_document, first_job, invoice_number, total="100")
    repository.finalize_invoice_decision(
        job_id=first_job,
        document_id=first_document,
        decision_status="APPROVED",
        extraction=extraction("100"),
        matched_po=None,
        reasons=["ok"],
        rule_checks={},
    )

    second_job, second_document = make_job()
    prior = claim(second_document, second_job, invoice_number, total="100")
    assert prior["state"] == "FINAL"

    decision = evaluate_decision(
        InvoiceExtraction(**extraction("100")), MatchResult(reason="none"), prior
    )
    assert decision.status == "REJECTED"


def test_different_amount_final_identity_needs_review(make_job) -> None:
    invoice_number = identity()
    first_job, first_document = make_job()
    claim(first_document, first_job, invoice_number, total="100")
    repository.finalize_invoice_decision(
        job_id=first_job,
        document_id=first_document,
        decision_status="APPROVED",
        extraction=extraction("100"),
        matched_po=None,
        reasons=["ok"],
        rule_checks={},
    )

    second_job, second_document = make_job()
    prior = claim(second_document, second_job, invoice_number, total="250")

    decision = evaluate_decision(
        InvoiceExtraction(**extraction("250")), MatchResult(reason="none"), prior
    )
    assert decision.status == "NEEDS_REVIEW"


def test_second_worker_cannot_hold_the_same_active_identity(make_job) -> None:
    invoice_number = identity()
    first_job, first_document = make_job()
    claim(first_document, first_job, invoice_number)

    second_job, second_document = make_job()
    prior = claim(second_document, second_job, invoice_number)

    assert prior["first_document_id"] == first_document
    assert prior["state"] == "PENDING"
    assert repository.get_identity_claim(second_document) is None
