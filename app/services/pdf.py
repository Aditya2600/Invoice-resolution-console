from __future__ import annotations

import io
import time
from dataclasses import dataclass
from pathlib import Path

import fitz
from PIL import Image

from app.core.config import get_settings


@dataclass
class PdfExtraction:
    page_count: int
    native_text: str
    page_images: list[Path]
    used_ocr: bool
    ocr_text: str
    ocr_confidence: float | None

    @property
    def best_text(self) -> str:
        return self.native_text if len(self.native_text.strip()) >= get_settings().native_text_min_chars else self.ocr_text


def inspect_pdf(path: Path) -> int:
    with fitz.open(path) as document:
        pages = len(document)
    if pages == 0:
        raise ValueError("PDF has no pages")
    if pages > get_settings().max_pdf_pages:
        raise ValueError(f"PDF has {pages} pages; maximum is {get_settings().max_pdf_pages}")
    return pages


def extract_pdf(path: Path, artifact_directory: Path) -> PdfExtraction:
    artifact_directory.mkdir(parents=True, exist_ok=True)
    native_text_parts: list[str] = []
    image_paths: list[Path] = []
    with fitz.open(path) as document:
        for index, page in enumerate(document):
            native_text_parts.append(page.get_text("text"))
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_path = artifact_directory / f"page-{index + 1}.png"
            pixmap.save(str(image_path))
            image_paths.append(image_path)

    native_text = "\n".join(native_text_parts).strip()
    used_ocr = len(native_text) < get_settings().native_text_min_chars
    ocr_text = ""
    ocr_confidence: float | None = None
    if used_ocr and get_settings().enable_paddle_ocr:
        ocr_text, ocr_confidence = run_paddle_ocr(image_paths)

    return PdfExtraction(
        page_count=len(image_paths),
        native_text=native_text,
        page_images=image_paths,
        used_ocr=used_ocr,
        ocr_text=ocr_text,
        ocr_confidence=ocr_confidence,
    )


def run_paddle_ocr(image_paths: list[Path]) -> tuple[str, float | None]:
    """Optional local OCR. Keeps the MVP runnable without installing PaddleOCR."""
    try:
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "PaddleOCR is enabled but not installed. Install requirements-ocr.txt or disable ENABLE_PADDLE_OCR."
        ) from exc

    ocr = PaddleOCR(use_angle_cls=True, lang="en")
    text_parts: list[str] = []
    confidences: list[float] = []
    for image_path in image_paths:
        result = ocr.ocr(str(image_path), cls=True)
        for page in result or []:
            for line in page or []:
                text, confidence = line[1]
                text_parts.append(text)
                confidences.append(float(confidence))
    confidence = sum(confidences) / len(confidences) if confidences else None
    return "\n".join(text_parts), confidence

