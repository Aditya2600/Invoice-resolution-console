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

CREATE TABLE IF NOT EXISTS po_invoice_allocations (
    allocation_id TEXT PRIMARY KEY,
    po_number TEXT NOT NULL REFERENCES purchase_orders(po_number) ON DELETE RESTRICT,
    document_id TEXT NOT NULL REFERENCES invoice_documents(document_id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES invoice_jobs(job_id) ON DELETE CASCADE,
    amount NUMERIC(14, 2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'RELEASED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ
);

-- One live allocation per document: a document can never consume PO balance twice.
CREATE UNIQUE INDEX IF NOT EXISTS uq_po_allocations_active_document
    ON po_invoice_allocations (document_id) WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_po_allocations_po
    ON po_invoice_allocations (po_number, status);

CREATE TABLE IF NOT EXISTS invoice_review_actions (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES invoice_jobs(job_id) ON DELETE CASCADE,
    reviewer_name TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('APPROVE', 'REJECT')),
    selected_po_number TEXT,
    corrections JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT NOT NULL,
    decision_before TEXT,
    decision_after TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invoice_review_actions_job
    ON invoice_review_actions (job_id, id);

-- Vendor + invoice-number identity with a lifecycle. invoice_duplicates is kept for
-- compatibility and is backfilled below; this ledger is the live source of truth.
CREATE TABLE IF NOT EXISTS invoice_identity_claims (
    claim_id TEXT PRIMARY KEY,
    vendor_normalized TEXT NOT NULL,
    invoice_number_normalized TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES invoice_documents(document_id) ON DELETE CASCADE,
    job_id TEXT REFERENCES invoice_jobs(job_id) ON DELETE SET NULL,
    invoice_total NUMERIC(14, 2),
    state TEXT NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'FINAL', 'RELEASED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalized_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    release_reason TEXT
);

-- Only one live claim per identity, so two workers cannot both hold it. RELEASED rows are
-- excluded, which is what lets a corrected re-upload claim the identity again.
CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_claims_active
    ON invoice_identity_claims (vendor_normalized, invoice_number_normalized)
    WHERE state IN ('PENDING', 'FINAL');

CREATE INDEX IF NOT EXISTS idx_identity_claims_document
    ON invoice_identity_claims (document_id, state);

ALTER TABLE invoice_jobs ADD COLUMN IF NOT EXISTS retry_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoice_jobs ADD COLUMN IF NOT EXISTS manual_retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoice_jobs ADD COLUMN IF NOT EXISTS last_retry_at TIMESTAMPTZ;
ALTER TABLE invoice_jobs ADD COLUMN IF NOT EXISTS last_retry_by TEXT;
ALTER TABLE invoice_jobs ADD COLUMN IF NOT EXISTS last_retry_actor_id TEXT;
ALTER TABLE invoice_jobs ADD COLUMN IF NOT EXISTS last_retry_actor_role TEXT;
ALTER TABLE invoice_review_actions ADD COLUMN IF NOT EXISTS actor_id TEXT;
ALTER TABLE invoice_review_actions ADD COLUMN IF NOT EXISTS actor_role TEXT;

-- The operations overview slices events by stage and by failure inside a time window; without
-- these it degrades to a sequential scan of the whole event log as history grows.
CREATE INDEX IF NOT EXISTS idx_invoice_events_stage_ts ON invoice_events (stage, ts);
CREATE INDEX IF NOT EXISTS idx_invoice_events_status_ts ON invoice_events (status, ts);
CREATE INDEX IF NOT EXISTS idx_invoice_jobs_updated ON invoice_jobs (updated_at);

ALTER TABLE invoice_results ADD COLUMN IF NOT EXISTS policy_snapshot JSONB;
ALTER TABLE invoice_results ADD COLUMN IF NOT EXISTS policy_hash TEXT;

-- Historical duplicates become FINAL claims. Deterministic claim_id keeps this a no-op on
-- every later startup; DO NOTHING also absorbs identities already claimed by the live ledger.
INSERT INTO invoice_identity_claims
  (claim_id, vendor_normalized, invoice_number_normalized, document_id, job_id,
   invoice_total, state, finalized_at)
SELECT
    'backfill:' || d.vendor_normalized || '|' || d.invoice_number_normalized,
    d.vendor_normalized,
    d.invoice_number_normalized,
    d.first_document_id,
    (SELECT j.job_id FROM invoice_jobs j WHERE j.document_id = d.first_document_id
     ORDER BY j.created_at LIMIT 1),
    d.first_total,
    'FINAL',
    d.first_seen_at
FROM invoice_duplicates d
ON CONFLICT DO NOTHING;
"""


def init_schema() -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
