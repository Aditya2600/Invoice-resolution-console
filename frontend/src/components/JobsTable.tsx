import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ArrowUpRight, Search } from "lucide-react";
import { Link } from "react-router-dom";

import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DecisionPill, StatusPill } from "@/components/StatusPill";
import { RowSkeleton } from "@/components/QueryState";
import { formatMoney, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { JobListItem } from "@/lib/types";
import { useUiStore } from "@/store/ui";

/** Written out in full because Tailwind only picks up class names that appear literally in source. */
const GRID = "md:grid-cols-[minmax(0,1fr)_180px_140px_160px_140px_60px]";

export function JobsTable({ jobs, loading }: { jobs: JobListItem[]; loading?: boolean }) {
  const { search, statusFilter, decisionFilter, setSearch, setStatusFilter, setDecisionFilter } = useUiStore();
  const reduce = useReducedMotion();

  const filtered = jobs.filter((job) => {
    if (statusFilter !== "ALL" && job.status !== statusFilter) return false;
    if (decisionFilter === "NONE" && job.decision_status) return false;
    if (decisionFilter !== "ALL" && decisionFilter !== "NONE" && job.decision_status !== decisionFilter) return false;
    if (search && !job.file_name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <section className="border-t border-divider">
      <div className="flex flex-col md:flex-row md:items-center gap-3 py-5">
        <h2 className="text-xl font-semibold tracking-tight mr-auto">All runs</h2>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" aria-hidden />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search filename"
            aria-label="Search runs by filename"
            className="pl-9 w-full md:w-72 bg-background border-divider rounded-full h-10"
          />
        </div>
        <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as typeof statusFilter)}>
          <SelectTrigger aria-label="Filter by job status" className="w-full md:w-[160px] rounded-full bg-background border-divider h-10">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All status</SelectItem>
            <SelectItem value="PENDING">Pending</SelectItem>
            <SelectItem value="PROCESSING">Processing</SelectItem>
            <SelectItem value="COMPLETED">Completed</SelectItem>
            <SelectItem value="FAILED">Failed</SelectItem>
          </SelectContent>
        </Select>
        <Select value={decisionFilter} onValueChange={(value) => setDecisionFilter(value as typeof decisionFilter)}>
          <SelectTrigger aria-label="Filter by decision" className="w-full md:w-[180px] rounded-full bg-background border-divider h-10">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All decisions</SelectItem>
            <SelectItem value="APPROVED">Approved</SelectItem>
            <SelectItem value="NEEDS_REVIEW">Needs review</SelectItem>
            <SelectItem value="REJECTED">Rejected</SelectItem>
            <SelectItem value="NONE">No decision yet</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="border-t border-divider">
        <div className={cn("hidden md:grid gap-4 mono-label text-muted-foreground py-3 border-b border-divider", GRID)}>
          <div>Filename</div>
          <div>Vendor / Total</div>
          <div>Status</div>
          <div>Decision</div>
          <div>Updated</div>
          <div />
        </div>

        {loading && <RowSkeleton rows={5} />}

        {!loading && filtered.length === 0 && (
          <div className="py-16 text-center">
            <div className="mono-label text-muted-foreground">Nothing here yet</div>
            <p className="mt-3 text-foreground/70">
              Upload invoices from the{" "}
              <Link to="/process" className="underline underline-offset-4">
                Process
              </Link>{" "}
              screen to see runs.
            </p>
          </div>
        )}

        <AnimatePresence initial={false}>
          {filtered.map((job) => (
            <motion.div
              key={job.job_id}
              layout={!reduce}
              initial={reduce ? false : { opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? undefined : { opacity: 0 }}
              transition={{ duration: 0.18 }}
              className={cn(
                "grid grid-cols-1 gap-2 md:gap-4 py-4 border-b border-divider items-center hover:bg-panel/50",
                GRID,
              )}
            >
              <Link to={`/runs/${job.job_id}`} className="min-w-0 truncate font-medium hover:text-electric">
                {job.file_name}
                <span className="ml-2 mono-label text-muted-foreground md:hidden">{formatRelative(job.updated_at)}</span>
              </Link>
              {/* Null until the run produces an extraction, so in-flight rows stay quiet. */}
              <div className="min-w-0">
                {job.vendor_name || job.total ? (
                  <>
                    <div className="text-sm truncate">{job.vendor_name ?? "—"}</div>
                    <div className="font-mono text-xs text-muted-foreground tabular-nums">
                      {formatMoney(job.total, job.currency)}
                    </div>
                  </>
                ) : (
                  <span className="text-muted-foreground text-sm">—</span>
                )}
              </div>
              <div>
                <StatusPill status={job.status} />
              </div>
              <div>
                <DecisionPill decision={job.decision_status} />
              </div>
              <div className="mono-label text-muted-foreground hidden md:block">{formatRelative(job.updated_at)}</div>
              <Link
                to={`/runs/${job.job_id}`}
                aria-label={`Open run for ${job.file_name}`}
                className="hidden md:inline-flex justify-end text-foreground/60 hover:text-foreground"
              >
                <ArrowUpRight className="size-4" />
              </Link>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </section>
  );
}
