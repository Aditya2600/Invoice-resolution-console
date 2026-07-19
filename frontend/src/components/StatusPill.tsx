import { cn } from "@/lib/utils";
import type { DecisionStatus, JobStatus, StageStatus } from "@/lib/types";

const JOB_TONE: Record<JobStatus, string> = {
  PENDING: "bg-panel text-foreground/70 border-divider",
  PROCESSING: "bg-electric/10 text-electric border-electric/30",
  COMPLETED: "bg-foreground/5 text-foreground border-divider",
  FAILED: "bg-destructive/10 text-destructive border-destructive/25",
};

export function StatusPill({ status }: { status: JobStatus }) {
  return (
    <span className={cn("mono-label inline-flex items-center gap-1.5 h-6 px-2 rounded-full border", JOB_TONE[status])}>
      {status === "PROCESSING" && <span className="size-1.5 rounded-full bg-electric animate-pulse-ring" />}
      {status}
    </span>
  );
}

const DECISION_TONE: Record<DecisionStatus, string> = {
  APPROVED: "bg-success/12 text-success border-success/25",
  NEEDS_REVIEW: "bg-warning/15 text-[oklch(0.42_0.12_75)] border-warning/30",
  REJECTED: "bg-destructive/10 text-destructive border-destructive/25",
};

export function DecisionPill({ decision }: { decision: DecisionStatus | null | undefined }) {
  if (!decision) return <span className="mono-label text-muted-foreground">—</span>;
  return (
    <span className={cn("mono-label inline-flex h-6 items-center px-2 rounded-full border", DECISION_TONE[decision])}>
      {decision.replace("_", " ")}
    </span>
  );
}

const EVENT_TONE: Record<StageStatus, string> = {
  PASS: "bg-success",
  FAIL: "bg-destructive",
  INFO: "bg-electric",
};

export function EventDot({ status }: { status: StageStatus }) {
  return <span className={cn("size-2 rounded-full inline-block", EVENT_TONE[status])} />;
}
