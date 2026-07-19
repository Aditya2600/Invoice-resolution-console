"""Human resolution of NEEDS_REVIEW invoices."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.db import repository
from app.pipeline import review
from tests.test_finalization import extraction


def needs_review_job(make_job) -> tuple[str, str]:
    job_id, document_id = make_job()
    repository.finalize_invoice_decision(
        job_id=job_id,
        document_id=document_id,
        decision_status="NEEDS_REVIEW",
        extraction=extraction("100"),
        matched_po=None,
        reasons=["Multiple open purchase orders match this vendor; a reviewer must choose one."],
        rule_checks={"purchase_order_match": {"passed": False, "candidates": []}},
    )
    return job_id, document_id


def test_reviewer_resolves_ambiguous_po_by_selection(make_po, make_job, po_row, allocations) -> None:
    chosen = make_po(total="500")
    make_po(total="500")  # the other candidate that made the match ambiguous
    job_id, _ = needs_review_job(make_job)

    outcome = review.resolve_review(
        job_id=job_id,
        action="APPROVE",
        reviewer_name="Priya",
        note="Confirmed against the signed PO.",
        selected_po_number=chosen,
    )

    assert outcome["decision_status"] == "APPROVED"
    assert len(allocations(chosen)) == 1
    assert po_row(chosen)["consumed_amount"] == Decimal("100")
    actions = repository.list_review_actions(job_id)
    assert [a["action"] for a in actions] == ["APPROVE"]
    assert actions[0]["decision_before"] == "NEEDS_REVIEW"
    assert actions[0]["decision_after"] == "APPROVED"


def test_rejected_review_creates_no_allocation(make_po, make_job, po_row, allocations) -> None:
    po_number = make_po(total="500")
    job_id, _ = needs_review_job(make_job)

    outcome = review.resolve_review(
        job_id=job_id,
        action="REJECT",
        reviewer_name="Priya",
        note="Vendor billed the wrong entity.",
        selected_po_number=po_number,
    )

    assert outcome["decision_status"] == "REJECTED"
    assert allocations(po_number) == []
    assert po_row(po_number)["consumed_amount"] == Decimal("0")
    assert repository.get_job(job_id)["decision_status"] == "REJECTED"


def test_second_resolution_is_rejected(make_po, make_job, allocations) -> None:
    po_number = make_po(total="500")
    job_id, _ = needs_review_job(make_job)

    review.resolve_review(
        job_id=job_id, action="APPROVE", reviewer_name="Priya", note="ok", selected_po_number=po_number
    )
    with pytest.raises(repository.ReviewConflict):
        review.resolve_review(
            job_id=job_id, action="APPROVE", reviewer_name="Arjun", note="again", selected_po_number=po_number
        )

    assert len(allocations(po_number)) == 1
    assert len(repository.list_review_actions(job_id)) == 1


def test_reviewer_cannot_approve_beyond_remaining_balance(make_po, make_job, allocations) -> None:
    po_number = make_po(total="50")
    job_id, _ = needs_review_job(make_job)

    with pytest.raises(review.ReviewError):
        review.resolve_review(
            job_id=job_id, action="APPROVE", reviewer_name="Priya", note="push it through",
            selected_po_number=po_number,
        )
    assert allocations(po_number) == []


def test_reviewer_corrections_do_not_overwrite_model_extraction(make_po, make_job) -> None:
    po_number = make_po(total="500")
    job_id, _ = needs_review_job(make_job)

    review.resolve_review(
        job_id=job_id,
        action="APPROVE",
        reviewer_name="Priya",
        note="Invoice number was misread.",
        selected_po_number=po_number,
        corrections={"invoice_number": "GLX-0001"},
    )

    result = repository.get_job_detail(job_id)["result"]
    assert result["extraction"]["invoice_number"] == "GLX-1"
    assert repository.list_review_actions(job_id)[0]["corrections"] == {"invoice_number": "GLX-0001"}
