import { motion, useReducedMotion } from "motion/react";
import { Check, Circle, X } from "lucide-react";

import { EventDot } from "@/components/StatusPill";
import { buildTimeline, formatMs } from "@/lib/format";
import type { InvoiceEvent, JobStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The audit tape. Stages come from the canonical worker order, annotated with
 * whichever invoice_events rows actually landed — optional stages (OCR fallback)
 * stay visible as "not run" so the gap is explicit rather than silent.
 */
export function StageTimeline({ events, jobStatus }: { events: InvoiceEvent[]; jobStatus: JobStatus }) {
  const stages = buildTimeline(events, jobStatus);
  const reduce = useReducedMotion();

  return (
    <ol className="relative border-l border-divider ml-3" aria-live="polite">
      {stages.map((stage, index) => {
        const event = stage.event;
        const failed = event?.status === "FAIL";
        const metrics = Object.entries(event?.metrics ?? {}).filter(
          ([, value]) => value !== null && value !== undefined && value !== "",
        );

        return (
          <motion.li
            key={stage.stage}
            initial={reduce ? false : { opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: reduce ? 0 : index * 0.03 }}
            className="pl-6 pb-6 relative"
          >
            <span
              aria-hidden
              className={cn(
                "absolute -left-[9px] top-1 size-4 rounded-full flex items-center justify-center border",
                stage.state === "done" && !failed && "bg-foreground text-background border-foreground",
                stage.state === "done" && failed && "bg-destructive text-destructive-foreground border-destructive",
                stage.state === "active" && "bg-electric text-electric-foreground border-electric animate-pulse-ring",
                (stage.state === "pending" || stage.state === "skipped") &&
                  "bg-background border-divider text-muted-foreground",
              )}
            >
              {stage.state === "done" && !failed && <Check className="size-2.5" />}
              {stage.state === "done" && failed && <X className="size-2.5" />}
              {stage.state !== "done" && <Circle className="size-1.5" />}
            </span>

            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <div className={cn("font-medium", stage.state === "skipped" && "text-muted-foreground")}>
                {stage.label}
              </div>
              <span className="mono-label text-muted-foreground">{stage.stage}</span>
              {event?.ms != null && <span className="mono-label text-muted-foreground">{formatMs(event.ms)}</span>}
              {event && (
                <span className="inline-flex items-center gap-1.5">
                  <EventDot status={event.status} />
                  <span className="mono-label">{event.status}</span>
                </span>
              )}
              {!event && stage.state === "active" && (
                <span className="mono-label text-electric">Worker is running this stage…</span>
              )}
              {!event && stage.state === "skipped" && <span className="mono-label text-muted-foreground">Not run</span>}
            </div>

            {event?.reason && <p className="mt-1.5 text-sm text-foreground/75">{event.reason}</p>}

            {metrics.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {metrics.map(([key, value]) => (
                  <span key={key} className="mono-label bg-panel px-2 py-1 rounded-md text-foreground/80">
                    {key.replaceAll("_", " ")}: {String(value)}
                  </span>
                ))}
              </div>
            )}
          </motion.li>
        );
      })}
    </ol>
  );
}
