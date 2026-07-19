from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.core import observability
from app.core.config import get_settings
from app.core.schemas import DecisionStatus, StageStatus
from app.db import repository
from app.pipeline.decision import evaluate_decision, match_purchase_order
from app.pipeline.normalizer import heuristic_extract
from app.services.medha import MedhaClient, MedhaError
from app.services.pdf import extract_pdf, inspect_pdf
from app.services.storage import file_path


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def process_job(job: dict[str, Any]) -> None:
    job_id = job["job_id"]
    document_id = job["document_id"]
    started = time.perf_counter()
    settings = get_settings()
    # Every log line this run emits carries the job id as its correlation id; the worker
    # handles one job per thread, so the context variable never straddles two runs.
    observability.request_id.set(job_id)
    model_name: str | None = None
    model_latency_ms: float | None = None

    repository.log_event(job_id, "invoice_received", StageStatus.PASS, reason="Worker claimed durable job.")
    try:
        document = repository.get_document(document_id)
        if not document:
            raise RuntimeError(f"Document {document_id} was not found")
        pdf_path = file_path(document["storage_key"])
        if not pdf_path.exists():
            raise RuntimeError("Stored invoice file was not found")

        validation_started = time.perf_counter()
        page_count = inspect_pdf(pdf_path)
        repository.log_event(
            job_id,
            "stage_pdf_validate",
            StageStatus.PASS,
            reason="PDF is readable and within page limit.",
            ms=_ms(validation_started),
            metrics={"page_count": page_count},
        )

        extraction_started = time.perf_counter()
        artifact_dir = settings.storage_dir / "artifacts" / document_id
        pdf = extract_pdf(pdf_path, artifact_dir)
        repository.log_event(
            job_id,
            "stage_text_extract",
            StageStatus.PASS,
            reason="Native text extracted." if not pdf.used_ocr else "Native text was weak; rendered pages for OCR/VLM extraction.",
            ms=_ms(extraction_started),
            metrics={"native_text_characters": len(pdf.native_text), "page_count": pdf.page_count},
            data={"used_ocr_fallback": pdf.used_ocr},
        )

        if pdf.used_ocr:
            repository.log_event(
                job_id,
                "stage_ocr_fallback",
                StageStatus.PASS if pdf.ocr_text else StageStatus.INFO,
                reason="PaddleOCR text available." if pdf.ocr_text else "Page images will be read directly by MEDHA; PaddleOCR is disabled or not needed.",
                metrics={"ocr_confidence": pdf.ocr_confidence},
            )

        raw_text = pdf.best_text
        medha = MedhaClient()
        if medha.is_configured:
            repository.extend_lease(job_id)
            model_started = time.perf_counter()
            try:
                extraction, model_latency_ms = medha.extract(page_images=pdf.page_images, raw_text=raw_text)
            except MedhaError as exc:
                # Recorded as a stage event so provider health is queryable from Postgres, not
                # only from a per-process counter. The exception still fails the job as before.
                repository.log_event(
                    job_id,
                    "stage_medha_extract",
                    StageStatus.FAIL,
                    reason=str(exc),
                    ms=_ms(model_started),
                    data={"error_code": exc.error_code, "provider": "medha"},
                )
                raise
            model_name = settings.medha_model
            repository.log_event(
                job_id,
                "stage_medha_extract",
                StageStatus.PASS,
                reason="MEDHA returned schema-valid invoice fields.",
                ms=_ms(model_started),
                metrics={"model_latency_ms": model_latency_ms, "confidence": extraction.extraction_confidence},
                data={"extraction": extraction.model_dump(mode="json")},
            )
        else:
            extraction = heuristic_extract(raw_text)
            repository.log_event(
                job_id,
                "stage_medha_extract",
                StageStatus.INFO,
                reason="MEDHA is not configured; used deterministic native-text fallback.",
                data={"extraction": extraction.model_dump(mode="json")},
            )

        duplicate = None
        if extraction.vendor_name and extraction.invoice_number:
            duplicate = repository.claim_semantic_invoice(
                document_id=document_id,
                job_id=job_id,
                vendor_name=extraction.vendor_name,
                invoice_number=extraction.invoice_number,
                total=extraction.total,
            )
        repository.log_event(
            job_id,
            "stage_semantic_duplicate",
            StageStatus.FAIL if duplicate else StageStatus.PASS,
            reason="A prior invoice identity exists." if duplicate else "No prior vendor/invoice identity exists.",
            data={"duplicate": duplicate or {}},
        )

        match = match_purchase_order(extraction)
        repository.log_event(
            job_id,
            "stage_po_match",
            StageStatus.PASS if match.po else StageStatus.FAIL,
            reason=match.reason,
            data={
                "matched_po": match.po.model_dump(mode="json") if match.po else None,
                "candidates": match.candidates,
                "score": match.score,
            },
        )

        decision = evaluate_decision(extraction, match, duplicate)

        # Balance consumption, the result row, job closure and the closing audit events are one
        # transaction; finalize downgrades to NEEDS_REVIEW if the PO balance moved under us.
        repository.finalize_invoice_decision(
            job_id=job_id,
            document_id=document_id,
            decision_status=decision.status,
            extraction=extraction.model_dump(mode="json"),
            matched_po=decision.matched_po.model_dump(mode="json") if decision.matched_po else None,
            reasons=decision.reasons,
            rule_checks=decision.rule_checks,
            model_name=model_name,
            model_latency_ms=model_latency_ms,
            allocation_amount=extraction.total if decision.status == DecisionStatus.APPROVED else None,
            total_ms=_ms(started),
            policy_snapshot=decision.policy_snapshot,
            policy_hash=decision.policy_hash,
        )
    except Exception as exc:
        repository.log_event(job_id, "invoice_closed", StageStatus.FAIL, reason=str(exc), ms=_ms(started))
        repository.fail_job(job_id, str(exc))
        raise

