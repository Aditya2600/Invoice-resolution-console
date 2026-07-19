from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.core import observability
from app.core.auth import Actor, admin_access, current_actor, read_access, review_access, upload_access
from app.core.config import get_settings
from app.core.schemas import RetryRequest, ReviewResolveRequest
from app.db import repository
from app.pipeline import review
from app.services.pdf import PdfValidationError, inspect_pdf
from app.services.storage import UploadTooLarge, commit_quarantined_pdf, file_path, quarantine_upload


router = APIRouter(prefix="/api")


def _safe_pdf_filename(value: str | None) -> str:
    leaf = (value or "invoice.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in leaf if ord(character) >= 32 and ord(character) != 127)
    cleaned = cleaned.replace('"', "").replace(";", "").strip(" .")[:180] or "invoice.pdf"
    return cleaned if cleaned.lower().endswith(".pdf") else f"{cleaned}.pdf"


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/auth/me")
def authenticated_actor(actor: Annotated[Actor, Depends(current_actor)]) -> dict[str, str]:
    return {
        "subject": actor.subject,
        "display_name": actor.display_name,
        "role": actor.role,
        "auth_mode": get_settings().auth_mode,
    }


@router.post("/purchase-orders/import")
async def import_purchase_orders(
    _: Annotated[Actor, Depends(admin_access)],
    file: UploadFile = File(...),
) -> dict:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV with PO master data.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The CSV is empty.")
    try:
        result = repository.import_purchase_orders(data)
    except (UnicodeDecodeError, ValueError) as exc:
        observability.log("po_import_rejected", error_code="invalid_csv", error_type=type(exc).__name__)
        raise HTTPException(status_code=422, detail="The purchase-order CSV is malformed.") from exc
    return {"message": "Purchase orders imported.", **result}


@router.post("/invoices/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_invoice(
    _: Annotated[Actor, Depends(upload_access)],
    file: UploadFile = File(...),
) -> dict:
    filename = _safe_pdf_filename(file.filename)

    quarantine_path: Path | None = None
    committed_path: Path | None = None
    keep_committed_file = False
    try:
        quarantine_path, digest, byte_size = quarantine_upload(file)
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"The PDF exceeds the {get_settings().upload_limit_bytes}-byte upload limit.",
        ) from exc
    except Exception as exc:
        observability.log("upload_rejected", error_code="upload_stream_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=422, detail="The upload could not be read.") from exc

    try:
        if byte_size == 0:
            raise PdfValidationError("pdf_empty_upload", "The uploaded PDF is empty.")
        with quarantine_path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise PdfValidationError("pdf_magic", "The uploaded file is not a PDF.")
        page_count = inspect_pdf(quarantine_path)
        storage_key = commit_quarantined_pdf(quarantine_path, digest)
        quarantine_path = None
        committed_path = file_path(storage_key)
        job, created = repository.create_document_and_job(
            file_name=filename,
            storage_key=storage_key,
            sha256=digest,
            content_type="application/pdf",
            byte_size=byte_size,
            page_count=page_count,
        )
        if not created:
            committed_path.unlink(missing_ok=True)
            committed_path = None
        else:
            keep_committed_file = True
    except PdfValidationError as exc:
        observability.log(
            "upload_rejected",
            error_code=exc.code,
            error_type=type(exc.__cause__ or exc).__name__,
        )
        raise HTTPException(status_code=422, detail=exc.detail) from exc
    except Exception as exc:
        observability.log("upload_failed", error_code="upload_persist_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail="The validated PDF could not be queued.") from exc
    finally:
        if quarantine_path is not None:
            quarantine_path.unlink(missing_ok=True)
        if committed_path is not None and not keep_committed_file:
            committed_path.unlink(missing_ok=True)

    return {
        "message": "Invoice queued." if created else "This exact PDF was already queued or processed.",
        "created": created,
        "job": job,
    }


@router.get("/ops/overview")
def ops_overview(_: Annotated[Actor, Depends(read_access)], window_hours: int = 24) -> dict:
    """Read-only operational aggregates for the dashboard. Every figure comes from Postgres."""
    return repository.ops_overview(window_hours)


@router.get("/jobs")
def list_jobs(_: Annotated[Actor, Depends(read_access)], limit: int = 50) -> dict:
    return {"jobs": repository.list_jobs(limit=min(max(limit, 1), 200))}


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, _: Annotated[Actor, Depends(read_access)]) -> dict:
    detail = repository.get_job_detail(job_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Job not found.")
    return detail


@router.get("/jobs/{job_id}/review/candidates")
def review_candidates(job_id: str, _: Annotated[Actor, Depends(read_access)]) -> dict:
    """Open purchase orders a reviewer may pick for this invoice, with live remaining balance."""
    detail = repository.get_job_detail(job_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Job not found.")
    extraction = (detail["result"] or {}).get("extraction") or {}
    candidates = repository.find_purchase_orders(po_number=None, vendor_name=extraction.get("vendor_name"))
    return {"candidates": [po.model_dump(mode="json") for po in candidates]}


@router.post("/jobs/{job_id}/review/resolve")
def resolve_review(
    job_id: str,
    body: ReviewResolveRequest,
    actor: Annotated[Actor, Depends(review_access)],
) -> dict:
    try:
        outcome = review.resolve_review(
            job_id=job_id,
            action=body.action,
            reviewer_name=actor.display_name,
            note=body.note.strip(),
            selected_po_number=body.selected_po_number.strip().upper() if body.selected_po_number else None,
            corrections=body.corrections.model_dump(mode="json", exclude_none=True) if body.corrections else {},
            actor_id=actor.subject,
            actor_role=actor.role,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (repository.ReviewConflict, repository.IdentityConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except review.ReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"message": f"Invoice resolved as {outcome['decision_status']}.", **outcome}


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    actor: Annotated[Actor, Depends(review_access)],
    body: RetryRequest | None = None,
) -> dict:
    """Re-queue a failed run. Completed runs are terminal and are refused with 409."""
    request = body or RetryRequest()
    try:
        outcome = repository.retry_job(
            job_id,
            requested_by=actor.display_name,
            note=request.note.strip() if request.note else None,
            actor_id=actor.subject,
            actor_role=actor.role,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.RetryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"message": "Invoice re-queued for processing.", **outcome}


@router.get("/documents/{document_id}/file")
def download_document(
    document_id: str,
    _: Annotated[Actor, Depends(read_access)],
) -> FileResponse:
    document = repository.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        path = file_path(document["storage_key"])
    except ValueError:
        raise HTTPException(status_code=404, detail="Stored file not found.") from None
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored file not found.")
    filename = _safe_pdf_filename(document["file_name"])
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
        headers={
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.post("/demo/seed-purchase-orders")
def seed_purchase_orders(_: Annotated[Actor, Depends(admin_access)]) -> dict:
    data_path = Path(__file__).resolve().parents[2] / "data" / "purchase_orders.csv"
    return {"message": "Sample purchase orders imported.", **repository.import_purchase_orders(data_path.read_bytes())}
