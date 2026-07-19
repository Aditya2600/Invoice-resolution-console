from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil import parser as date_parser

from app.core.schemas import Evidence, InvoiceExtraction


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def normalize_invoice_number(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def normalize_po_number(value: str | None) -> str | None:
    if not value:
        return None
    value = value.upper().strip()
    normalized = re.sub(r"\s+", "", value)
    if normalized in {"NOT", "NONE", "NA", "N/A", "MISSING", "UNKNOWN"}:
        return None
    return normalized


def money(value: str | Decimal | int | float | None) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    cleaned = str(value).replace(",", "").replace("₹", "").replace("INR", "").strip()
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date_parser.parse(value, dayfirst=True, fuzzy=True).date()
    except (TypeError, ValueError, OverflowError):
        return None


def parse_model_json(content: str) -> dict:
    """Parse raw JSON or a fenced JSON response from a VLM."""
    stripped = content.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Model response did not contain a JSON object")
    return json.loads(stripped[start : end + 1])


def heuristic_extract(raw_text: str) -> InvoiceExtraction:
    """Small deterministic fallback for native-text PDFs when MEDHA is unavailable."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    vendor_name = lines[0] if lines else None

    invoice_match = re.search(
        r"(?:invoice[ \t]*(?:no\.?|number|#)?[ \t]*[:\-]?[ \t]*)([A-Z0-9][A-Z0-9\-/]+)",
        raw_text,
        re.IGNORECASE,
    )
    po_match = re.search(r"(?:purchase[ \t]*order|PO)[ \t]*(?:no\.?|number|#)?[ \t]*[:\-]?[ \t]*([A-Z0-9\-/]+)", raw_text, re.IGNORECASE)
    date_match = re.search(r"(?:invoice\s*)?date\s*[:\-]?\s*([^\n]+)", raw_text, re.IGNORECASE)

    total_matches = re.findall(
        r"(?:grand\s*total|invoice\s*total|total\s*amount|total)\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)",
        raw_text,
        re.IGNORECASE,
    )
    tax_matches = re.findall(r"(?:GST|tax)\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", raw_text, re.IGNORECASE)
    subtotal_matches = re.findall(r"(?:sub\s*total|subtotal)\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", raw_text, re.IGNORECASE)

    evidence: list[Evidence] = []
    if invoice_match:
        evidence.append(Evidence(field="invoice_number", page=1, quote=invoice_match.group(0), confidence=0.7))
    if po_match:
        evidence.append(Evidence(field="po_number", page=1, quote=po_match.group(0), confidence=0.7))
    if total_matches:
        evidence.append(Evidence(field="total", page=1, quote=total_matches[-1], confidence=0.65))

    normalized_po_number = normalize_po_number(po_match.group(1)) if po_match else None
    useful_fields = sum(bool(value) for value in (vendor_name, invoice_match, normalized_po_number, total_matches))
    return InvoiceExtraction(
        vendor_name=vendor_name,
        invoice_number=invoice_match.group(1).strip() if invoice_match else None,
        invoice_date=parse_date(date_match.group(1)) if date_match else None,
        po_number=normalized_po_number,
        subtotal=money(subtotal_matches[-1]) if subtotal_matches else None,
        tax=money(tax_matches[-1]) if tax_matches else None,
        total=money(total_matches[-1]) if total_matches else None,
        extraction_confidence=min(0.25 + useful_fields * 0.15, 0.9),
        evidence=evidence,
        raw_text=raw_text,
    )


def coerce_model_extraction(payload: dict, raw_text: str) -> InvoiceExtraction:
    evidence = payload.get("evidence") or []
    payload = {
        **payload,
        "po_number": normalize_po_number(payload.get("po_number")),
        "raw_text": raw_text,
        "evidence": evidence,
    }
    return InvoiceExtraction.model_validate(payload)
