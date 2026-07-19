from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "sample_invoices"


def write_invoice(filename: str, *, vendor: str, invoice_number: str, po_number: str | None, subtotal: str, tax: str, total: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(OUTPUT / filename), pagesize=A4)
    _, height = A4
    y = height - 72
    rows = [
        (vendor, 18),
        ("TAX INVOICE", 14),
        (f"Invoice Number: {invoice_number}", 11),
        ("Invoice Date: 18/07/2026", 11),
        (f"Purchase Order: {po_number}", 11) if po_number else ("", 11),
        ("", 11),
        (f"Subtotal: INR {subtotal}", 11),
        (f"GST: INR {tax}", 11),
        (f"Grand Total: INR {total}", 13),
    ]
    for text, size in rows:
        canvas.setFont("Helvetica-Bold" if size >= 13 else "Helvetica", size)
        canvas.drawString(72, y, text)
        y -= 30
    canvas.setFont("Helvetica", 9)
    canvas.drawString(72, y - 20, "Generated sample data for the Invoice Resolution Console demo.")
    canvas.save()


def main() -> None:
    write_invoice(
        "01-approved-acme.pdf",
        vendor="Acme Supplies Pvt Ltd",
        invoice_number="ACME-2026-001",
        po_number="PO-1001",
        subtotal="10000.00",
        tax="1800.00",
        total="11800.00",
    )
    write_invoice(
        "02-duplicate-acme.pdf",
        vendor="Acme Supplies Pvt Ltd",
        invoice_number="ACME-2026-001",
        po_number="PO-1001",
        subtotal="10000.00",
        tax="1800.00",
        total="11800.00",
    )
    write_invoice(
        "03-exceeds-po.pdf",
        vendor="Acme Supplies Pvt Ltd",
        invoice_number="ACME-2026-002",
        po_number="PO-1001",
        subtotal="15000.00",
        tax="2700.00",
        total="17700.00",
    )
    write_invoice(
        "04-ambiguous-no-po.pdf",
        vendor="Orbit Office Solutions",
        invoice_number="ORB-2026-011",
        po_number=None,
        subtotal="4000.00",
        tax="720.00",
        total="4720.00",
    )
    print(f"Wrote sample invoices to {OUTPUT}")


if __name__ == "__main__":
    main()
