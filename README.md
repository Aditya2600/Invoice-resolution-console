# Invoice Resolution Console

A runnable take-home case-study project for **PS-1: Invoice processing — from PDF to decision**.

The console accepts a real invoice PDF, matches it against a real purchase-order CSV, and creates an auditable decision:

- `APPROVED`
- `NEEDS_REVIEW`
- `REJECTED`

It uses MEDHA (Gemma 4 26B served through vLLM) for structured extraction when configured, while keeping PO matching and finance decisions deterministic.

## Why this architecture

- PostgreSQL is both the durable application database and the job queue.
- Workers safely claim jobs with `FOR UPDATE SKIP LOCKED` and a crash-recovery lease.
- Every stage writes an append-only `invoice_events` record for the dashboard and live demo.
- Exact-file and business-level duplicates are deliberately separate checks.
- The model extracts facts; Python rules make financial decisions.

## System flow

1. Upload a PO CSV to create/update the purchase-order master.
2. Upload an invoice PDF.
3. The API stores the file, hashes it, and creates a durable `PENDING` job.
4. A worker claims the job and reads native PDF text with PyMuPDF.
5. For scanned/weak PDFs it renders pages and can use PaddleOCR; MEDHA also receives page images.
6. MEDHA returns strict JSON invoice fields plus page-level evidence.
7. The rules engine validates fields, checks duplicates, matches a PO, reconciles totals, and creates the decision.
8. The dashboard shows the live run timeline, evidence, policy checks, and final outcome.

## Repository layout

```text
app/
  api/                 FastAPI endpoints
  core/                settings and Pydantic schemas
  db/                  Postgres schema and repositories
  pipeline/            normalization, PO matching, policy decisions
  services/            PDF, MEDHA, and storage adapters
  worker.py            standalone durable queue worker
config/
  vendor_rules.json    per-vendor tolerance and approval configuration
data/
  purchase_orders.csv  demo PO master
frontend/              React/Vite dashboard
scripts/               sample PDF generator
tests/                 deterministic unit tests
```

## Quick start with Docker

```bash
cp .env.example .env
docker compose up --build
```

Open:

- Dashboard: `http://localhost:5173`
- API docs: `http://localhost:8000/docs`

The dashboard has a **Seed sample PO master** button. You can also upload `data/purchase_orders.csv` manually.

Generate four realistic demo PDFs:

```bash
python scripts/generate_sample_invoices.py
```

Then upload PDFs from `data/sample_invoices/` in this order:

1. `01-approved-acme.pdf`
2. `02-duplicate-acme.pdf`
3. `03-exceeds-po.pdf`
4. `04-ambiguous-no-po.pdf`

Without MEDHA credentials, machine-readable sample PDFs still run through the deterministic native-text fallback. Configure MEDHA for robust scanned-document extraction.

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

It sends page images plus available native/OCR text and requests JSON-only output. The model cannot approve an invoice.

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
Native PDF text available → PyMuPDF text → MEDHA / deterministic fallback
Native text weak          → page render → PaddleOCR (optional) → MEDHA
```

## Decision policy

| Condition | Outcome |
|---|---|
| Exact vendor/PO match, valid arithmetic, sufficient balance, no duplicate | `APPROVED` |
| Low confidence, ambiguous PO, missing data, or balance mismatch | `NEEDS_REVIEW` |
| Exact same vendor + invoice number + amount | `REJECTED` |
| Closed PO | `REJECTED` |

All money values use Python `Decimal`. When an invoice is approved, the worker atomically reserves the PO amount so two workers cannot over-allocate the same balance.

## API endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/purchase-orders/import` | Upload PO master CSV |
| `POST /api/invoices/upload` | Upload invoice PDF and queue work |
| `GET /api/jobs` | Dashboard history |
| `GET /api/jobs/{job_id}` | Job, event timeline, and final result |
| `GET /api/documents/{document_id}/file` | Original PDF |
| `POST /api/demo/seed-purchase-orders` | Seed the provided PO master |

## Test

```bash
python -m pytest
```

The unit tests cover field extraction, normalization, approval, duplicate rejection, and remaining-balance review behavior.

## Demo script

1. Seed the PO master.
2. Upload `01-approved-acme.pdf`; show extraction evidence, `PO-1001`, and `APPROVED`.
3. Upload `02-duplicate-acme.pdf`; show the duplicate event and `REJECTED` result.
4. Upload `03-exceeds-po.pdf`; show `NEEDS_REVIEW` due to remaining PO balance.
5. Explain that model output is validated and policy decisions are deterministic.

