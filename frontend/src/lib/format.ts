import type { DecisionStatus, InvoiceEvent, JobStatus } from "./types";
import {
  FRIENDLY_STAGE_LABELS,
  FRIENDLY_STAGE_OF,
  FRIENDLY_STAGE_ORDER,
  STAGE_LABELS,
  type FriendlyStage,
  type StageName,
} from "./types";

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
  stage: FriendlyStage;
  label: string;
  /** Every internal event that landed in this stage, for the Technical details pane. */
  events: InvoiceEvent[];
  /** The last one, which is what the rail reads its duration and status from. */
  event: InvoiceEvent | null;
  state: "done" | "active" | "pending" | "skipped";
}

/**
 * The rail an operator watches: eight friendly stages, not the eleven internal ones. Several
 * internal stages fold into one row (native text and the OCR fallback are both "Text extracted"),
 * and optional stages that never ran leave their row empty rather than inventing progress.
 */
export function buildTimeline(events: InvoiceEvent[], jobStatus: JobStatus): StageState[] {
  const byStage = new Map<FriendlyStage, InvoiceEvent[]>();
  for (const event of events) {
    const friendly = FRIENDLY_STAGE_OF[event.stage as StageName];
    if (!friendly) continue;
    byStage.set(friendly, [...(byStage.get(friendly) ?? []), event]);
  }
  const lastSeen = FRIENDLY_STAGE_ORDER.reduce(
    (max, stage, index) => (byStage.has(stage) ? index : max),
    -1,
  );

  return FRIENDLY_STAGE_ORDER.map((stage, index) => {
    const stageEvents = byStage.get(stage) ?? [];
    let state: StageState["state"];
    if (stageEvents.length > 0) state = "done";
    else if (index === lastSeen + 1 && isRunning(jobStatus)) state = "active";
    else if (!isRunning(jobStatus)) state = "skipped";
    else state = "pending";
    return {
      stage,
      label: FRIENDLY_STAGE_LABELS[stage],
      events: stageEvents,
      event: stageEvents[stageEvents.length - 1] ?? null,
      state,
    };
  });
}

/**
 * One sentence for the header. A failure is what an operator needs first; otherwise the newest
 * reason the worker wrote, which is already written for a human. Never invented — an event with
 * no reason produces no message rather than a filler line.
 */
export function latestMessage(events: InvoiceEvent[], jobStatus: JobStatus): string | null {
  const failure = [...events].reverse().find((event) => event.status === "FAIL" && event.reason);
  if (failure) return failure.reason;
  const latest = [...events].reverse().find((event) => event.reason);
  if (latest) return latest.reason;
  return isRunning(jobStatus) ? "Waiting for a worker to pick this up." : null;
}
