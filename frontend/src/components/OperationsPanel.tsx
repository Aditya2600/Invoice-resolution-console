import { Activity, AlertTriangle, Clock, Users } from "lucide-react";

import { useOpsOverview } from "@/hooks/queries";
import { formatMs } from "@/lib/format";
import type { OpsOverview } from "@/lib/types";
import { cn } from "@/lib/utils";

function percent(part: number, whole: number) {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

/**
 * Operational health, straight from /api/ops/overview. Every number here is aggregated in
 * Postgres — nothing is derived from the job list this page happens to hold, so the panel is
 * honest about runs it never loaded. It renders nothing until the API answers: an empty state
 * is truthful, a zero is not.
 */
export function OperationsPanel() {
  const { data, isLoading, error } = useOpsOverview();

  if (error) return null;
  if (isLoading || !data) {
    return (
      <div className="rounded-2xl border border-divider p-6">
        <div className="mono-label text-muted-foreground">OPERATIONS</div>
        <p className="mt-3 text-sm text-muted-foreground">Loading service health…</p>
      </div>
    );
  }

  const inFlight = (data.queue.PENDING ?? 0) + (data.queue.PROCESSING ?? 0);
  const decided =
    (data.decisions.APPROVED ?? 0) + (data.decisions.NEEDS_REVIEW ?? 0) + (data.decisions.REJECTED ?? 0);

  return (
    <section aria-labelledby="operations-heading" className="rounded-2xl border border-divider p-6 md:p-7">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="operations-heading" className="mono-label text-muted-foreground">
          OPERATIONS · LAST {data.window_hours}H
        </h2>
        <span className="text-xs text-muted-foreground">
          {data.reliability.jobs} run{data.reliability.jobs === 1 ? "" : "s"} in window
        </span>
      </div>

      <div className="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <Metric icon={<Activity className="size-3.5" aria-hidden />} label="Queue" value={String(inFlight)}>
          <span className="text-xs text-muted-foreground">
            {data.queue.PROCESSING ?? 0} processing · {data.queue.PENDING ?? 0} waiting
          </span>
        </Metric>

        <Metric icon={<Users className="size-3.5" aria-hidden />} label="Review queue" value={String(data.awaiting_review)}>
          <span className="text-xs text-muted-foreground">awaiting a human decision</span>
        </Metric>

        <Metric
          icon={<Clock className="size-3.5" aria-hidden />}
          label="Processing time"
          value={data.processing_ms.p50 != null ? formatMs(data.processing_ms.p50) : "—"}
        >
          <span className="text-xs text-muted-foreground">
            p50 · p95 {data.processing_ms.p95 != null ? formatMs(data.processing_ms.p95) : "—"}
          </span>
        </Metric>

        <Metric
          icon={<AlertTriangle className="size-3.5" aria-hidden />}
          label="Failed"
          value={String(data.reliability.failed_jobs)}
          tone={data.reliability.failed_jobs > 0 ? "warning" : undefined}
        >
          <span className="text-xs text-muted-foreground">
            {data.reliability.manual_retries} manual retr{data.reliability.manual_retries === 1 ? "y" : "ies"}
          </span>
        </Metric>
      </div>

      {decided > 0 && (
        <div className="mt-6">
          <div className="mono-label text-muted-foreground">DECISION MIX</div>
          <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-panel" role="presentation">
            <div className="bg-success" style={{ width: `${percent(data.decisions.APPROVED ?? 0, decided)}%` }} />
            <div className="bg-acid" style={{ width: `${percent(data.decisions.NEEDS_REVIEW ?? 0, decided)}%` }} />
            <div className="bg-destructive" style={{ width: `${percent(data.decisions.REJECTED ?? 0, decided)}%` }} />
          </div>
          <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
            <Share label="Approved" count={data.decisions.APPROVED ?? 0} total={decided} dot="bg-success" />
            <Share label="Needs review" count={data.decisions.NEEDS_REVIEW ?? 0} total={decided} dot="bg-acid" />
            <Share label="Rejected" count={data.decisions.REJECTED ?? 0} total={decided} dot="bg-destructive" />
          </dl>
        </div>
      )}

      <TopIssue overview={data} />
    </section>
  );
}

function Metric({
  icon,
  label,
  value,
  tone,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone?: "warning";
  children?: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5 mono-label text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className={cn("mt-1.5 text-3xl font-semibold tracking-tight tabular-nums", tone === "warning" && "text-destructive")}>
        {value}
      </div>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}

function Share({ label, count, total, dot }: { label: string; count: number; total: number; dot: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className={cn("size-2 rounded-full", dot)} aria-hidden />
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">
        {count} ({percent(count, total)}%)
      </dd>
    </div>
  );
}

/** The one thing worth acting on: the most frequent failure, or the provider that is degrading. */
function TopIssue({ overview }: { overview: OpsOverview }) {
  const failure = overview.top_failures[0];
  const medhaFailures = overview.medha.timeout + overview.medha.error;

  if (!failure && medhaFailures === 0) {
    return (
      <p className="mt-6 text-sm text-muted-foreground">
        No failures recorded in this window
        {overview.ocr.fallback_rate != null &&
          ` · ${Math.round(overview.ocr.fallback_rate * 100)}% of documents needed the OCR fallback`}
        .
      </p>
    );
  }

  return (
    <div className="mt-6 rounded-xl border border-divider bg-panel/60 p-4">
      <div className="mono-label text-muted-foreground">TOP ISSUE</div>
      {failure && (
        <p className="mt-1.5 text-sm">
          <span className="font-medium tabular-nums">{failure.occurrences}×</span> {failure.reason}
        </p>
      )}
      {medhaFailures > 0 && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          Extraction provider: {overview.medha.success} succeeded, {overview.medha.timeout} timed out,{" "}
          {overview.medha.error} errored
          {overview.medha.p95_ms != null && ` · p95 ${formatMs(overview.medha.p95_ms)}`}.
        </p>
      )}
    </div>
  );
}
