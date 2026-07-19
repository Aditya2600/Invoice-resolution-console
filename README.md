# Invoice Resolution Console

A runnable take-home case-study project for **PS-1: Invoice processing — from PDF to decision**.

The console accepts a real invoice PDF, matches it against a real purchase-order CSV, and creates an auditable decision:

- `APPROVED`
- `NEEDS_REVIEW`
- `REJECTED`

It uses MEDHA (Gemma 4 26B served through vLLM) for structured extraction when configured, while keeping PO matching and finance decisions deterministic in Python. A human reviewer can resolve `NEEDS_REVIEW` invoices from the dashboard; that resolution re-runs the same deterministic policy engine instead of trusting a free-form human verdict.

## Why this architecture

- PostgreSQL is both the durable application database and the job queue — no separate broker.
- Workers safely claim jobs with `FOR UPDATE SKIP LOCKED` plus a lease, so a crashed worker's job is retried by another worker instead of hanging forever.
- Every stage writes an append-only `invoice_events` record, which is what the dashboard's live timeline is built from.
- Exact-file duplicates (same PDF bytes, via `sha256`) and business-level duplicates (same vendor + invoice number) are deliberately separate checks.
- The model extracts facts; Python rules make financial decisions. MEDHA is never allowed to approve or reject an invoice.
- PO-balance consumption, the result row, and job closure happen in **one Postgres transaction**, so two workers racing on the same PO can never over-allocate it.
- Every decision stores a frozen `policy_snapshot` + `policy_hash` of the vendor rule values it was judged under, so a later change to `vendor_rules.json` can never rewrite history.
- A vendor+invoice-number "identity claim" has its own lifecycle (`PENDING → FINAL` / `RELEASED`), decoupled from the job's own status, so failed/rejected runs release the identity for a corrected re-upload instead of blocking it forever.

## Architecture (components)

```text
┌───────────────────────────┐        ┌──────────────────────────────┐
│   Frontend (React/Vite)   │        │        MEDHA (vLLM)           │
│  Dashboard · Process ·    │        │  OpenAI-compatible endpoint   │
│  Run Detail · Settings    │        │  page images + raw text -> JSON│
└─────────────┬─────────────┘        └───────────────▲────────────────┘
              │ HTTP (fetch)                          │ HTTPS
              ▼                                        │
┌───────────────────────────────────────────────────────────────────┐
│                      FastAPI app  (app/api/routes.py)              │
│  /purchase-orders/import   /invoices/upload   /jobs   /jobs/{id}   │
│  /jobs/{id}/review/*       /jobs/{id}/retry   /documents/{id}/file │
└─────────────┬───────────────────────────────────────┬──────────────┘
              │ writes document + PENDING job          │ reads job/events/result
              ▼                                        │
┌───────────────────────────────────────────────────────────────────┐
│                          PostgreSQL                                │
│  invoice_documents · invoice_jobs (durable queue) · invoice_events │
│  purchase_orders · po_invoice_allocations · invoice_results        │
│  invoice_identity_claims · invoice_review_actions                  │
└─────────────▲───────────────────────────────────────────────────────┘
              │ FOR UPDATE SKIP LOCKED claim + lease
              │
┌─────────────┴─────────────────────────────────────────────────────┐
│                  Worker  (app/worker.py, polling loop)             │
│      app/pipeline/orchestrator.py -> process_job(job)               │
│                                                                      │
│  services/pdf.py        services/medha.py       pipeline/decision.py│
│  PyMuPDF text +          MEDHA client /           vendor rules,     │
│  page render + optional  chat/completions         PO matching,      │
│  PaddleOCR fallback                                policy verdict    │
└───────────────────────────────────────────────────────────────────┘
```

Filesystem storage (`storage/`) holds the uploaded PDF bytes and rendered page-image artifacts; only the storage key is kept in Postgres.

## Processing flow (one invoice, start to finish)

```text
 1. Upload PO CSV  ─────────────────────────►  purchase_orders table (upsert by po_number)

 2. Upload invoice PDF
       │
       ├─ reject if not a PDF / too large / unreadable
       ├─ sha256 hash                      → duplicate exact-file upload returns the existing job
       └─ create invoice_documents row + invoice_jobs row (status = PENDING)
              │
              ▼
 3. Worker polls, claims job (SKIP LOCKED, lease) → status = PROCESSING
              │
              ▼
 4. stage_pdf_validate      PyMuPDF opens PDF, checks page count ≤ MAX_PDF_PAGES
              │
              ▼
 5. stage_text_extract      native PyMuPDF text; if too short → render page PNGs
              │                                  (weak text also triggers OCR fallback)
              ▼
 6. stage_ocr_fallback      PaddleOCR reads the rendered pages
              │
              ▼
 7. stage_medha_extract     MEDHA reads text + page images,
              │             returns strict JSON: vendor, invoice#, PO#, amounts,
              │             line items, confidence, per-field evidence
              │             — else: deterministic heuristic_extract() fallback
              ▼
 8. stage_semantic_duplicate  claim (vendor_normalized, invoice_number_normalized)
              │               identity; a live PENDING/FINAL claim blocks/duplicates this run
              ▼
 9. stage_po_match          exact PO#, single open PO, or "only one affordable PO" match;
              │             multiple/no unambiguous match → NEEDS_REVIEW candidate list
              ▼
10. evaluate_decision()     required fields, currency, arithmetic tolerance,
              │             confidence threshold, duplicate state, PO open/vendor/
              │             currency/remaining-balance checks → policy snapshot + hash
              ▼
11. finalize_invoice_decision()   ONE transaction:
              │                   - reserve PO balance (only if APPROVED)
              │                   - write invoice_results
              │                   - close job (status = COMPLETED)
              │                   - settle identity claim (FINAL / RELEASED / stays PENDING)
              │                   - write stage_policy_validate + invoice_closed events
              ▼
12. Dashboard shows live stage timeline, evidence, rule checks, and final outcome.
        If NEEDS_REVIEW: reviewer approves (re-runs evaluate_decision under the
        reviewer's PO choice/corrections) or rejects — never a free-form verdict.
        If FAILED: an operator can retry, which re-queues under a new retry_generation.
```

## Repository layout

```text
app/
  api/routes.py            FastAPI endpoints
  core/config.py           pydantic-settings environment config
  core/schemas.py          Pydantic models (extraction, decision, requests)
  db/pg.py                 connection pool + schema DDL (idempotent, run on startup)
  db/repository.py         all SQL: queue claim, finalize transaction, identity claims, retry
  pipeline/normalizer.py   name/invoice-number normalization, heuristic fallback extraction
  pipeline/decision.py     vendor rules, PO matching, deterministic policy evaluation
  pipeline/review.py       human resolution of NEEDS_REVIEW jobs
  pipeline/orchestrator.py process_job(): the per-invoice pipeline a worker runs
  services/pdf.py          PyMuPDF text/page extraction + optional PaddleOCR
  services/medha.py        MEDHA (OpenAI-compatible) client
  services/storage.py      local file storage adapter
  worker.py                standalone durable-queue polling worker
config/
  vendor_rules.json        per-vendor tolerance/confidence/currency/PO-requirement config
data/
  sample_invoices/
    Happy-path PDFs/       4 approvable invoices + matching PO master CSV
    Edge-Case PDFs/        ambiguous PO, exhausted balance, low-quality scan, semantic duplicate
frontend/                  React/Vite/Tailwind dashboard (shadcn/ui components)
scripts/generate_sample_invoices.py   regenerates the legacy 4-PDF demo set
tests/                     pytest suite (see "Test" below)
```

## Quick start with Docker

```bash
cp .env.example .env
docker compose up --build
```

Open:

- Dashboard: `http://localhost:5173`
- API docs: `http://localhost:8000/docs`

Seed a PO master from the dashboard's **Settings** page, or via API:

```bash
curl -F "file=@data/sample_invoices/Happy-path PDFs/happy_path_purchase_orders.csv" \
  http://localhost:8000/api/purchase-orders/import
```

Then upload PDFs from `data/sample_invoices/Happy-path PDFs/` and `data/sample_invoices/Edge-Case PDFs/` (see **Demo script** below for a suggested order and what each one proves).

Without MEDHA credentials, invoices still run through the deterministic native-text fallback (`heuristic_extract`) so the pipeline is runnable end-to-end offline. Configure MEDHA for robust scanned-document extraction.

## Configure MEDHA

Set these only in `.env`; never hardcode an internal address or key.

```env
MEDHA_API_URL=https://your-medha-endpoint/v1
MEDHA_API_KEY=replace-me
MEDHA_MODEL=Medha
ENABLE_MEDHA=true
```

The client calls the OpenAI-compatible endpoint at:

```text
${MEDHA_API_URL}/chat/completions
```

It sends page images plus available native/OCR text and requests JSON-only output (vendor, invoice number, PO number, amounts, line items, per-field evidence, confidence). The model cannot approve an invoice — `app/pipeline/decision.py` is the only place a verdict is produced.

## Optional local open-source OCR

For scanned PDFs, install the PaddlePaddle wheel suitable for your CPU/GPU, then:

```bash
pip install -r requirements.txt -r requirements-ocr.txt
```

Enable it in `.env`:

```env
ENABLE_PADDLE_OCR=true
```

Pipeline behavior:

```text
Native PDF text available (≥ NATIVE_TEXT_MIN_CHARS) → PyMuPDF text  → MEDHA / deterministic fallback
Native text weak                                     → page render → PaddleOCR (optional) → MEDHA
```

## Decision policy

Evaluated deterministically in `app/pipeline/decision.py`, per vendor rule from `config/vendor_rules.json`:

| Check | Outcome if it fails |
|---|---|
| Required fields present (vendor, invoice number, total) | `NEEDS_REVIEW` |
| PO number required by vendor policy but missing | `NEEDS_REVIEW` |
| Currency allowed for this vendor | `NEEDS_REVIEW` |
| `subtotal + tax == total` within `amount_tolerance` | `NEEDS_REVIEW` |
| Extraction confidence ≥ vendor's minimum | `NEEDS_REVIEW` |
| Same vendor + invoice number seen before, same total, and that prior claim is `FINAL` | `REJECTED` |
| Same identity seen before but still `PENDING` (another run in flight) | `NEEDS_REVIEW` |
| Same identity, different total | `NEEDS_REVIEW` |
| Matched PO is closed | `REJECTED` |
| No unambiguous PO match (0 or >1 candidates) | `NEEDS_REVIEW` |
| PO vendor / currency mismatch, or total exceeds remaining PO balance | `NEEDS_REVIEW` |
| All checks pass | `APPROVED` |

All money values use Python `Decimal`. When an invoice is approved (by the pipeline or by a reviewer), `finalize_invoice_decision` atomically reserves the PO amount inside the same transaction that writes the result and closes the job — two workers (or a worker and a reviewer) can never over-allocate the same balance. If the balance no longer covers the invoice at commit time, the decision is silently downgraded to `NEEDS_REVIEW` instead of allowed to over-allocate.

## Human review flow

A `NEEDS_REVIEW` job is resolved from the Run Detail page (`ReviewPanel`), never by hand-writing an outcome:

- **Approve**: reviewer optionally selects a PO and/or corrects extracted fields; the server re-runs `evaluate_decision` with `extraction_confidence` attested at `1.0` and the reviewer's inputs. If it still fails a check (closed PO, insufficient balance, currency mismatch...), the API returns `422` and nothing is written.
- **Reject**: always allowed with a note; releases the identity claim so a corrected re-upload can be processed.
- A job that is no longer `NEEDS_REVIEW` (already resolved, or resolved concurrently by someone else) returns `409` — resolutions are not idempotent replays.
- The original model extraction in `invoice_results` is never overwritten; corrections are stored only on the `invoice_review_actions` audit row.
- The reviewer and retry actor are derived from the authenticated identity. Compatibility fields such as `reviewer_name` and `requested_by` in request bodies are ignored.

## Authentication and secure intake

There is no password or login flow in this service. `AUTH_MODE=development` uses the clearly marked local demo identity and is accepted only with `ENVIRONMENT=development`. Any other environment must use `AUTH_MODE=jwt`, provide `JWT_SECRET`, and send a bearer JWT with `sub`, `exp`, `role`, and optionally `name`. Supported roles are:

| Role | Access |
|---|---|
| `viewer` | Read jobs, evidence, and documents |
| `operator` | Viewer access plus invoice upload |
| `reviewer` | Viewer access plus review resolution and failed-job retry |
| `admin` | All access, including purchase-order import and demo seed |

Invoice uploads are streamed to an OS quarantine file while hashing. The server ignores the supplied media type and storage name, verifies `%PDF-` magic and PyMuPDF parseability, rejects encrypted files, and enforces `MAX_UPLOAD_BYTES`, `MAX_PDF_PAGES`, and `MAX_RENDER_PIXELS` before creating any database state. This is format validation and resource limiting, not antivirus or malware scanning.

PDFs are available only through the authenticated document API. Responses use private/no-store caching, content sniffing protection, a safe server-sanitized download name, and sandbox-oriented headers.

## API endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/auth/me` | Server-derived authenticated actor |
| `POST /api/purchase-orders/import` | Upload PO master CSV (upsert by `po_number`) |
| `POST /api/invoices/upload` | Upload invoice PDF and queue work (202) |
| `GET /api/jobs` | Dashboard history |
| `GET /api/jobs/{job_id}` | Job, event timeline, result, review actions, allocations |
| `GET /api/jobs/{job_id}/review/candidates` | Open POs for this invoice's vendor, with live remaining balance |
| `POST /api/jobs/{job_id}/review/resolve` | Approve/reject a `NEEDS_REVIEW` job |
| `POST /api/jobs/{job_id}/retry` | Re-queue a `FAILED` job (409 otherwise) |
| `GET /api/documents/{document_id}/file` | Original PDF |
| `POST /api/demo/seed-purchase-orders` | Seed `data/purchase_orders.csv` if present |

## Test

Fast, DB-independent tests (decision policy, normalizer):

```bash
python -m pytest tests/test_decision.py tests/test_normalizer.py
```

Full suite, including transactional integrity tests against a real Postgres (skipped automatically if unreachable — start it with `docker compose up postgres` or `docker compose up --build`):

```bash
python -m pytest
```

Coverage:

| File | What it proves |
|---|---|
| `test_decision.py` | Approval, PO-balance rejection, and exact-duplicate rejection logic |
| `test_normalizer.py` | Heuristic text extraction, name/invoice-number normalization, fenced-JSON parsing |
| `test_finalization.py` | No partial state on a failed write; two concurrent approvals cannot over-allocate a PO; a retried finalization creates exactly one allocation |
| `test_identity.py` | Identity-claim lifecycle: failure releases it, review keeps it pending, rejection releases it, approval finalizes it, a released identity can be reclaimed, a second worker cannot steal a live claim |
| `test_review.py` | Reviewer approval/rejection, double-resolution conflict (409), balance-exceeding approval refused, corrections never overwrite the stored model extraction |
| `test_retry.py` | Only `FAILED` jobs retry, a `NEEDS_REVIEW` job cannot, a retry cannot double-consume an allocation, policy snapshots survive later `vendor_rules.json` changes |

## Demo script

1. Seed the happy-path PO master (`data/sample_invoices/Happy-path PDFs/happy_path_purchase_orders.csv`).
2. Upload the four `Happy-path PDFs/*.pdf` invoices; each should extract cleanly and reach `APPROVED` against its matching PO.
3. Upload `Happy-path PDFs/semantic_duplicate_invoice.pdf` (same vendor + invoice number as an already-approved one) → `REJECTED` with a duplicate-identity reason.
4. Switch to `Edge-Case PDFs/`, seed `realistic_purchase_order_master_matching_edge_cases.csv`, and upload:
   - `po_balance_exhausted_invoice.pdf` → `NEEDS_REVIEW`, remaining PO balance insufficient.
   - `ambiguous_po_match_invoice.pdf` → `NEEDS_REVIEW`, multiple open POs match the vendor; resolve it from the Run Detail review panel by picking a PO.
   - `low_quality_scanned_invoice.pdf` → exercises the OCR/page-image fallback path.
5. Fail a job (e.g. stop MEDHA mid-run or use an unreadable file) and retry it from the dashboard to show the retry-generation audit trail.
6. Explain that model output is validated and every policy decision is deterministic, reproducible from the stored `policy_snapshot`/`policy_hash`, and reviewer resolutions re-run the same policy engine rather than trusting a free-form human verdict.
