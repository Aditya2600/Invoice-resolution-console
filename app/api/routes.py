from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.db import repository
from app.services.pdf import inspect_pdf
from app.services.storage import file_path, save_uploaded_pdf


router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/purchase-orders/import")
async def import_purchase_orders(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a CSV with PO master data.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The CSV is empty.")
    try:
        result = repository.import_purchase_orders(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid PO CSV: {exc}") from exc
    return {"message": "Purchase orders imported.", **result}


@router.post("/invoices/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_invoice(file: UploadFile = File(...)) -> dict:
    settings = get_settings()
    filename = Path(file.filename or "invoice.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF invoices are accepted.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded PDF is empty.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"PDF exceeds {settings.max_upload_mb} MB limit.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="The file does not look like a valid PDF.")

    storage_key, digest = save_uploaded_pdf(file, data)
    path = file_path(storage_key)
    try:
        page_count = inspect_pdf(path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid PDF: {exc}") from exc

    job, created = repository.create_document_and_job(
        file_name=filename,
        storage_key=storage_key,
        sha256=digest,
        content_type=file.content_type or "application/pdf",
        byte_size=len(data),
        page_count=page_count,
    )
    if not created:
        # The random local file created for a duplicate upload is unnecessary.
        path.unlink(missing_ok=True)
    return {
        "message": "Invoice queued." if created else "This exact PDF was already queued or processed.",
        "created": created,
        "job": job,
    }


@router.get("/jobs")
def list_jobs(limit: int = 50) -> dict:
    return {"jobs": repository.list_jobs(limit=min(max(limit, 1), 200))}


@router.get("/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    detail = repository.get_job_detail(job_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Job not found.")
    return detail


@router.get("/documents/{document_id}/file")
def download_document(document_id: str) -> FileResponse:
    document = repository.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    path = file_path(document["storage_key"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored file not found.")
    return FileResponse(path, media_type=document["content_type"], filename=document["file_name"])


@router.post("/demo/seed-purchase-orders")
def seed_purchase_orders() -> dict:
    data_path = Path(__file__).resolve().parents[2] / "data" / "purchase_orders.csv"
    return {"message": "Sample purchase orders imported.", **repository.import_purchase_orders(data_path.read_bytes())}

