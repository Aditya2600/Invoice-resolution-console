import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Check, ChevronDown, Circle, X } from "lucide-react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { EventDot } from "@/components/StatusPill";
import { buildTimeline, formatMs } from "@/lib/format";
import type { InvoiceEvent, JobStatus, StageStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * PASS reasons narrate the happy path ("Native text extracted.") and say nothing
 * a watcher cannot already see from the tick. INFO and FAIL are where the run
 * deviated, so those are the only ones worth a line.
 */
function liveNote(reason: string | null, status: StageStatus | undefined) {
  if (!reason || status === "PASS") return null;
  if (/not configured/i.test(reason)) return "Using native-text fallback";
  if (/PaddleOCR is disabled/i.test(reason)) return "Reading page images directly";
  return reason;
}

/** One raw event, exactly as the worker wrote it: internal stage name, reason, metrics, payload. */
function TechnicalRow({ event, friendly }: { event: InvoiceEvent; friendly: string }) {
  const metrics = Object.entries(event.metrics ?? {}).filter(
    ([, value]) => value !== null && value !== undefined && value !== "",
  );
  const payload = Object.keys(event.data ?? {}).length > 0 ? event.data : null;

  return (
    <li className="text-xs">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="mono-label">{event.stage}</span>
        <span className="mono-label text-muted-foreground">{friendly}</span>
        {event.ms != null && <span className="mono-label text-muted-foreground">{formatMs(event.ms)}</span>}
        <span className="inline-flex items-center gap-1.5">
          <EventDot status={event.status} />
          <span className="mono-label">{event.status}</span>
        </span>
      </div>
      {event.reason && <p className="mt-1 text-foreground/70">{event.reason}</p>}
      {metrics.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-2">
          {metrics.map(([key, value]) => (
            <span key={key} className="mono-label bg-background px-2 py-0.5 rounded text-foreground/80">
              {key.replaceAll("_", " ")}: {String(value)}
            </span>
          ))}
        </div>
      )}
      {payload && (
        <pre className="mt-1.5 max-h-40 overflow-auto rounded-lg bg-background p-2 font-mono text-[11px] whitespace-pre-wrap">
          {JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </li>
  );
}

/**
 * Two modes, one component. While a run is in flight the tape carries live
 * durations and deviation notes, because watching the pipeline execute is the
 * point. Once it is terminal that detail drops back under "Technical details"
 * and the reviewer sees only stage names.
 */
export function StageTimeline({
  events,
  jobStatus,
  live = false,
}: {
  events: InvoiceEvent[];
  jobStatus: JobStatus;
  live?: boolean;
}) {
  const all = buildTimeline(events, jobStatus);
  const reduce = useReducedMotion();
  const [technicalOpen, setTechnicalOpen] = useState(false);

  // Optional stages that never ran (OCR fallback on a native-text PDF) and the
  // terminal bookkeeping row carry nothing a reviewer can act on. While the run
  // is live, upcoming stages stay visible so the remaining work is legible.
  const visible = all.filter(
    (stage) => stage.stage !== "completed" && !(stage.state === "skipped" && !stage.event),
  );

  return (
    <div>
      {/* No aria-live here: LiveRunStrip announces the current stage in one short
          sentence, where this list would re-announce every row on each poll. */}
      <ol className="ml-3">
        {visible.map((stage, index) => {
          const event = stage.event;
          const failed = event?.status === "FAIL";
          const note = live ? liveNote(event?.reason ?? null, event?.status) : null;
          const reached = stage.state === "done" || stage.state === "active";

          return (
            <motion.li
              key={stage.stage}
              layout={!reduce}
              initial={reduce ? false : { opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.2, delay: reduce ? 0 : Math.min(index, 6) * 0.03 }}
              className={cn(
                // The rail is the progress bar: each row owns its segment, so the fill
                // is exact by construction rather than an animated percentage.
                "relative border-l-2 pl-6 transition-colors duration-500",
                live ? "pb-3.5" : "pb-4",
                reached ? "border-electric" : "border-divider",
                index === visible.length - 1 && "border-transparent",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "absolute -left-[9px] top-0.5 size-4 rounded-full flex items-center justify-center border transition-colors",
                  stage.state === "done" && !failed && "bg-foreground text-background border-foreground",
                  stage.state === "done" && failed && "bg-destructive text-destructive-foreground border-destructive",
                  stage.state === "active" && "bg-electric text-electric-foreground border-electric",
                  stage.state === "pending" && "bg-background border-divider text-muted-foreground",
                )}
              >
                {stage.state === "active" && !reduce && (
                  <span className="absolute inset-0 rounded-full bg-electric animate-pulse-ring" aria-hidden />
                )}
                {stage.state === "done" && !failed && <Check className="relative size-2.5" />}
                {stage.state === "done" && failed && <X className="relative size-2.5" />}
                {stage.state !== "done" && <Circle className="relative size-1.5" />}
              </span>

              <div className="flex items-baseline justify-between gap-4">
                <span
                  className={cn(
                    "text-sm font-medium",
                    stage.state === "pending" && "text-muted-foreground",
                    stage.state === "active" && "text-electric",
                  )}
                >
                  {stage.label}
                </span>
                {live && event?.ms != null && (
                  <span className="font-mono text-xs text-muted-foreground tabular-nums shrink-0">
                    {formatMs(event.ms)}
                  </span>
                )}
                {!live && failed && <span className="text-xs text-destructive mr-auto">Failed</span>}
                {!live && stage.state === "active" && <span className="text-xs text-electric mr-auto">In progress</span>}
              </div>

              {note && (
                <p className={cn("mt-0.5 text-xs line-clamp-2", failed ? "text-destructive" : "text-muted-foreground")}>
                  {note}
                </p>
              )}
            </motion.li>
          );
        })}
      </ol>

      <Collapsible open={technicalOpen} onOpenChange={setTechnicalOpen}>
        <CollapsibleTrigger asChild>
          <button className="mt-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
            Technical details
            <ChevronDown className={cn("size-3.5 transition-transform", technicalOpen && "rotate-180")} aria-hidden />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          {/* The only place internal stage names, raw reasons and event payloads appear. */}
          <ul className="mt-3 flex flex-col gap-3 rounded-xl bg-panel p-4">
            {all.flatMap((stage) =>
              stage.events.length > 0
                ? stage.events.map((event) => (
                    <TechnicalRow key={event.id} event={event} friendly={stage.stage} />
                  ))
                : [
                    <li key={stage.stage} className="text-xs">
                      <div className="flex flex-wrap items-baseline gap-x-3">
                        <span className="mono-label">{stage.stage}</span>
                        <span className="mono-label text-muted-foreground">
                          {stage.state === "active" ? "RUNNING" : stage.state === "pending" ? "QUEUED" : "NOT RUN"}
                        </span>
                      </div>
                    </li>,
                  ],
            )}
          </ul>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
