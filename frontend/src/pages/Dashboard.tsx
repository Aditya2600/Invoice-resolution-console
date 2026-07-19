import { useMemo } from "react";
import { AlertTriangle, ArrowUpRight, Upload } from "lucide-react";
import { Link } from "react-router-dom";

import { JobsTable } from "@/components/JobsTable";
import { KpiTile } from "@/components/KpiTile";
import { ErrorState } from "@/components/QueryState";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { useJobs } from "@/hooks/queries";
import { formatRelative, isRunning } from "@/lib/format";

export function Dashboard() {
  const { data: jobs = [], isLoading, error, refetch } = useJobs();

  const kpis = useMemo(() => {
    const counts = { processing: 0, review: 0, approved: 0, rejected: 0 };
    for (const job of jobs) {
      if (isRunning(job.status)) counts.processing += 1;
      if (job.decision_status === "NEEDS_REVIEW") counts.review += 1;
      if (job.decision_status === "APPROVED") counts.approved += 1;
      if (job.decision_status === "REJECTED") counts.rejected += 1;
    }
    return counts;
  }, [jobs]);

  const needsReview = useMemo(
    () => jobs.filter((job) => job.decision_status === "NEEDS_REVIEW").slice(0, 6),
    [jobs],
  );

  return (
    <div className="mx-auto max-w-7xl px-5 md:px-8">
      <section className="py-10 md:py-16 border-b border-divider">
        <div className="mono-label text-muted-foreground">DASHBOARD · INVOICE OPS</div>
        <h1 className="mt-4 text-5xl md:text-7xl font-semibold tracking-tight leading-[0.95] max-w-4xl">
          Invoice resolution, run end-to-end.
        </h1>
        <div className="mt-8 flex flex-wrap gap-3">
          <Button asChild className="rounded-full bg-foreground text-background hover:bg-foreground/90 h-11 px-5">
            <Link to="/process">
              <Upload className="size-4 mr-1" />
              Process invoices
            </Link>
          </Button>
          <Button
            asChild
            variant="outline"
            className="rounded-full border-foreground/20 bg-background hover:bg-panel h-11 px-5"
          >
            <Link to="/process">Import PO master</Link>
          </Button>
        </div>
      </section>

      {error && (
        <div className="pt-8">
          <ErrorState error={error} onRetry={() => refetch()} />
        </div>
      )}

      <section className="py-10">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <KpiTile label="In flight" value={kpis.processing} tone="electric" />
          <KpiTile label="Needs review" value={kpis.review} tone="warning" />
          <KpiTile label="Approved" value={kpis.approved} />
          <KpiTile label="Rejected" value={kpis.rejected} />
        </div>
      </section>

      <section className="pb-4">
        <div className="rounded-2xl border border-divider bg-panel/50 p-6 md:p-8 relative overflow-hidden">
          <div className="absolute right-6 top-6 opacity-40 pointer-events-none" aria-hidden>
            <div className="size-40 rounded-full texture-dots" />
          </div>
          <div className="flex items-center gap-3">
            <span className="inline-flex size-8 items-center justify-center rounded-full bg-acid text-foreground">
              <AlertTriangle className="size-4" aria-hidden />
            </span>
            <div className="mono-label text-muted-foreground">NEEDS YOUR INPUT</div>
          </div>
          <h2 className="mt-4 text-3xl md:text-5xl font-semibold tracking-tight max-w-3xl">
            {needsReview.length > 0
              ? `${needsReview.length} invoice${needsReview.length === 1 ? "" : "s"} paused for a human call.`
              : "Nothing paused."}
          </h2>
          {needsReview.length > 0 && (
            <div className="mt-8 grid md:grid-cols-2 gap-3">
              {needsReview.map((job) => (
                <Link
                  key={job.job_id}
                  to={`/runs/${job.job_id}`}
                  className="group flex items-start justify-between gap-4 rounded-xl bg-background border border-divider p-4 hover:border-foreground/40 transition-colors"
                >
                  <div className="min-w-0">
                    <div className="mono-label text-muted-foreground truncate">{job.job_id.slice(0, 12)}…</div>
                    <div className="mt-1 font-medium truncate">{job.file_name}</div>
                    <div className="mt-3 flex items-center gap-2">
                      <StatusPill status={job.status} />
                      <span className="mono-label text-muted-foreground">{formatRelative(job.updated_at)}</span>
                    </div>
                  </div>
                  <ArrowUpRight className="size-5 text-foreground/50 group-hover:text-foreground shrink-0" aria-hidden />
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>

      <JobsTable jobs={jobs} loading={isLoading} />
    </div>
  );
}
