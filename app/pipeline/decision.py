from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.core.schemas import DecisionStatus, InvoiceDecision, InvoiceExtraction, MatchResult, PurchaseOrder
from app.db.repository import find_purchase_orders
from app.pipeline.normalizer import normalize_name


@dataclass(frozen=True)
class VendorRule:
    amount_tolerance: Decimal
    minimum_auto_approve_confidence: float
    require_po_number: bool
    allowed_currencies: tuple[str, ...]


@lru_cache
def _rules(path: str, modified_at: float) -> dict:
    return json.loads(Path(path).read_text())


def get_vendor_rule(vendor_name: str | None) -> VendorRule:
    settings = get_settings()
    path = settings.vendor_rules_path
    contents = _rules(str(path), path.stat().st_mtime)
    selected = contents.get(normalize_name(vendor_name), contents["__default__"])
    return VendorRule(
        amount_tolerance=Decimal(str(selected.get("amount_tolerance", "1.00"))),
        minimum_auto_approve_confidence=float(selected.get("minimum_auto_approve_confidence", 0.85)),
        require_po_number=bool(selected.get("require_po_number", False)),
        allowed_currencies=tuple(currency.upper() for currency in selected.get("allowed_currencies", ["INR"])),
    )


def build_policy_snapshot(vendor_name: str | None) -> dict:
    """The exact rule values a decision was made under, frozen onto the result row.

    vendor_rules.json can change at any time; a stored snapshot keeps every historical
    decision explainable without reading the current config.
    """
    rule = get_vendor_rule(vendor_name)
    return {
        "policy_version": get_settings().policy_version,
        "vendor_normalized": normalize_name(vendor_name),
        "amount_tolerance": str(rule.amount_tolerance),
        "minimum_auto_approve_confidence": rule.minimum_auto_approve_confidence,
        "require_po_number": rule.require_po_number,
        "allowed_currencies": list(rule.allowed_currencies),
    }


def rule_from_snapshot(snapshot: dict) -> VendorRule:
    """Rebuild the rule a decision was made under, so a re-review reads the frozen values.

    A reviewer resolving an invoice weeks later must be judged by the policy that was in force
    when it was processed, never by whatever vendor_rules.json says today.
    """
    return VendorRule(
        amount_tolerance=Decimal(str(snapshot["amount_tolerance"])),
        minimum_auto_approve_confidence=float(snapshot["minimum_auto_approve_confidence"]),
        require_po_number=bool(snapshot["require_po_number"]),
        allowed_currencies=tuple(currency.upper() for currency in snapshot["allowed_currencies"]),
    )


def policy_hash(snapshot: dict) -> str:
    """Stable SHA-256 over canonical sorted JSON: same rule values always hash the same."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def match_purchase_order(extraction: InvoiceExtraction) -> MatchResult:
    candidates = find_purchase_orders(po_number=extraction.po_number, vendor_name=extraction.vendor_name)
    if not candidates:
        reason = "No purchase order matches the extracted PO number or vendor."
        return MatchResult(reason=reason)

    if extraction.po_number:
        po = candidates[0]
        return MatchResult(
            po=po,
            score=1.0 if normalize_name(po.vendor_name) == normalize_name(extraction.vendor_name) else 0.75,
            candidates=[po.po_number],
            reason="Exact PO number match.",
        )

    if len(candidates) == 1:
        return MatchResult(po=candidates[0], score=0.65, candidates=[candidates[0].po_number], reason="Single open PO for vendor.")

    affordable = [po for po in candidates if extraction.total is not None and po.remaining_amount >= extraction.total]
    if len(affordable) == 1:
        po = affordable[0]
        return MatchResult(po=po, score=0.72, candidates=[item.po_number for item in candidates], reason="Only one vendor PO has enough remaining balance.")

    return MatchResult(
        candidates=[po.po_number for po in candidates],
        reason="Multiple open purchase orders match this vendor; a reviewer must choose one.",
    )


def evaluate_decision(
    extraction: InvoiceExtraction,
    match: MatchResult,
    semantic_duplicate: dict | None,
    *,
    policy_snapshot: dict | None = None,
    reviewer_attestation: bool = False,
) -> InvoiceDecision:
    """Deterministic policy verdict, stamped with the rule values it was made under.

    Pass policy_snapshot to re-evaluate under a decision's frozen policy instead of the current
    config; reviewer_attestation replaces the model-confidence gate with a human's sign-off.
    """
    snapshot = policy_snapshot or build_policy_snapshot(extraction.vendor_name)
    decision = _evaluate(
        extraction, match, semantic_duplicate, rule_from_snapshot(snapshot), reviewer_attestation
    )
    return decision.model_copy(update={"policy_snapshot": snapshot, "policy_hash": policy_hash(snapshot)})


def _evaluate(
    extraction: InvoiceExtraction,
    match: MatchResult,
    semantic_duplicate: dict | None,
    rule: VendorRule,
    reviewer_attestation: bool,
) -> InvoiceDecision:
    checks: dict[str, object] = {}
    reasons: list[str] = []

    missing = [
        label
        for label, value in {
            "vendor name": extraction.vendor_name,
            "invoice number": extraction.invoice_number,
            "invoice total": extraction.total,
        }.items()
        if value is None or value == ""
    ]
    checks["required_invoice_fields"] = {"passed": not missing, "missing": missing}
    if missing:
        reasons.append("Missing required extracted field(s): " + ", ".join(missing) + ".")

    if rule.require_po_number and not extraction.po_number:
        checks["required_po_number"] = {"passed": False}
        reasons.append("This vendor policy requires a PO number.")
    else:
        checks["required_po_number"] = {"passed": True}

    currency_ok = extraction.currency in rule.allowed_currencies
    checks["currency"] = {"passed": currency_ok, "value": extraction.currency, "allowed": list(rule.allowed_currencies)}
    if not currency_ok:
        reasons.append(f"Currency {extraction.currency or 'unknown'} is not allowed for this vendor policy.")

    arithmetic_ok = True
    if extraction.subtotal is not None and extraction.tax is not None and extraction.total is not None:
        arithmetic_ok = abs((extraction.subtotal + extraction.tax) - extraction.total) <= rule.amount_tolerance
    checks["invoice_arithmetic"] = {"passed": arithmetic_ok}
    if not arithmetic_ok:
        reasons.append("Subtotal plus tax does not reconcile to the extracted total.")

    # The model's own confidence is reported unchanged either way; attestation records that a
    # human read the document, it never rewrites what the model claimed.
    confidence_ok = reviewer_attestation or extraction.extraction_confidence >= rule.minimum_auto_approve_confidence
    checks["extraction_confidence"] = {
        "passed": confidence_ok,
        "value": extraction.extraction_confidence,
        "minimum": rule.minimum_auto_approve_confidence,
        "reviewer_attested": reviewer_attestation,
    }
    if not confidence_ok:
        reasons.append("Extraction confidence is below the auto-approval threshold.")

    if semantic_duplicate:
        first_total = semantic_duplicate.get("first_total")
        same_total = first_total is not None and extraction.total is not None and Decimal(str(first_total)) == extraction.total
        state = semantic_duplicate.get("state", "FINAL")
        checks["semantic_duplicate"] = {
            "passed": False,
            "first_document_id": semantic_duplicate.get("first_document_id"),
            "same_total": same_total,
            "claim_state": state,
        }
        # A claim that is still PENDING or awaiting review is not proof of a duplicate yet, so it
        # blocks auto-approval instead of rejecting outright.
        if state != "FINAL":
            reasons.append(
                "Another invoice with the same vendor and invoice number is still being processed."
            )
        elif same_total:
            return InvoiceDecision(
                status=DecisionStatus.REJECTED,
                reasons=["Duplicate vendor and invoice number already processed."],
                matched_po=match.po,
                match_confidence=match.score,
                rule_checks=checks,
            )
        else:
            reasons.append("Invoice number was seen before for this vendor, but the amount differs.")
    else:
        checks["semantic_duplicate"] = {"passed": True}

    if not match.po:
        checks["purchase_order_match"] = {"passed": False, "candidates": match.candidates}
        reasons.append(match.reason or "No unambiguous purchase order match exists.")
    else:
        po = match.po
        vendor_ok = normalize_name(po.vendor_name) == normalize_name(extraction.vendor_name)
        po_open = po.status.upper() == "OPEN"
        po_currency_ok = po.currency.upper() == (extraction.currency or "").upper()
        amount_ok = extraction.total is not None and extraction.total <= po.remaining_amount + rule.amount_tolerance
        checks["purchase_order_match"] = {
            "passed": vendor_ok and po_open and po_currency_ok and amount_ok,
            "po_number": po.po_number,
            "vendor_matches": vendor_ok,
            "po_open": po_open,
            "currency_matches": po_currency_ok,
            "remaining_amount": str(po.remaining_amount),
            "invoice_total": str(extraction.total) if extraction.total is not None else None,
            "amount_within_remaining_balance": amount_ok,
        }
        if not po_open:
            return InvoiceDecision(
                status=DecisionStatus.REJECTED,
                reasons=[f"Purchase order {po.po_number} is {po.status} and cannot receive invoices."],
                matched_po=po,
                match_confidence=match.score,
                rule_checks=checks,
            )
        if not vendor_ok:
            reasons.append("The invoice vendor does not match the purchase-order vendor.")
        if not po_currency_ok:
            reasons.append("Invoice and purchase-order currencies do not match.")
        if not amount_ok:
            reasons.append("Invoice total exceeds the remaining purchase-order balance.")

    if reasons:
        return InvoiceDecision(
            status=DecisionStatus.NEEDS_REVIEW,
            reasons=reasons,
            matched_po=match.po,
            match_confidence=match.score,
            rule_checks=checks,
        )

    return InvoiceDecision(
        status=DecisionStatus.APPROVED,
        reasons=["All required fields, PO checks, and policy checks passed."],
        matched_po=match.po,
        match_confidence=match.score,
        rule_checks=checks,
    )

