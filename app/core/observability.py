"""Prometheus-compatible metrics and structured logs.

Two rules hold this module together:

1. **Metric labels are bounded and never carry invoice data.** Every label name and every label
   value is declared up front; anything else raises. A job_id, document_id, vendor name or model
   response as a label would give the metric unbounded cardinality and leak invoice content into
   a store that is scraped, cached and rarely access-controlled. Per-job facts belong in the
   structured log and in Postgres, which is what /api/ops/overview reads.

2. **The registry is per-process.** ponytail: no prometheus_client dependency and no shared
   store, so /metrics reports the process that serves it. The API and the worker run separately,
   so cross-process truth is queried from Postgres rather than summed from counters.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Any, Iterator


logger = logging.getLogger("invoice")

# The eight stages an operator reads. Internal stage names stay in the event payload and the
# "Technical details" pane; they never become a metric label.
FRIENDLY_STAGES = (
    "received",
    "document_read",
    "text_or_ocr",
    "ai_extraction",
    "duplicate_check",
    "po_validation",
    "decision",
    "completed",
)

STAGE_MAP = {
    "invoice_received": "received",
    "stage_pdf_validate": "document_read",
    "stage_text_extract": "text_or_ocr",
    "stage_ocr_fallback": "text_or_ocr",
    "stage_medha_extract": "ai_extraction",
    "stage_semantic_duplicate": "duplicate_check",
    "stage_po_match": "po_validation",
    "stage_policy_validate": "decision",
    "stage_review_resolve": "decision",
    "stage_retry_requested": "received",
    "invoice_closed": "completed",
}


def friendly_stage(stage: str) -> str:
    """Bounded stage label. An unknown internal stage collapses to "other" rather than
    minting a new time series for every string a caller happens to pass."""
    return STAGE_MAP.get(stage, "other")


# Every label value that may ever be exported, per label name.
LABEL_VALUES: dict[str, set[str]] = {
    "stage": set(FRIENDLY_STAGES) | {"other"},
    "outcome": {"PASS", "FAIL", "INFO", "success", "error", "timeout", "native", "fallback"},
    "decision": {"APPROVED", "NEEDS_REVIEW", "REJECTED"},
    "provider": {"medha", "ocr"},
}

BUCKETS_MS = (50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0, 30000.0, 60000.0)

# name -> (type, help, label names)
METRICS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "invoice_stage_events_total": ("counter", "Pipeline stage transitions.", ("stage", "outcome")),
    "invoice_stage_duration_ms": ("histogram", "Pipeline stage duration in milliseconds.", ("stage",)),
    "invoice_decisions_total": ("counter", "Finalized invoice decisions.", ("decision",)),
    "invoice_jobs_failed_total": ("counter", "Jobs that ended without a decision.", ()),
    "invoice_provider_requests_total": (
        "counter",
        "Calls to an extraction provider.",
        ("provider", "outcome"),
    ),
    "invoice_provider_duration_ms": (
        "histogram",
        "Extraction provider latency in milliseconds.",
        ("provider",),
    ),
    "invoice_http_requests_total": ("counter", "HTTP requests served.", ("outcome",)),
}

_lock = Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_histograms: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}


def _key(name: str, labels: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
    if name not in METRICS:
        raise ValueError(f"Unknown metric {name!r}")
    declared = METRICS[name][2]
    if set(labels) != set(declared):
        raise ValueError(f"{name} takes labels {declared}, got {tuple(labels)}")
    for label, value in labels.items():
        if value not in LABEL_VALUES[label]:
            raise ValueError(f"{label}={value!r} is not an allowed value for {name}")
    return name, tuple(sorted(labels.items()))


def count(name: str, amount: float = 1.0, **labels: str) -> None:
    key = _key(name, labels)
    with _lock:
        _counters[key] = _counters.get(key, 0.0) + amount


def observe(name: str, milliseconds: float, **labels: str) -> None:
    key = _key(name, labels)
    with _lock:
        entry = _histograms.setdefault(
            key, {"count": 0, "sum": 0.0, "buckets": dict.fromkeys(BUCKETS_MS, 0)}
        )
        entry["count"] += 1
        entry["sum"] += milliseconds
        for bound in BUCKETS_MS:
            if milliseconds <= bound:
                entry["buckets"][bound] += 1


def reset() -> None:
    """Test-only: start from an empty registry."""
    with _lock:
        _counters.clear()
        _histograms.clear()


def _labels_text(labels: tuple[tuple[str, str], ...], extra: tuple[str, str] | None = None) -> str:
    pairs = list(labels) + ([extra] if extra else [])
    if not pairs:
        return ""
    return "{" + ",".join(f'{key}="{value}"' for key, value in pairs) + "}"


def render() -> str:
    """The Prometheus text exposition format, version 0.0.4."""
    with _lock:
        counters = dict(_counters)
        histograms = {key: dict(value) for key, value in _histograms.items()}

    lines: list[str] = []
    for name, (kind, help_text, _) in METRICS.items():
        samples = [item for item in (counters if kind == "counter" else histograms) if item[0] == name]
        if not samples:
            continue
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for key in samples:
            labels = key[1]
            if kind == "counter":
                lines.append(f"{name}{_labels_text(labels)} {counters[key]}")
                continue
            entry = histograms[key]
            for bound in BUCKETS_MS:
                # Already cumulative: observe() increments every bucket the sample falls under.
                lines.append(f"{name}_bucket{_labels_text(labels, ('le', str(bound)))} {entry['buckets'][bound]}")
            lines.append(f"{name}_bucket{_labels_text(labels, ('le', '+Inf'))} {entry['count']}")
            lines.append(f"{name}_sum{_labels_text(labels)} {entry['sum']}")
            lines.append(f"{name}_count{_labels_text(labels)} {entry['count']}")
    return "\n".join(lines) + "\n"


# --- structured logs -------------------------------------------------------------------

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


@contextmanager
def request_context(value: str | None = None) -> Iterator[str]:
    token_value = value or str(uuid.uuid4())
    token = request_id.set(token_value)
    try:
        yield token_value
    finally:
        request_id.reset(token)


def log(event: str, **fields: Any) -> None:
    """One JSON line per operational fact.

    Unlike metrics, a log line may carry identifiers: it is written once per event, not held
    open as a time series, and it is what an operator greps when a specific run went wrong.
    """
    record = {"event": event, "request_id": request_id.get(), **fields}
    logger.info(json.dumps({key: value for key, value in record.items() if value is not None}))


def record_stage(
    *,
    job_id: str,
    stage: str,
    outcome: str,
    duration_ms: float | None = None,
    document_id: str | None = None,
    error_code: str | None = None,
) -> None:
    """The single funnel every pipeline transition passes through: one metric, one log line."""
    label = friendly_stage(stage)
    count("invoice_stage_events_total", stage=label, outcome=outcome)
    if duration_ms is not None:
        observe("invoice_stage_duration_ms", duration_ms, stage=label)
    log(
        "stage",
        job_id=job_id,
        document_id=document_id,
        stage=label,
        internal_stage=stage,
        outcome=outcome,
        duration_ms=duration_ms,
        error_code=error_code,
    )


@contextmanager
def provider_call(provider: str) -> Iterator[dict[str, str]]:
    """Times a Medha/OCR call and records success, error or timeout exactly once.

    The caller sets result["outcome"] to distinguish a timeout from any other failure; an
    exception that escapes without one is recorded as a plain error.
    """
    result = {"outcome": "success"}
    started = time.perf_counter()
    try:
        yield result
    except BaseException:
        if result["outcome"] == "success":
            result["outcome"] = "error"
        raise
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        count("invoice_provider_requests_total", provider=provider, outcome=result["outcome"])
        observe("invoice_provider_duration_ms", elapsed, provider=provider)
        log("provider", provider=provider, outcome=result["outcome"], duration_ms=round(elapsed, 2))
