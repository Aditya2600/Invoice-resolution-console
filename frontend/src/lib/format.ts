import type { DecisionStatus, InvoiceEvent, JobStatus } from "./types";
import { STAGE_LABELS, STAGE_ORDER, type StageName } from "./types";

export function formatDateTime(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDate(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

export function formatMoney(amount?: string | null, currency?: string | null) {
  if (amount === null || amount === undefined || amount === "") return "—";
  const value = Number(amount);
  if (Number.isNaN(value)) return String(amount);
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: currency || "INR",
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatMs(ms?: number | null) {
  if (ms === null || ms === undefined) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

export function formatPercent(value?: number | null) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

/** A run is in flight until the worker writes a terminal status. */
export function isRunning(status: JobStatus) {
  return status === "PENDING" || status === "PROCESSING";
}

/** The label a reviewer reads first: the decision if there is one, otherwise the job state. */
export function outcomeOf(job: { status: JobStatus; decision_status: DecisionStatus | null }) {
  return job.decision_status ?? job.status;
}

export function humanizeStage(stage: string) {
  return STAGE_LABELS[stage as StageName] ?? stage.replace(/^stage_/, "").replaceAll("_", " ");
}

export function formatRelative(value?: string | null) {
  if (!value) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export interface StageState {
  stage: StageName;
  label: string;
  event: InvoiceEvent | null;
  state: "done" | "active" | "pending" | "skipped";
}

/**
 * The worker emits stages in a fixed order but skips optional ones (OCR fallback
 * only fires for weak native text), so the timeline is the canonical order
 * annotated with whichever events actually landed.
 */
export function buildTimeline(events: InvoiceEvent[], jobStatus: JobStatus): StageState[] {
  const byStage = new Map<StageName, InvoiceEvent>();
  for (const event of events) {
    const stage = event.stage as StageName;
    if (STAGE_LABELS[stage] && !byStage.has(stage)) byStage.set(stage, event);
  }
  const lastSeen = STAGE_ORDER.reduce((max, stage, index) => (byStage.has(stage) ? index : max), -1);

  return STAGE_ORDER.map((stage, index) => {
    const event = byStage.get(stage) ?? null;
    let state: StageState["state"];
    if (event) state = "done";
    else if (index === lastSeen + 1 && isRunning(jobStatus)) state = "active";
    else if (!isRunning(jobStatus)) state = "skipped";
    else state = "pending";
    return { stage, label: STAGE_LABELS[stage], event, state };
  });
}
