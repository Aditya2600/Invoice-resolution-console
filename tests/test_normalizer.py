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
    assert normalize_name("Acme Supplies Pvt. Ltd") == "acmesuppliespvtltd"
    assert normalize_invoice_number("acm / 100-2") == "ACM1002"


def test_model_json_allows_fences() -> None:
    assert parse_model_json("```json\n{\"invoice_number\": \"I-1\"}\n```") == {"invoice_number": "I-1"}

