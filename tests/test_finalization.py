"""Financial integrity of finalize_invoice_decision: no partial state, no over-allocation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from app.db import repository


def extraction(total: str = "100") -> dict:
    return {
        "vendor_name": "Globex Ltd",
        "invoice_number": "GLX-1",
        "currency": "INR",
        "subtotal": "85",
        "tax": "15",
        "total": total,
        "extraction_confidence": 0.99,
        "line_items": [],
        "evidence": [],
    }


def matched(po_number: str, total: str = "1000") -> dict:
    return {
        "po_number": po_number,
        "vendor_name": "Globex Ltd",
        "currency": "INR",
        "total_amount": total,
        "consumed_amount": "0",
        "status": "OPEN",
    }


def approve(job_id: str, document_id: str, po_number: str, amount: str) -> dict:
    return repository.finalize_invoice_decision(
        job_id=job_id,
        document_id=document_id,
        decision_status="APPROVED",
        extraction=extraction(amount),
        matched_po=matched(po_number),
        reasons=["All checks passed."],
        rule_checks={},
        allocation_amount=Decimal(amount),
    )


def test_result_write_failure_leaves_no_consumed_balance(make_po, make_job, po_row, allocations) -> None:
    po_number = make_po(total="1000")
    job_id, document_id = make_job()

    # An unserializable extraction fails the result write, which happens after the PO update.
    with pytest.raises(TypeError):
        repository.finalize_invoice_decision(
            job_id=job_id,
            document_id=document_id,
            decision_status="APPROVED",
            extraction={"vendor_name": object()},
            matched_po=matched(po_number),
            reasons=[],
            rule_checks={},
            allocation_amount=Decimal("100"),
        )

    assert po_row(po_number)["consumed_amount"] == Decimal("0")
    assert allocations(po_number) == []
    assert repository.get_job(job_id)["status"] == "PROCESSING"


def test_concurrent_approvals_cannot_over_allocate(make_po, make_job, po_row, allocations) -> None:
    po_number = make_po(total="100")
    first = make_job()
    second = make_job()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve, job, document, po_number, "100") for job, document in (first, second)]
        outcomes = [future.result() for future in futures]

    statuses = sorted(outcome["decision_status"] for outcome in outcomes)
    assert statuses == ["APPROVED", "NEEDS_REVIEW"]
    assert po_row(po_number)["consumed_amount"] == Decimal("100")
    assert len(allocations(po_number)) == 1


def test_approved_invoice_creates_exactly_one_allocation(make_po, make_job, po_row, allocations) -> None:
    po_number = make_po(total="1000")
    job_id, document_id = make_job()

    outcome = approve(job_id, document_id, po_number, "100")
    assert outcome["decision_status"] == "APPROVED"
    assert outcome["allocation_id"]

    # A retried finalization of the same document must not consume the balance twice.
    approve(job_id, document_id, po_number, "100")

    assert len(allocations(po_number)) == 1
    assert po_row(po_number)["consumed_amount"] == Decimal("100")
    assert repository.get_job(job_id)["decision_status"] == "APPROVED"
