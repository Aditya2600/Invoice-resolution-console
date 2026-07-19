"""Human resolution of NEEDS_REVIEW invoices.

The reviewer never hand-writes an outcome: an approval re-runs the same deterministic policy
evaluation the pipeline ran, using the reviewer's corrections and/or PO choice, and finalizes
through the one atomic transaction that also consumes purchase-order balance.
"""

from __future__ import annotations

from typing import Any

from app.core.schemas import DecisionStatus, InvoiceExtraction, MatchResult
from app.db import repository
from app.pipeline.decision import build_policy_snapshot, evaluate_decision, match_purchase_order, policy_hash


class ReviewError(RuntimeError):
    """The requested resolution is not allowed for this job."""


def _corrected_extraction(original: dict[str, Any], corrections: dict[str, Any]) -> InvoiceExtraction:
    """Original model extraction stays untouched in invoice_results; this is the review-time view.

    A human has now read the document, so the auto-approval confidence gate is attested rather than
    modelled. Every financial rule (PO open, vendor, currency, remaining balance) still applies.
    """
    merged = {**original, **{k: v for k, v in corrections.items() if v is not None}}
    return InvoiceExtraction(**{**merged, "extraction_confidence": 1.0})


def resolve_review(
    *,
    job_id: str,
    action: str,
    reviewer_name: str,
    note: str,
    selected_po_number: str | None = None,
    corrections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail = repository.get_job_detail(job_id)
    if not detail:
        raise LookupError("Job not found.")

    job, result = detail["job"], detail["result"]
    if job["decision_status"] != DecisionStatus.NEEDS_REVIEW:
        raise repository.ReviewConflict(
            f"This invoice is already resolved as {job['decision_status'] or job['status']}."
        )
    if not result:
        raise ReviewError("This job has no decision result to resolve.")

    corrections = corrections or {}
    extraction = _corrected_extraction(result["extraction"], corrections)
    review_action = {
        "reviewer_name": reviewer_name,
        "action": action,
        "selected_po_number": selected_po_number,
        "corrections": corrections,
        "note": note,
        "decision_before": job["decision_status"],
    }

    if action == "REJECT":
        snapshot = build_policy_snapshot(extraction.vendor_name)
        return repository.finalize_invoice_decision(
            job_id=job_id,
            document_id=job["document_id"],
            decision_status=DecisionStatus.REJECTED,
            extraction=result["extraction"],
            matched_po=result["matched_po"],
            reasons=[f"Rejected by {reviewer_name}: {note}"],
            rule_checks={**result["rule_checks"], "human_review": {"passed": False, "reviewer": reviewer_name}},
            model_name=result["model_name"],
            model_latency_ms=result["model_latency_ms"],
            allocation_amount=None,
            review_action=review_action,
            policy_snapshot=snapshot,
            policy_hash=policy_hash(snapshot),
        )

    if selected_po_number:
        candidates = repository.find_purchase_orders(po_number=selected_po_number, vendor_name=None)
        if not candidates:
            raise ReviewError(f"Purchase order {selected_po_number} does not exist.")
        match = MatchResult(
            po=candidates[0],
            score=1.0,
            candidates=[candidates[0].po_number],
            reason=f"{reviewer_name} selected this purchase order.",
        )
    else:
        match = match_purchase_order(extraction)

    # Deterministic re-validation: a closed PO or an insufficient balance can never be approved,
    # regardless of what the reviewer asked for.
    decision = evaluate_decision(extraction, match, None)
    if decision.status != DecisionStatus.APPROVED:
        raise ReviewError(
            "This invoice still fails validation and cannot be approved: " + " ".join(decision.reasons)
        )

    outcome = repository.finalize_invoice_decision(
        job_id=job_id,
        document_id=job["document_id"],
        decision_status=DecisionStatus.APPROVED,
        extraction=result["extraction"],
        matched_po=decision.matched_po.model_dump(mode="json") if decision.matched_po else None,
        reasons=[f"Approved by {reviewer_name}: {note}"],
        rule_checks={**decision.rule_checks, "human_review": {"passed": True, "reviewer": reviewer_name}},
        model_name=result["model_name"],
        model_latency_ms=result["model_latency_ms"],
        allocation_amount=extraction.total,
        review_action=review_action,
        policy_snapshot=decision.policy_snapshot,
        policy_hash=decision.policy_hash,
    )
    if outcome["decision_status"] != DecisionStatus.APPROVED:
        raise ReviewError(
            "The purchase-order balance changed during resolution; the invoice remains in review."
        )
    return outcome
