/** Mirrors app/core/schemas.py and the invoice_* tables. */

export type JobStatus = "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";
export type DecisionStatus = "APPROVED" | "NEEDS_REVIEW" | "REJECTED";
export type StageStatus = "PASS" | "FAIL" | "INFO";

/** The exact stage strings app/pipeline/orchestrator.py writes to invoice_events, in emit order. */
export type StageName =
  | "invoice_received"
  | "stage_pdf_validate"
  | "stage_text_extract"
  | "stage_ocr_fallback"
  | "stage_medha_extract"
  | "stage_semantic_duplicate"
  | "stage_po_match"
  | "stage_policy_validate"
  | "invoice_closed";

export const STAGE_ORDER: StageName[] = [
  "invoice_received",
  "stage_pdf_validate",
  "stage_text_extract",
  "stage_ocr_fallback",
  "stage_medha_extract",
  "stage_semantic_duplicate",
  "stage_po_match",
  "stage_policy_validate",
  "invoice_closed",
];

export const STAGE_LABELS: Record<StageName, string> = {
  invoice_received: "Invoice received",
  stage_pdf_validate: "PDF validate",
  stage_text_extract: "Text extract",
  stage_ocr_fallback: "OCR fallback",
  stage_medha_extract: "MEDHA field extract",
  stage_semantic_duplicate: "Semantic duplicate check",
  stage_po_match: "Purchase-order match",
  stage_policy_validate: "Policy validate",
  invoice_closed: "Invoice closed",
};

export interface JobListItem {
  job_id: string;
  document_id: string;
  file_name: string;
  status: JobStatus;
  decision_status: DecisionStatus | null;
  attempts: number;
  created_at: string;
  updated_at: string;
}

/** GET /api/jobs/{id} returns the joined job row, so it carries document columns too. */
export interface JobRow extends JobListItem {
  policy_version: string;
  max_attempts: number;
  lease_until: string | null;
  last_error: string | null;
  storage_key: string;
  sha256: string;
  page_count: number | null;
}

export interface InvoiceEvent {
  id: number;
  job_id: string;
  ts: string;
  stage: string;
  status: StageStatus;
  reason: string | null;
  ms: number | null;
  metrics: Record<string, unknown>;
  data: Record<string, unknown>;
}

export interface Evidence {
  field: string;
  page: number | null;
  quote: string | null;
  confidence: number | null;
}

export interface LineItem {
  description: string | null;
  quantity: string | null;
  unit_price: string | null;
  amount: string | null;
}

/** Money arrives as strings because the backend serializes Decimal. */
export interface InvoiceExtraction {
  vendor_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  po_number: string | null;
  currency: string | null;
  subtotal: string | null;
  tax: string | null;
  total: string | null;
  line_items: LineItem[];
  extraction_confidence: number;
  evidence: Evidence[];
  raw_text: string | null;
}

export interface PurchaseOrder {
  po_number: string;
  vendor_name: string;
  currency: string;
  total_amount: string;
  consumed_amount: string;
  status: string;
}

export interface RuleCheck {
  passed: boolean;
  [key: string]: unknown;
}

export interface InvoiceResultRow {
  document_id: string;
  job_id: string;
  decision_status: DecisionStatus;
  extraction: InvoiceExtraction;
  matched_po: PurchaseOrder | null;
  reasons: string[];
  rule_checks: Record<string, RuleCheck>;
  model_name: string | null;
  model_latency_ms: number | null;
  updated_at: string;
}

export interface JobDetail {
  job: JobRow;
  events: InvoiceEvent[];
  result: InvoiceResultRow | null;
}

export interface UploadInvoiceResponse {
  message: string;
  created: boolean;
  job: JobListItem;
}

export interface ImportPurchaseOrdersResponse {
  message: string;
  imported: number;
}

export interface VendorRule {
  amount_tolerance: string;
  minimum_auto_approve_confidence: number;
  require_po_number: boolean;
  allowed_currencies: string[];
}
