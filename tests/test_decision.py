from datetime import date
from decimal import Decimal

from app.core.schemas import DecisionStatus, InvoiceExtraction, MatchResult, PurchaseOrder
from app.pipeline.decision import evaluate_decision


def extraction(**overrides):
    base = {
        "vendor_name": "Acme Supplies Pvt Ltd",
        "invoice_number": "ACME-1",
        "invoice_date": date(2026, 7, 18),
        "po_number": "PO-1001",
        "currency": "INR",
        "subtotal": Decimal("100"),
        "tax": Decimal("18"),
        "total": Decimal("118"),
        "extraction_confidence": 0.98,
    }
    return InvoiceExtraction(**(base | overrides))


def po(**overrides):
    base = {
        "po_number": "PO-1001",
        "vendor_name": "Acme Supplies Pvt Ltd",
        "currency": "INR",
        "total_amount": Decimal("500"),
        "consumed_amount": Decimal("0"),
        "status": "OPEN",
    }
    return PurchaseOrder(**(base | overrides))


def test_approves_exact_reconciled_invoice() -> None:
    candidate = po()
    decision = evaluate_decision(extraction(), MatchResult(po=candidate, score=1.0), None)
    assert decision.status == DecisionStatus.APPROVED


def test_needs_review_when_total_exceeds_po_balance() -> None:
    candidate = po(total_amount=Decimal("100"))
    decision = evaluate_decision(extraction(), MatchResult(po=candidate, score=1.0), None)
    assert decision.status == DecisionStatus.NEEDS_REVIEW
    assert "remaining purchase-order balance" in " ".join(decision.reasons)


def test_rejects_exact_semantic_duplicate() -> None:
    decision = evaluate_decision(
        extraction(),
        MatchResult(po=po(), score=1.0),
        {"first_document_id": "earlier-document", "first_total": Decimal("118")},
    )
    assert decision.status == DecisionStatus.REJECTED

