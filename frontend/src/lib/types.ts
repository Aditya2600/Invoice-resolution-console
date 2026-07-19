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
  stage_pdf_validate: "Document validated",
  stage_text_extract: "Text extracted",
  stage_ocr_fallback: "Scanned pages read",
  stage_medha_extract: "Invoice data extracted",
  stage_semantic_duplicate: "Duplicate check",
  stage_po_match: "Purchase order lookup",
  stage_policy_validate: "Validation",
  invoice_closed: "Invoice closed",
};

/** The eight stages an operator reads. Internal stage names stay in Technical details. */
export const FRIENDLY_STAGE_ORDER = [
  "received",
  "document_read",
  "text_or_ocr",
  "ai_extraction",
  "duplicate_check",
  "po_validation",
  "decision",
  "completed",
] as const;

export type FriendlyStage = (typeof FRIENDLY_STAGE_ORDER)[number];

/** Mirrors STAGE_MAP in app/core/observability.py. */
export const FRIENDLY_STAGE_OF: Record<StageName, FriendlyStage> = {
  invoice_received: "received",
  stage_pdf_validate: "document_read",
  stage_text_extract: "text_or_ocr",
  stage_ocr_fallback: "text_or_ocr",
  stage_medha_extract: "ai_extraction",
  stage_semantic_duplicate: "duplicate_check",
  stage_po_match: "po_validation",
  stage_policy_validate: "decision",
  invoice_closed: "completed",
};

export const FRIENDLY_STAGE_LABELS: Record<FriendlyStage, string> = {
  received: "Invoice received",
  document_read: "Document read",
  text_or_ocr: "Text extracted",
  ai_extraction: "Invoice data extracted",
  duplicate_check: "Duplicate check",
  po_validation: "Purchase order checked",
  decision: "Decision",
  completed: "Completed",
};

/** GET /api/ops/overview. Every figure is aggregated in Postgres, never counted in the browser. */
export interface OpsOverview {
  window_hours: number;
  queue: Partial<Record<JobStatus, number>>;
  awaiting_review: number;
  decisions: Partial<Record<DecisionStatus, number>>;
  reliability: { failed_jobs: number; manual_retries: number; retried_jobs: number; jobs: number };
  processing_ms: { p50: number | null; p95: number | null; samples: number };
  stages: { stage: FriendlyStage; p50_ms: number | null; p95_ms: number | null; samples: number }[];
  ocr: { fallbacks: number; documents: number; fallback_rate: number | null };
  medha: {
    success: number;
    timeout: number;
    error: number;
    p50_ms: number | null;
    p95_ms: number | null;
  };
  top_failures: { reason: string; occurrences: number }[];
}

export interface JobListItem {
  job_id: string;
  document_id: string;
  file_name: string;
  status: JobStatus;
  decision_status: DecisionStatus | null;
  attempts: number;
  created_at: string;
  updated_at: string;
  /** Read off the result row, so null until the run produces an extraction. */
  vendor_name: string | null;
  total: string | null;
  currency: string | null;
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
  retry_generation: number;
  manual_retry_count: number;
  last_retry_at: string | null;
  last_retry_by: string | null;
  last_retry_actor_id: string | null;
  last_retry_actor_role: string | null;
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
  policy_snapshot: PolicySnapshot | null;
  policy_hash: string | null;
  updated_at: string;
}

/** The rule values a decision was actually made under, frozen at finalization. */
export interface PolicySnapshot {
  policy_version: string;
  vendor_normalized: string | null;
  amount_tolerance: string;
  minimum_auto_approve_confidence: number;
  require_po_number: boolean;
  allowed_currencies: string[];
}

export interface PoAllocation {
  allocation_id: string;
  po_number: string;
  document_id: string;
  job_id: string;
  amount: string;
  status: "ACTIVE" | "RELEASED";
  created_at: string;
  released_at: string | null;
}

export interface ReviewActionRow {
  id: number;
  job_id: string;
  reviewer_name: string;
  actor_id: string | null;
  actor_role: string | null;
  action: ReviewAction;
  selected_po_number: string | null;
  corrections: Record<string, string | null>;
  note: string;
  decision_before: DecisionStatus | null;
  decision_after: DecisionStatus | null;
  created_at: string;
}

export interface JobDetail {
  job: JobRow;
  events: InvoiceEvent[];
  result: InvoiceResultRow | null;
  /** Model extraction with the last reviewer's corrections applied. Null until a run produces one. */
  effective_extraction: InvoiceExtraction | null;
  review_actions: ReviewActionRow[];
  allocations: PoAllocation[];
}

export type ReviewAction = "APPROVE" | "REJECT";

export interface ResolveReviewRequest {
  action: ReviewAction;
  note: string;
  selected_po_number?: string | null;
  corrections?: Record<string, string> | null;
}

export interface ResolveReviewResponse {
  message: string;
  decision_status: DecisionStatus;
  reasons: string[];
  allocation_id: string | null;
}

export interface RetryJobRequest {
  note?: string | null;
}

export interface AuthenticatedActor {
  subject: string;
  display_name: string;
  role: "viewer" | "operator" | "reviewer" | "admin";
  auth_mode: "development" | "jwt";
}

export interface RetryJobResponse {
  message: string;
  job_id: string;
  retry_generation: number;
  manual_retry_count: number;
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
