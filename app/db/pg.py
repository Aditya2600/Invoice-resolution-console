from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings


@lru_cache
def get_pool() -> ConnectionPool:
    return ConnectionPool(
        conninfo=get_settings().database_url,
        kwargs={"row_factory": dict_row},
        min_size=1,
        max_size=8,
        open=True,
    )


@contextmanager
def connection() -> Connection:
    with get_pool().connection() as conn:
        yield conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS invoice_documents (
    document_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    byte_size BIGINT NOT NULL,
    page_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoice_jobs (
    job_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES invoice_documents(document_id) ON DELETE CASCADE,
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_until TIMESTAMPTZ,
    last_error TEXT,
    decision_status TEXT CHECK (decision_status IN ('APPROVED', 'NEEDS_REVIEW', 'REJECTED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, policy_version)
);

CREATE INDEX IF NOT EXISTS idx_invoice_jobs_claim
    ON invoice_jobs (status, lease_until, created_at);

CREATE TABLE IF NOT EXISTS invoice_events (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES invoice_jobs(job_id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    stage TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PASS', 'FAIL', 'INFO')),
    reason TEXT,
    ms DOUBLE PRECISION,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    data JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_invoice_events_job ON invoice_events (job_id, id);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_number TEXT PRIMARY KEY,
    vendor_name TEXT NOT NULL,
    vendor_normalized TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    total_amount NUMERIC(14, 2) NOT NULL,
    consumed_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_purchase_orders_vendor
    ON purchase_orders (vendor_normalized, status);

CREATE TABLE IF NOT EXISTS invoice_duplicates (
    vendor_normalized TEXT NOT NULL,
    invoice_number_normalized TEXT NOT NULL,
    first_document_id TEXT NOT NULL REFERENCES invoice_documents(document_id) ON DELETE CASCADE,
    first_total NUMERIC(14, 2),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (vendor_normalized, invoice_number_normalized)
);

CREATE TABLE IF NOT EXISTS invoice_results (
    document_id TEXT PRIMARY KEY REFERENCES invoice_documents(document_id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES invoice_jobs(job_id) ON DELETE CASCADE,
    decision_status TEXT NOT NULL CHECK (decision_status IN ('APPROVED', 'NEEDS_REVIEW', 'REJECTED')),
    extraction JSONB NOT NULL,
    matched_po JSONB,
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    rule_checks JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_name TEXT,
    model_latency_ms DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def init_schema() -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()

