from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx

from app.core import observability
from app.core.config import get_settings
from app.core.schemas import InvoiceExtraction
from app.pipeline.normalizer import coerce_model_extraction, parse_model_json


class MedhaError(RuntimeError):
    """MEDHA could not return a usable extraction."""

    error_code = "medha_error"


class MedhaTimeout(MedhaError):
    """MEDHA did not answer within the configured timeout."""

    error_code = "medha_timeout"


SYSTEM_PROMPT = """You extract invoice facts for an AP workflow. Return JSON only.
Never invent a value. Use null for absent/uncertain values. Amounts must be numbers, not strings.
Extract vendor_name, invoice_number, invoice_date (YYYY-MM-DD), po_number, currency,
subtotal, tax, total, line_items, extraction_confidence (0..1), and evidence.
Each evidence item must contain field, page, quote, confidence. Do not make an approval decision.
"""


class MedhaClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.enable_medha and self.settings.medha_api_url)

    def extract(self, *, page_images: list[Path], raw_text: str) -> tuple[InvoiceExtraction, float]:
        if not self.is_configured:
            raise RuntimeError("MEDHA is not configured")

        content: list[dict] = [
            {
                "type": "text",
                "text": f"Native/OCR text, which may be incomplete:\n{raw_text[:16000]}",
            }
        ]
        for image in page_images:
            data = base64.b64encode(image.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{data}"},
                }
            )

        payload: dict = {
            "model": self.settings.medha_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "max_tokens": 1800,
        }
        if self.settings.medha_json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.settings.medha_api_key:
            headers["Authorization"] = f"Bearer {self.settings.medha_api_key}"

        endpoint = self.settings.medha_api_url.rstrip("/") + "/chat/completions"
        started = time.perf_counter()
        last_error: Exception | None = None
        # One provider_call spans every retry: an extraction either produced a result or it did
        # not, and a per-attempt count would read as more traffic than the pipeline generates.
        with observability.provider_call("medha") as call:
            for attempt in range(self.settings.medha_max_retries + 1):
                try:
                    with httpx.Client(timeout=self.settings.medha_timeout_seconds) as client:
                        response = client.post(endpoint, headers=headers, json=payload)
                        response.raise_for_status()
                    body = response.json()
                    content_text = body["choices"][0]["message"]["content"]
                    extraction = coerce_model_extraction(parse_model_json(content_text), raw_text)
                    return extraction, (time.perf_counter() - started) * 1000
                except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
                    last_error = exc
                    if attempt >= self.settings.medha_max_retries:
                        break
            if isinstance(last_error, httpx.TimeoutException):
                call["outcome"] = "timeout"
                raise MedhaTimeout(f"MEDHA timed out: {last_error}")
            call["outcome"] = "error"
            raise MedhaError(f"MEDHA extraction failed: {last_error}")

