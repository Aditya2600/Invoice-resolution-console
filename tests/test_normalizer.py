from decimal import Decimal

from app.pipeline.normalizer import heuristic_extract, normalize_invoice_number, normalize_name, parse_model_json


def test_heuristic_extracts_common_invoice_fields() -> None:
    extraction = heuristic_extract(
        """Acme Supplies Pvt Ltd
        TAX INVOICE
        Invoice Number: ACM-102
        Invoice Date: 18/07/2026
        Purchase Order: PO-1001
        Subtotal: INR 100.00
        GST: INR 18.00
        Grand Total: INR 118.00
        """
    )
    assert extraction.vendor_name == "Acme Supplies Pvt Ltd"
    assert extraction.invoice_number == "ACM-102"
    assert extraction.po_number == "PO-1001"
    assert extraction.total == Decimal("118.00")


def test_normalization_is_stable() -> None:
    assert normalize_name("Acme Supplies Pvt. Ltd") == "acmesupplies"
    assert normalize_invoice_number("acm / 100-2") == "ACM1002"


def test_normalize_name_strips_legal_suffixes_for_matching() -> None:
    assert normalize_name("BluePeak Office Supplies") == normalize_name(
        "BluePeak Office Supplies Pvt Ltd"
    )
    assert normalize_name("Stellar IT Services") == normalize_name(
        "Stellar IT Services Private Limited"
    )


def test_normalize_name_does_not_collapse_different_vendors() -> None:
    assert normalize_name("BluePeak Office Supplies") != normalize_name(
        "GreenPeak Office Supplies Pvt Ltd"
    )
    assert normalize_name("Stellar IT Services") != normalize_name(
        "Stellar Software Services Private Limited"
    )


def test_model_json_allows_fences() -> None:
    assert parse_model_json("```json\n{\"invoice_number\": \"I-1\"}\n```") == {"invoice_number": "I-1"}

