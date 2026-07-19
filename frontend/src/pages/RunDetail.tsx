import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, ExternalLink, Loader2, RotateCw, XCircle } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import { LiveRunStrip } from "@/components/LiveRunStrip";
import { ReviewHistory, ReviewPanel } from "@/components/ReviewPanel";
import { ErrorState } from "@/components/QueryState";
import { RuleChecks } from "@/components/RuleChecks";
import { StageTimeline } from "@/components/StageTimeline";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useJob, useRetryJob } from "@/hooks/queries";
import { api } from "@/lib/api";
import { formatDate, formatDateTime, formatMoney, formatMs, formatPercent, isRunning } from "@/lib/format";
import type { InvoiceResultRow, JobDetail, JobRow } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useUiStore } from "@/store/ui";

export function RunDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const { data, isLoading, error, refetch, dataUpdatedAt } = useJob(jobId);

  if (error) {
    return (
      <div className="mx-auto max-w-3xl px-5 md:px-8 py-16">
        <ErrorState error={error} onRetry={() => refetch()} />
        <Link to="/" className="mt-6 inline-block mono-label underline underline-offset-4">
          BACK TO DASHBOARD
        </Link>
      </div>
    );
  }

  if (isLoading || !data) {
    return (
      <div className="mx-auto max-w-6xl px-5 md:px-8 py-12">
        <div className="mono-label text-muted-foreground">LOADING RUN</div>
        <Skeleton className="mt-6 h-32 rounded-2xl" />
        <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
          <Skeleton className="h-80 rounded-2xl" />
          <Skeleton className="h-80 rounded-2xl" />
        </div>
      </div>
    );
  }

  const { job, events, result } = data;
  const running = isRunning(job.status);

  return (
    <div className="mx-auto max-w-6xl px-5 md:px-8 py-10">
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 mono-label text-muted-foreground">
        <Link to="/" className="hover:text-foreground">
          DASHBOARD
        </Link>
        <span aria-hidden>/</span>
        <span className="text-foreground truncate max-w-[240px]">{job.job_id}</span>
      </nav>

      <header className="mt-4 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl md:text-3xl font-semibold tracking-tight">{outcomeHeadline(job)}</h1>
          <p className="mt-1 text-sm text-muted-foreground truncate max-w-xl" title={job.file_name}>
            {job.file_name} · {formatDate(job.created_at)}
          </p>
        </div>
        <Button asChild variant="outline" className="rounded-full border-foreground/20">
          <a href={api.documentFileUrl(job.document_id)} target="_blank" rel="noreferrer">
            Original PDF <ExternalLink className="size-3.5 ml-1" aria-hidden />
          </a>
        </Button>
      </header>

      <div className="mt-6">
        {running ? (
          <LiveRunStrip
            events={events}
            jobStatus={job.status}
            createdAt={job.created_at}
            updatedAt={dataUpdatedAt}
          />
        ) : job.decision_status === "NEEDS_REVIEW" && job.status === "COMPLETED" ? (
          <ReviewPanel detail={data} />
        ) : (
          <OutcomeBanner detail={data} />
        )}
      </div>
      {job.status === "FAILED" && <RetryPanel job={job} />}
      <ReviewHistory detail={data} />
      <PolicyApplied result={result} />

      {/* Nothing is extracted yet during a run, so the second column would only hold
          empty states. Collapsing to one keeps the timeline above the fold. */}
      <div
        className={cn(
          "grid gap-8 items-start",
          running ? "mt-5" : "mt-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]",
        )}
      >
        <section aria-labelledby="timeline-heading">
          <h2
            id="timeline-heading"
            className={cn("text-sm font-semibold tracking-tight mb-4", running && "sr-only")}
          >
            Processing
          </h2>
          <StageTimeline events={events} jobStatus={job.status} live={running} />
          <RunDetails job={job} result={result} />
        </section>
        {!running && (
          <div className="flex flex-col gap-8">
            <InvoiceSummary result={result} />
            <RelevantEvidence result={result} />
            <RawText result={result} />
          </div>
        )}
      </div>
    </div>
  );
}

/** The single business status the page leads with; processing state is implied. */
function outcomeHeadline(job: JobRow) {
  if (job.status === "FAILED") return "Processing failed";
  switch (job.decision_status) {
    case "APPROVED":
      return "Approved";
    case "REJECTED":
      return "Rejected";
    case "NEEDS_REVIEW":
      return "Needs review";
    default:
      return "Queued";
  }
}

interface Explanation {
  text: string;
  action?: string;
}

/**
 * One explanation, picked from the rule check that actually drove the decision,
 * so a balance breach never renders as a generic "no PO match" message. This is
 * a read of the existing response — no rule is re-evaluated here.
 */
function explain(detail: JobDetail): Explanation | null {
  const { job, result } = detail;
  if (job.status === "FAILED") {
    // The technical error is kept out of the lead paragraph; it lives in the retry panel.
    return {
      text: "This invoice could not be processed, so no decision was reached.",
      action: "Retry processing below. Nothing was approved and no purchase-order balance was used.",
    };
  }
  const checks = result?.rule_checks ?? {};
  const extraction = result?.extraction;
  const vendor = extraction?.vendor_name;
  const currency = result?.matched_po?.currency ?? extraction?.currency;

  const duplicate = checks.semantic_duplicate;
  if (duplicate && duplicate.passed === false) {
    return {
      text:
        duplicate.same_total === true
          ? `This invoice was already processed for ${vendor ?? "this vendor"} with the same total.`
          : `A near-identical invoice from ${vendor ?? "this vendor"} was already processed with a different total.`,
      action: "Confirm this is not a re-submission before paying.",
    };
  }

  const po = checks.purchase_order_match;
  if (po && po.passed === false) {
    const candidates = Array.isArray(po.candidates) ? (po.candidates as string[]) : [];
    if (candidates.length > 0) {
      return {
        text: `More than one open purchase order matches ${vendor ?? "this vendor"}: ${candidates.join(", ")}.`,
        action: "Pick the correct purchase order for this invoice.",
      };
    }
    if (po.amount_within_remaining_balance === false) {
      const over = Number(po.invoice_total ?? 0) - Number(po.remaining_amount ?? 0);
      return {
        text: `Invoice total ${formatMoney(String(po.invoice_total ?? ""), currency)} exceeds the remaining balance of ${formatMoney(String(po.remaining_amount ?? ""), currency)} on ${po.po_number ?? "the purchase order"}, by ${formatMoney(String(over), currency)}.`,
        action: "Verify the PO balance or request an updated purchase order.",
      };
    }
    if (po.po_open === false) {
      return {
        text: `Purchase order ${po.po_number ?? ""} is no longer open.`.replace("  ", " "),
        action: "Reopen the purchase order or raise a new one.",
      };
    }
    if (po.currency_matches === false) {
      return {
        text: `The invoice currency does not match purchase order ${po.po_number ?? ""}.`.replace("  ", " "),
        action: "Confirm the correct currency with the vendor.",
      };
    }
    return {
      text: `No open purchase order matches ${vendor ?? "this vendor"}${po.po_number ? ` or PO number ${po.po_number}` : ""}.`,
      action: "Import the missing purchase order or verify the PO number.",
    };
  }

  const required = checks.required_invoice_fields;
  if (required && required.passed === false) {
    const missing = Array.isArray(required.missing) ? (required.missing as string[]) : [];
    return {
      text: missing.length > 0 ? `Missing from the invoice: ${missing.join(", ")}.` : "Required invoice fields are missing.",
      action: "Check the original PDF for these values.",
    };
  }

  if (checks.required_po_number?.passed === false) {
    return { text: "No PO number was found on the invoice.", action: "Ask the vendor to reference a purchase order." };
  }

  const confidence = checks.extraction_confidence;
  if (confidence && confidence.passed === false) {
    return {
      text: `Extraction confidence ${formatPercent(Number(confidence.value))} is below the ${formatPercent(Number(confidence.minimum))} required to auto-approve.`,
      action: "Compare the extracted values against the original PDF.",
    };
  }

  if (checks.currency?.passed === false) {
    return { text: `Currency ${String(checks.currency.value ?? "")} is not accepted by policy.`.replace("  ", " ") };
  }

  if (checks.invoice_arithmetic?.passed === false) {
    return { text: "Subtotal and tax do not add up to the invoice total.", action: "Ask the vendor for a corrected invoice." };
  }

  const reason = result?.reasons?.[0];
  return reason ? { text: reason } : null;
}

function OutcomeBanner({ detail }: { detail: JobDetail }) {
  const reduce = useReducedMotion();
  const { job } = detail;
  const decision = job.decision_status;

  const tone =
    job.status === "FAILED" || decision === "REJECTED"
      ? "destructive"
      : decision === "NEEDS_REVIEW"
        ? "warning"
        : decision === "APPROVED"
          ? "success"
          : null;
  if (!tone) return null;

  const { eyebrow, headline } =
    job.status === "FAILED"
      ? { eyebrow: "FAILED", headline: "Run failed." }
      : decision === "REJECTED"
        ? { eyebrow: "REJECTED", headline: "Rejected on policy." }
        : decision === "NEEDS_REVIEW"
          ? { eyebrow: "NEEDS YOUR INPUT", headline: "Needs a human call." }
          : { eyebrow: "APPROVED", headline: "Approved." };

  const explanation = explain(detail);
  if (tone === "success" && !explanation) return null;

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={cn(
        "rounded-2xl border p-6 md:p-8",
        tone === "success" && "bg-success/12 border-success/25",
        tone === "warning" && "bg-acid border-foreground/10",
        tone === "destructive" && "bg-destructive/10 border-destructive/25",
      )}
    >
      <div className="flex items-center gap-2 mono-label">
        <span
          className={cn(
            "inline-flex size-6 items-center justify-center rounded-full",
            tone === "success" && "bg-success text-white",
            tone === "warning" && "bg-foreground text-background",
            tone === "destructive" && "bg-destructive text-white",
          )}
        >
          {tone === "success" ? (
            <CheckCircle2 className="size-4" aria-hidden />
          ) : tone === "warning" ? (
            <AlertTriangle className="size-4" aria-hidden />
          ) : (
            <XCircle className="size-4" aria-hidden />
          )}
        </span>
        {eyebrow}
      </div>
      <h2 className="mt-4 text-3xl md:text-5xl font-semibold tracking-tight leading-tight">{headline}</h2>
      {explanation && <p className="mt-4 text-foreground/80 max-w-2xl">{explanation.text}</p>}
      {explanation?.action && (
        <p className="mt-3 max-w-2xl">
          <span className="font-medium">Recommended action:</span> {explanation.action}
        </p>
      )}
    </motion.div>
  );
}

/**
 * Only a failed run can be re-queued; the backend refuses anything else with 409. Retrying
 * never re-consumes purchase-order balance — an already-allocated document keeps its allocation.
 */
function RetryPanel({ job }: { job: JobRow }) {
  const [note, setNote] = useState("");
  const retry = useRetryJob(job.job_id);

  return (
    <section aria-labelledby="retry-heading" className="mt-6 rounded-2xl border border-divider p-5">
      <h2 id="retry-heading" className="text-sm font-semibold tracking-tight">
        Try this invoice again
      </h2>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Processing stopped before a decision. Re-queueing keeps the full history of this run.
        {job.manual_retry_count > 0 && ` Retried ${job.manual_retry_count} time(s) already.`}
      </p>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="text-sm min-w-[240px] flex-1">
          <span className="mono-label text-muted-foreground">NOTE (OPTIONAL)</span>
          <Input
            className="mt-1.5"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Vendor sent a clearer scan."
          />
        </label>
        <Button
          className="rounded-full"
          disabled={retry.isPending}
          onClick={() =>
            retry.mutate(
              { requested_by: "Operator", note: note.trim() || null },
              {
                onSuccess: (data) => toast.success(data.message),
                onError: (error) => toast.error(error instanceof Error ? error.message : "Retry failed."),
              },
            )
          }
        >
          {retry.isPending ? (
            <Loader2 className="size-4 mr-1 animate-spin" aria-hidden />
          ) : (
            <RotateCw className="size-4 mr-1" aria-hidden />
          )}
          Retry processing
        </Button>
      </div>
      {job.last_error && (
        <Disclosure label="Technical error" className="mt-4">
          <p className="mt-2 rounded-xl bg-panel p-3 font-mono text-xs break-words">{job.last_error}</p>
        </Disclosure>
      )}
    </section>
  );
}

/** What the decision was measured against, in words first and JSON only on request. */
function PolicyApplied({ result }: { result: InvoiceResultRow | null }) {
  const snapshot = result?.policy_snapshot;
  if (!snapshot) return null;

  const rows: [string, string][] = [
    ["Policy version", snapshot.policy_version],
    ["Amount tolerance", snapshot.amount_tolerance],
    ["Auto-approve confidence", formatPercent(snapshot.minimum_auto_approve_confidence)],
    ["PO number required", snapshot.require_po_number ? "Yes" : "No"],
    ["Allowed currencies", snapshot.allowed_currencies.join(", ")],
  ];

  return (
    <section className="mt-6 rounded-2xl border border-divider p-5">
      <h2 className="text-sm font-semibold tracking-tight">Policy applied</h2>
      <dl className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-0.5 text-sm font-medium break-words">{value}</dd>
          </div>
        ))}
      </dl>
      <Disclosure label="Policy snapshot (technical)" className="mt-4">
        <p className="mt-2 mono-label text-muted-foreground break-all">HASH {result?.policy_hash ?? "—"}</p>
        <pre className="mt-2 max-h-72 overflow-auto rounded-xl bg-panel p-3 text-xs font-mono whitespace-pre-wrap">
          {JSON.stringify(snapshot, null, 2)}
        </pre>
      </Disclosure>
    </section>
  );
}

function Disclosure({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className={className}>
      <CollapsibleTrigger asChild>
        <button className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
          {label}
          <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>{children}</CollapsibleContent>
    </Collapsible>
  );
}

/** Everything an operator needs when debugging a run, and nothing a reviewer does. */
function RunDetails({ job, result }: { job: JobRow; result: InvoiceResultRow | null }) {
  const rows: [string, string][] = [
    ["Job", job.job_id],
    ["Document", job.document_id],
    ["Attempts", `${job.attempts} of ${job.max_attempts}`],
    ["Pages", job.page_count != null ? String(job.page_count) : "—"],
    ["Policy version", job.policy_version],
    ["Created", formatDateTime(job.created_at)],
    ["Updated", formatDateTime(job.updated_at)],
  ];
  if (result?.model_name) {
    rows.push(["Model", result.model_name], ["Model latency", formatMs(result.model_latency_ms) || "—"]);
  }

  return (
    <Disclosure label="Run details" className="mt-4">
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl bg-panel p-4 text-xs">
        {rows.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="mono-label text-muted-foreground">{label}</dt>
            <dd className="mt-0.5 font-mono break-all">{value}</dd>
          </div>
        ))}
      </dl>
      {result && Object.keys(result.rule_checks ?? {}).length > 0 && (
        <div className="mt-3">
          <RuleChecks checks={result.rule_checks} />
        </div>
      )}
    </Disclosure>
  );
}

function InvoiceSummary({ result }: { result: InvoiceResultRow | null }) {
  if (!result) {
    return (
      <section>
        <h2 className="text-sm font-semibold tracking-tight">Invoice</h2>
        <p className="mt-3 text-sm text-foreground/60">No extraction yet.</p>
      </section>
    );
  }

  const extraction = result.extraction;
  const po = result.matched_po;
  const fields: [string, string][] = [
    ["Vendor", extraction.vendor_name ?? "—"],
    ["Invoice no.", extraction.invoice_number ?? "—"],
    ["Invoice date", formatDate(extraction.invoice_date)],
    ["PO number", extraction.po_number ?? "—"],
    ["Subtotal", formatMoney(extraction.subtotal, extraction.currency)],
    ["Tax", formatMoney(extraction.tax, extraction.currency)],
    ["Total", formatMoney(extraction.total, extraction.currency)],
  ];

  return (
    <section>
      <h2 className="text-sm font-semibold tracking-tight">Invoice</h2>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-y border-divider py-4">
        {fields.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-0.5 text-sm font-medium break-words tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      {po && (
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3">
          <div className="col-span-2">
            <dt className="text-xs text-muted-foreground">Matched purchase order</dt>
            <dd className="mt-0.5 text-sm font-medium">
              {po.po_number} · {po.vendor_name}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">PO total</dt>
            <dd className="mt-0.5 text-sm tabular-nums">{formatMoney(po.total_amount, po.currency)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Remaining</dt>
            <dd className="mt-0.5 text-sm tabular-nums">
              {formatMoney(String(Number(po.total_amount) - Number(po.consumed_amount)), po.currency)}
            </dd>
          </div>
        </dl>
      )}

      {extraction.line_items.length > 0 && (
        <Disclosure label="Invoice line items" className="mt-4">
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Invoice line items</caption>
              <thead>
                <tr className="text-xs text-muted-foreground text-left border-b border-divider">
                  <th scope="col" className="py-2 pr-3 font-normal">Description</th>
                  <th scope="col" className="py-2 px-3 font-normal text-right">Qty</th>
                  <th scope="col" className="py-2 px-3 font-normal text-right">Unit</th>
                  <th scope="col" className="py-2 pl-3 font-normal text-right">Amount</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-divider">
                {extraction.line_items.map((item, index) => (
                  <tr key={`${item.description ?? "line"}-${index}`}>
                    <td className="py-2 pr-3">{item.description ?? "—"}</td>
                    <td className="py-2 px-3 text-right tabular-nums">{item.quantity ?? "—"}</td>
                    <td className="py-2 px-3 text-right tabular-nums">
                      {formatMoney(item.unit_price, extraction.currency)}
                    </td>
                    <td className="py-2 pl-3 text-right tabular-nums">
                      {formatMoney(item.amount, extraction.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Disclosure>
      )}
    </section>
  );
}

/** Fields the decision turned on, quoted from the page they were read off. */
const PRIORITY_EVIDENCE = ["vendor_name", "po_number"];

function RelevantEvidence({ result }: { result: InvoiceResultRow | null }) {
  const evidence = result?.extraction.evidence ?? [];
  if (evidence.length === 0) return null;

  const primary = evidence.filter((item) => PRIORITY_EVIDENCE.includes(item.field));
  const rest = evidence.filter((item) => !PRIORITY_EVIDENCE.includes(item.field));
  const shown = primary.length > 0 ? primary : evidence.slice(0, 2);
  const hidden = primary.length > 0 ? rest : evidence.slice(2);

  return (
    <section>
      <h2 className="text-sm font-semibold tracking-tight">Evidence</h2>
      <EvidenceList items={shown} />
      {hidden.length > 0 && (
        <Disclosure label="All extracted evidence" className="mt-3">
          <EvidenceList items={hidden} />
        </Disclosure>
      )}
    </section>
  );
}

function EvidenceList({ items }: { items: InvoiceResultRow["extraction"]["evidence"] }) {
  return (
    <ul className="mt-3 flex flex-col gap-2">
      {items.map((item, index) => (
        <li key={`${item.field}-${index}`} className="border-l-2 border-electric bg-panel/70 rounded-r-md px-3 py-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{item.field.replaceAll("_", " ")}</span>
            {item.page != null && <span>page {item.page}</span>}
            {/* Confidence is only worth the pixels when it is not a certainty. */}
            {item.confidence != null && item.confidence < 1 && <span>{formatPercent(item.confidence)} confident</span>}
          </div>
          {item.quote && <p className="mt-1 font-mono text-xs text-foreground/80 break-words">{item.quote}</p>}
        </li>
      ))}
    </ul>
  );
}

function RawText({ result }: { result: InvoiceResultRow | null }) {
  const open = useUiStore((state) => state.rawTextOpen);
  const setOpen = useUiStore((state) => state.setRawTextOpen);
  const text = result?.extraction.raw_text;
  if (!text) return null;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <button className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
          Raw extracted text
          <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="mt-3 max-h-96 overflow-auto rounded-xl border border-divider bg-background p-4 text-xs font-mono whitespace-pre-wrap">
          {text}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}
