import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";

import { buildTimeline } from "@/lib/format";
import type { InvoiceEvent, JobStatus } from "@/lib/types";

/**
 * Ticks while a run is in flight. 100ms keeps the tenths digit honest without
 * the counter reading as a spinner; the interval is torn down on terminal state
 * so a finished page is completely static.
 */
function useTicker(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

/** Server and browser clocks drift; a negative elapsed time would read as a bug. */
function elapsedSeconds(from: string, now: number) {
  return Math.max(0, (now - new Date(from).getTime()) / 1000);
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;
}

function formatSince(seconds: number) {
  if (seconds < 2) return "updated just now";
  if (seconds < 60) return `updated ${Math.floor(seconds)}s ago`;
  return `updated ${Math.floor(seconds / 60)}m ago`;
}

/**
 * The running-state header: what the worker is doing right now, how long the run
 * has taken, and proof the page is still talking to the API. Replaces the full
 * -bleed hero so the stage timeline stays above the fold on a laptop.
 */
export function LiveRunStrip({
  events,
  jobStatus,
  createdAt,
  updatedAt,
}: {
  events: InvoiceEvent[];
  jobStatus: JobStatus;
  createdAt: string;
  updatedAt: number;
}) {
  const reduce = useReducedMotion();
  const now = useTicker(true);
  const stages = buildTimeline(events, jobStatus);

  const active = stages.find((stage) => stage.state === "active");
  const lastDone = [...stages].reverse().find((stage) => stage.state === "done");
  const current = active ?? lastDone;

  // Queue time counts: before a worker claims the job there are no events, and a
  // counter frozen at 0.0s would read as a broken page rather than a waiting one.
  const elapsed = elapsedSeconds(events[0]?.ts ?? createdAt, now);
  const since = Math.max(0, (now - updatedAt) / 1000);

  return (
    <div className="rounded-2xl bg-ink text-white px-5 py-4 md:px-6 md:py-5">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="relative flex size-2 shrink-0" aria-hidden>
              {!reduce && (
                <span className="absolute inset-0 rounded-full bg-electric animate-pulse-ring" />
              )}
              <span className="relative size-2 rounded-full bg-electric" />
            </span>
            <span className="mono-label text-white/60">
              {jobStatus === "PENDING" ? "QUEUED" : "LIVE"} · {formatSince(since).toUpperCase()}
            </span>
          </div>
          <p className="mt-1.5 text-xl md:text-2xl font-semibold tracking-tight truncate">
            {jobStatus === "PENDING" ? "Waiting for a worker" : (current?.label ?? "Starting")}
          </p>
        </div>

        <div className="text-right shrink-0">
          <motion.div
            key={Math.floor(elapsed)}
            initial={reduce ? false : { opacity: 0.75 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.15 }}
            className="font-mono text-2xl md:text-3xl tabular-nums leading-none"
          >
            {formatElapsed(elapsed)}
          </motion.div>
          <div className="mono-label text-white/50 mt-1.5">ELAPSED</div>
        </div>
      </div>
      <span className="sr-only" aria-live="polite">
        {current ? `Current stage: ${current.label}.` : "Waiting for a worker."}
      </span>
    </div>
  );
}
