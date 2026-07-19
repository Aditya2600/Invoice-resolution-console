"""Operational aggregates, metric label hygiene and provider instrumentation."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest

from app.core import observability
from app.db import repository
from app.services.medha import MedhaClient, MedhaError, MedhaTimeout
from tests.test_finalization import extraction


@pytest.fixture
def metrics():
    """A clean registry per test; the module-level one is shared by everything in the process."""
    observability.reset()
    yield observability
    observability.reset()


def sample(name: str, **labels: str) -> float:
    return observability._counters.get(observability._key(name, labels), 0.0)


def test_overview_reports_queue_decisions_retries_and_timings(make_job, make_po, db) -> None:
    approved_job, approved_document = make_job()
    repository.log_event(approved_job, "stage_pdf_validate", "PASS", reason="ok", ms=42.0)
    repository.finalize_invoice_decision(
        job_id=approved_job,
        document_id=approved_document,
        decision_status="APPROVED",
        extraction={**extraction("100"), "invoice_number": f"OPS-{uuid.uuid4().hex[:8]}"},
        matched_po=None,
        reasons=["ok"],
        rule_checks={},
        total_ms=1234.0,
    )
    retried_job, _ = make_job()
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE invoice_jobs SET status = 'FAILED', manual_retry_count = 2 WHERE job_id = %s",
            (retried_job,),
        )
        conn.commit()

    overview = repository.ops_overview(window_hours=24)

    assert overview["window_hours"] == 24
    assert overview["queue"]["FAILED"] >= 1
    assert overview["queue"]["COMPLETED"] >= 1
    assert overview["decisions"]["APPROVED"] >= 1
    assert overview["reliability"]["failed_jobs"] >= 1
    assert overview["reliability"]["manual_retries"] >= 2
    assert overview["reliability"]["retried_jobs"] >= 1

    # invoice_closed carries the end-to-end duration, so "completed" is the end-to-end percentile.
    assert overview["processing_ms"]["p50"] is not None
    assert overview["processing_ms"]["samples"] >= 1
    stages = {row["stage"]: row for row in overview["stages"]}
    assert "document_read" in stages and stages["document_read"]["p50_ms"] is not None
    # Internal stage names never leak into the operational view; only the friendly eight do.
    assert set(stages) <= set(observability.FRIENDLY_STAGES)
    assert isinstance(overview["top_failures"], list) and len(overview["top_failures"]) <= 5
    assert "fallback_rate" in overview["ocr"]
    assert set(overview["medha"]) == {"success", "timeout", "error", "p50_ms", "p95_ms"}


def test_overview_window_is_bounded(make_job) -> None:
    assert repository.ops_overview(window_hours=0)["window_hours"] == 1
    assert repository.ops_overview(window_hours=100_000)["window_hours"] == 168


def test_metrics_refuse_unbounded_or_invoice_labels(metrics) -> None:
    """A job id, document id or vendor name as a label would be both unbounded cardinality
    and invoice content in a store that is scraped and cached. Both must be impossible."""
    for labels in (
        {"job_id": str(uuid.uuid4())},
        {"stage": "received", "document_id": "d-1"},
        {"stage": "Globex Ltd", "outcome": "PASS"},
        {"stage": "received", "outcome": "Invoice GLX-1 total 100"},
    ):
        with pytest.raises(ValueError):
            metrics.count("invoice_stage_events_total", **labels)

    with pytest.raises(ValueError):
        metrics.count("invoice_decisions_total", decision="Globex Ltd")


def test_rendered_metrics_expose_only_declared_labels(metrics, make_job) -> None:
    job_id, _ = make_job()
    vendor = f"Globex-{uuid.uuid4().hex[:8]}"
    invoice_number = f"GLX-{uuid.uuid4().hex[:8]}"
    repository.log_event(
        job_id,
        "stage_medha_extract",
        "PASS",
        reason=f"Read invoice {invoice_number} from {vendor}.",
        ms=90.0,
        data={"extraction": {"vendor_name": vendor, "invoice_number": invoice_number}},
    )

    text = metrics.render()

    # The event carried the vendor, the invoice number and the job id; none may reach the scrape.
    for secret in (vendor, invoice_number, job_id):
        assert secret not in text
    assert 'invoice_stage_events_total{outcome="PASS",stage="ai_extraction"} 1.0' in text
    assert "invoice_stage_duration_ms_count{stage=\"ai_extraction\"} 1" in text
    label_names = {
        pair.split("=")[0]
        for line in text.splitlines()
        if not line.startswith("#") and "{" in line
        for pair in line.split("{", 1)[1].split("}", 1)[0].split(",")
    }
    assert label_names <= set(observability.LABEL_VALUES) | {"le"}


def test_medha_timeout_and_error_are_counted_separately(metrics, monkeypatch, tmp_path) -> None:
    client = MedhaClient()
    monkeypatch.setattr(client.settings, "enable_medha", True)
    monkeypatch.setattr(client.settings, "medha_api_url", "http://medha.invalid/v1")
    monkeypatch.setattr(client.settings, "medha_max_retries", 0)

    def timing_out(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx.Client, "post", timing_out)
    with pytest.raises(MedhaTimeout):
        client.extract(page_images=[], raw_text="invoice")
    assert sample("invoice_provider_requests_total", provider="medha", outcome="timeout") == 1

    def refusing(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.Client, "post", refusing)
    with pytest.raises(MedhaError):
        client.extract(page_images=[], raw_text="invoice")
    assert sample("invoice_provider_requests_total", provider="medha", outcome="error") == 1
    assert sample("invoice_provider_requests_total", provider="medha", outcome="timeout") == 1


def test_ocr_fallback_is_counted_per_document(metrics, tmp_path) -> None:
    from app.services.pdf import extract_pdf

    import fitz

    scanned = tmp_path / "scanned.pdf"
    document = fitz.open()
    document.new_page()  # a page with no text layer forces the OCR path
    document.save(str(scanned))
    document.close()

    extract_pdf(scanned, tmp_path / "artifacts")

    assert sample("invoice_provider_requests_total", provider="ocr", outcome="fallback") == 1
    assert sample("invoice_provider_requests_total", provider="ocr", outcome="native") == 0


def test_instrumentation_leaves_decisions_and_allocations_unchanged(
    make_po, make_job, allocations, po_row
) -> None:
    """P0/P1/P2A semantics are untouched: one allocation, consumed balance, a FINAL claim."""
    po_number = make_po(total="500")
    job_id, document_id = make_job()
    invoice_number = f"OPS-{uuid.uuid4().hex[:8]}"
    repository.claim_semantic_invoice(
        document_id=document_id,
        job_id=job_id,
        vendor_name="Globex Ltd",
        invoice_number=invoice_number,
        total=Decimal("100"),
    )

    outcome = repository.finalize_invoice_decision(
        job_id=job_id,
        document_id=document_id,
        decision_status="APPROVED",
        extraction={**extraction("100"), "invoice_number": invoice_number},
        matched_po={"po_number": po_number},
        reasons=["Matched the purchase order."],
        rule_checks={},
        allocation_amount=Decimal("100"),
    )

    assert outcome["decision_status"] == "APPROVED"
    assert len(allocations(po_number)) == 1
    assert po_row(po_number)["consumed_amount"] == Decimal("100")
    assert repository.get_identity_claim(document_id)["state"] == "FINAL"
