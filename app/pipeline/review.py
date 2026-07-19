"""Human resolution of NEEDS_REVIEW invoices.

The reviewer never hand-writes an outcome: an approval re-runs the same deterministic policy
evaluation the pipeline ran, using the reviewer's corrections and/or PO choice, and finalizes
through the one atomic transaction that also consumes purchase-order balance.
"""

from __future__ import annotations

from typing import Any

from app.core.schemas import DecisionStatus, InvoiceExtraction, MatchResult, merge_corrections
from app.db import repository
from app.pipeline.decision import build_policy_snapshot, evaluate_decision, match_purchase_order, policy_hash
from app.pipeline.normalizer import normalize_invoice_number, normalize_name


class ReviewError(RuntimeError):
    """The requested resolution is not allowed for this job."""


def _correction_detail(original: dict[str, Any], corrections: dict[str, Any]) -> list[dict[str, Any]]:
    """Before/after for every field the reviewer touched, for the audit event."""
    return [
        {"field": field, "original": original.get(field), "corrected": value}
        for field, value in corrections.items()
        if value is not None
    ]


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
    original = result["extraction"]
    extraction = InvoiceExtraction(**merge_corrections(original, corrections))
    # The policy frozen onto the result at processing time, not whatever the config says today.
    snapshot = result.get("policy_snapshot") or build_policy_snapshot(extraction.vendor_name)
    review_action = {
        "reviewer_name": reviewer_name,
        "action": action,
        "selected_po_number": selected_po_number,
        "corrections": corrections,
        "corrections_detail": _correction_detail(original, corrections),
        "reviewer_attestation": action == "APPROVE",
        "note": note,
        "decision_before": job["decision_status"],
    }

    if action == "REJECT":
        # A rejection releases the identity outright, so there is nothing to migrate.
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

    # Deterministic re-validation of the effective extraction: vendor, PO, currency, arithmetic and
    # balance are all re-checked, so a closed PO or an insufficient balance can never be approved
    # regardless of what the reviewer asked for. Only the model-confidence gate is attested away.
    decision = evaluate_decision(
        extraction, match, None, policy_snapshot=snapshot, reviewer_attestation=True
    )
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
        rule_checks={
            **decision.rule_checks,
            "human_review": {
                "passed": True,
                "reviewer": reviewer_name,
                "reviewer_attestation": True,
                "model_confidence": original.get("extraction_confidence"),
                "corrected_fields": [item["field"] for item in review_action["corrections_detail"]],
            },
        },
        model_name=result["model_name"],
        model_latency_ms=result["model_latency_ms"],
        allocation_amount=extraction.total,
        review_action=review_action,
        policy_snapshot=decision.policy_snapshot,
        policy_hash=decision.policy_hash,
        identity={
            "vendor_normalized": normalize_name(extraction.vendor_name),
            "invoice_number_normalized": normalize_invoice_number(extraction.invoice_number),
            "total": extraction.total,
        },
    )
    if outcome["decision_status"] != DecisionStatus.APPROVED:
        raise ReviewError(
            "The purchase-order balance changed during resolution; the invoice remains in review."
        )
    return outcome
