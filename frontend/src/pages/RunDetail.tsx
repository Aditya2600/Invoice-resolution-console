import { AlertTriangle, ChevronDown, ExternalLink, FileText, Info, Sparkles, XCircle } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { Link, useParams } from "react-router-dom";

import { ProcessingPanel } from "@/components/ProcessingPanel";
import { ErrorState } from "@/components/QueryState";
import { RuleChecks } from "@/components/RuleChecks";
import { StageTimeline } from "@/components/StageTimeline";
import { DecisionPill, StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import { useJob } from "@/hooks/queries";
import { api } from "@/lib/api";
import { formatDate, formatDateTime, formatMoney, formatMs, formatPercent, isRunning } from "@/lib/format";
import type {
  DecisionStatus,
  InvoiceEvent,
  InvoiceResultRow,
  JobDetail,
  JobStatus,
  RuleCheck,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useUiStore } from "@/store/ui";

export function RunDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const { data, isLoading, error, refetch } = useJob(jobId);

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
        <Skeleton className="mt-6 h-40 rounded-2xl" />
        <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <Skeleton className="h-96 rounded-2xl" />
          <Skeleton className="h-96 rounded-2xl" />
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

      <header className="mt-5 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <FileText className="size-4 text-foreground/50" aria-hidden />
            <span className="mono-label text-muted-foreground">INVOICE</span>
          </div>
          <h1 className="mt-2 text-3xl md:text-5xl font-semibold tracking-tight break-words max-w-3xl">
            {job.file_name}
          </h1>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <StatusPill status={job.status} />
            <DecisionPill decision={job.decision_status} />
            <span className="mono-label text-muted-foreground">ATTEMPTS ×{job.attempts}</span>
            {job.page_count != null && (
              <span className="mono-label text-muted-foreground">{job.page_count} PAGES</span>
            )}
            <span className="mono-label text-muted-foreground">CREATED {formatDateTime(job.created_at)}</span>
            <span className="mono-label text-muted-foreground">POLICY {job.policy_version}</span>
          </div>
        </div>
        <Button asChild variant="outline" className="rounded-full border-foreground/20">
          <a href={api.documentFileUrl(job.document_id)} target="_blank" rel="noreferrer">
            Original PDF <ExternalLink className="size-3.5 ml-1" aria-hidden />
          </a>
        </Button>
      </header>

      <div className="mt-8">
        {running ? (
          <ProcessingPanel detail={`The worker is processing ${job.file_name}. Stages update live below.`} />
        ) : (
          <DecisionBanner status={job.status} decision={job.decision_status} result={result} lastError={job.last_error} />
        )}
      </div>

      <EdgeCases detail={data} />

      <div className="mt-10 grid gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <section aria-labelledby="timeline-heading">
          <SectionTitle id="timeline-heading" overline="AUDIT" title="Stage timeline" />
          <div className="mt-6">
            <StageTimeline events={events} jobStatus={job.status} />
          </div>
        </section>
        <div className="flex flex-col gap-8">
          <ExtractedFields result={result} />
          <MatchedPoBlock result={result} />
          <PolicyChecks result={result} />
          <ModelBlock result={result} />
          <RawText result={result} />
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ id, overline, title }: { id?: string; overline: string; title: string }) {
  return (
    <div>
      <div className="mono-label text-muted-foreground">{overline}</div>
      <h2 id={id} className="mt-1 text-2xl font-semibold tracking-tight">
        {title}
      </h2>
    </div>
  );
}

function DecisionBanner({
  status,
  decision,
  result,
  lastError,
}: {
  status: JobStatus;
  decision: DecisionStatus | null;
  result: InvoiceResultRow | null;
  lastError: string | null;
}) {
  const reduce = useReducedMotion();
  const reasons = result?.reasons ?? [];

  const config =
    status === "FAILED"
      ? {
          tone: "destructive" as const,
          label: "FAILED",
          icon: <XCircle className="size-5" aria-hidden />,
          headline: "This run couldn't finish.",
          fallback: lastError ?? "The pipeline halted before a decision. The timeline shows the failing stage.",
        }
      : decision === "APPROVED"
        ? {
            tone: "success" as const,
            label: "APPROVED",
            icon: <Sparkles className="size-5" aria-hidden />,
            headline: "Approved. Nothing else needed from you.",
            fallback: "All required fields, PO checks and policy checks passed.",
          }
        : decision === "NEEDS_REVIEW"
          ? {
              tone: "warning" as const,
              label: "NEEDS YOUR INPUT",
              icon: <AlertTriangle className="size-5" aria-hidden />,
              headline: "A human call is required here.",
              fallback: "The policy engine could not decide with the required confidence.",
            }
          : decision === "REJECTED"
            ? {
                tone: "destructive" as const,
                label: "REJECTED",
                icon: <XCircle className="size-5" aria-hidden />,
                headline: "Rejected on policy.",
                fallback: "A hard policy rule rejected this invoice.",
              }
            : null;

  if (!config) return null;

  const toneClass = {
    success: "bg-success/12 border-success/25",
    warning: "bg-acid border-foreground/10",
    destructive: "bg-destructive/10 border-destructive/25",
  }[config.tone];

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={cn("rounded-2xl border p-6 md:p-8", toneClass)}
    >
      <div className="flex items-center gap-2 mono-label">
        <span
          className={cn(
            "inline-flex size-6 items-center justify-center rounded-full",
            config.tone === "success" && "bg-success text-white",
            config.tone === "warning" && "bg-foreground text-background",
            config.tone === "destructive" && "bg-destructive text-white",
          )}
        >
          {config.icon}
        </span>
        {config.label}
      </div>
      <h2 className="mt-4 text-3xl md:text-5xl font-semibold tracking-tight leading-tight">{config.headline}</h2>
      {reasons.length > 0 ? (
        <ul className="mt-4 flex flex-col gap-1.5 max-w-2xl text-foreground/80">
          {reasons.map((reason) => (
            <li key={reason} className="flex gap-2">
              <span aria-hidden>·</span>
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 text-foreground/70 max-w-2xl">{config.fallback}</p>
      )}
    </motion.div>
  );
}

function checkOf(result: InvoiceResultRow | null, key: string): RuleCheck | undefined {
  return result?.rule_checks?.[key];
}

function eventOf(events: InvoiceEvent[], stage: string) {
  return events.find((event) => event.stage === stage);
}

/**
 * Each block is derived from what the worker actually recorded — rule_checks
 * context and event payloads — never from a guess about which case applies.
 */
function EdgeCases({ detail }: { detail: JobDetail }) {
  const { events, result } = detail;
  const blocks: React.ReactNode[] = [];

  const ocrEvent = eventOf(events, "stage_ocr_fallback");
  const textEvent = eventOf(events, "stage_text_extract");
  const usedOcr = Boolean(ocrEvent) || textEvent?.data?.used_ocr_fallback === true;
  if (usedOcr) {
    const ocrConfidence = ocrEvent?.metrics?.ocr_confidence;
    const extractionConfidence = result?.extraction.extraction_confidence;
    blocks.push(
      <div key="ocr" className="rounded-2xl border border-electric/25 bg-electric/8 p-5 md:p-6">
        <div className="flex items-center gap-2 mono-label text-electric">
          <Info className="size-4" aria-hidden /> OCR FALLBACK
        </div>
        <p className="mt-3 text-foreground/80 max-w-3xl">
          {ocrEvent?.reason ?? textEvent?.reason ?? "Native PDF text was weak, so pages were rendered for OCR/VLM."}
        </p>
        <div className="mt-4 flex flex-wrap gap-6">
          {typeof ocrConfidence === "number" && (
            <Stat label="OCR CONFIDENCE" value={formatPercent(ocrConfidence)} />
          )}
          {extractionConfidence != null && (
            <Stat
              label="EXTRACTION CONFIDENCE"
              value={formatPercent(extractionConfidence)}
              tone={extractionConfidence < 0.85 ? "destructive" : undefined}
            />
          )}
        </div>
      </div>,
    );
  }

  const duplicate = checkOf(result, "semantic_duplicate");
  if (duplicate && duplicate.passed === false) {
    const firstDocumentId = duplicate.first_document_id;
    blocks.push(
      <div key="duplicate" className="rounded-2xl border border-destructive/25 bg-destructive/8 p-5 md:p-6">
        <div className="flex items-center gap-2 mono-label text-destructive">
          <XCircle className="size-4" aria-hidden /> SEMANTIC DUPLICATE
        </div>
        <p className="mt-3 text-foreground/80 max-w-3xl">
          This vendor and invoice number were already processed
          {duplicate.same_total === true
            ? " with the same total, which is a hard rejection."
            : " with a different total, so a reviewer must confirm."}
        </p>
        <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat label="VENDOR" value={result?.extraction.vendor_name ?? "—"} />
          <Stat label="INVOICE NO." value={result?.extraction.invoice_number ?? "—"} />
          <Stat
            label="THIS TOTAL"
            value={formatMoney(result?.extraction.total, result?.extraction.currency)}
          />
          {typeof firstDocumentId === "string" && (
            <div>
              <div className="mono-label text-muted-foreground">FIRST DOCUMENT</div>
              <a
                href={api.documentFileUrl(firstDocumentId)}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-flex items-center gap-1 font-mono text-sm underline underline-offset-4 break-all"
              >
                {firstDocumentId.slice(0, 12)}…
                <ExternalLink className="size-3" aria-hidden />
              </a>
            </div>
          )}
        </div>
      </div>,
    );
  }

  const poCheck = checkOf(result, "purchase_order_match");
  const candidates = Array.isArray(poCheck?.candidates) ? (poCheck.candidates as string[]) : [];
  const poEvent = eventOf(events, "stage_po_match");
  if (!result?.matched_po && candidates.length > 0) {
    blocks.push(
      <div key="ambiguous" className="rounded-2xl border border-foreground/10 bg-acid p-5 md:p-6">
        <div className="flex items-center gap-2 mono-label">
          <AlertTriangle className="size-4" aria-hidden /> AMBIGUOUS PURCHASE ORDER
        </div>
        <p className="mt-3 text-foreground/80 max-w-3xl">
          {poEvent?.reason ?? "More than one open purchase order matches this vendor; a reviewer must choose one."}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {candidates.map((candidate) => (
            <span
              key={candidate}
              className="mono-label bg-background border border-foreground/20 rounded-full px-3 py-1"
            >
              {candidate}
            </span>
          ))}
        </div>
      </div>,
    );
  }

  if (poCheck && poCheck.amount_within_remaining_balance === false) {
    const remaining = Number(poCheck.remaining_amount ?? 0);
    const invoiceTotal = Number(poCheck.invoice_total ?? 0);
    const currency = result?.matched_po?.currency ?? result?.extraction.currency;
    blocks.push(
      <div key="balance" className="rounded-2xl border border-destructive/25 bg-destructive/8 p-5 md:p-6">
        <div className="flex items-center gap-2 mono-label text-destructive">
          <XCircle className="size-4" aria-hidden /> PO BALANCE EXCEEDED
        </div>
        <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat label="INVOICE TOTAL" value={formatMoney(String(poCheck.invoice_total ?? ""), currency)} />
          <Stat label="PO TOTAL" value={formatMoney(result?.matched_po?.total_amount, currency)} />
          <Stat label="CONSUMED" value={formatMoney(result?.matched_po?.consumed_amount, currency)} />
          <Stat label="REMAINING" value={formatMoney(String(poCheck.remaining_amount ?? ""), currency)} tone="destructive" />
        </div>
        <p className="mt-4 text-foreground/80">
          Over by <span className="font-semibold tabular-nums">{formatMoney(String(invoiceTotal - remaining), currency)}</span>.
        </p>
      </div>,
    );
  }

  if (blocks.length === 0) return null;
  return <div className="mt-6 flex flex-col gap-4">{blocks}</div>;
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "destructive" }) {
  return (
    <div>
      <div className="mono-label text-muted-foreground">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold tabular-nums", tone === "destructive" && "text-destructive")}>
        {value}
      </div>
    </div>
  );
}

function ConfidenceMeter({ value }: { value: number }) {
  const tone = value >= 0.85 ? "bg-success" : value >= 0.6 ? "bg-electric" : "bg-warning";
  return (
    <span className="inline-flex items-center gap-2">
      <span aria-hidden className="h-1.5 w-16 rounded-full bg-panel overflow-hidden">
        <span className={cn("block h-full rounded-full", tone)} style={{ width: `${Math.round(value * 100)}%` }} />
      </span>
      <span className="mono-label tabular-nums">{formatPercent(value)}</span>
    </span>
  );
}

function ExtractedFields({ result }: { result: InvoiceResultRow | null }) {
  if (!result) {
    return (
      <section>
        <SectionTitle overline="EXTRACTED" title="Fields & evidence" />
        <p className="mt-4 text-foreground/60 text-sm">No extraction has been written for this run yet.</p>
      </section>
    );
  }

  const extraction = result.extraction;
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
      <SectionTitle overline="EXTRACTED" title="Fields & evidence" />

      <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-4 border-y border-divider py-4">
        {fields.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="mono-label text-muted-foreground">{label}</dt>
            <dd className="mt-1 font-medium break-words tabular-nums">{value}</dd>
          </div>
        ))}
        <div className="min-w-0">
          <dt className="mono-label text-muted-foreground">Confidence</dt>
          <dd className="mt-1">
            <ConfidenceMeter value={extraction.extraction_confidence} />
          </dd>
        </div>
      </dl>

      {extraction.line_items.length > 0 && (
        <div className="mt-6 overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Invoice line items</caption>
            <thead>
              <tr className="mono-label text-muted-foreground text-left border-b border-divider">
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
      )}

      {extraction.evidence.length > 0 && (
        <div className="mt-6">
          <div className="mono-label text-muted-foreground">EVIDENCE</div>
          <ul className="mt-3 flex flex-col gap-3">
            {extraction.evidence.map((item, index) => (
              <li
                key={`${item.field}-${index}`}
                className="border-l-2 border-electric bg-panel/70 rounded-r-md px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-3">
                  <span className="mono-label">{item.field}</span>
                  {item.page != null && <span className="mono-label text-muted-foreground">P{item.page}</span>}
                  {item.confidence != null && <ConfidenceMeter value={item.confidence} />}
                </div>
                {item.quote && <p className="mt-2 font-mono text-xs text-foreground/80 break-words">{item.quote}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function MatchedPoBlock({ result }: { result: InvoiceResultRow | null }) {
  const po = result?.matched_po;
  if (!po) return null;

  const remaining = Number(po.total_amount) - Number(po.consumed_amount);
  const invoiceTotal = result?.extraction.total;
  const covered = invoiceTotal != null && Number(invoiceTotal) <= remaining;

  return (
    <section>
      <SectionTitle overline="PO MATCH" title="Matched purchase order" />
      <div className="mt-5 rounded-2xl bg-ink text-electric-foreground p-6">
        <div className="mono-label opacity-70">PO NUMBER</div>
        <div className="mt-1 text-3xl font-semibold tracking-tight">{po.po_number}</div>
        <div className="mono-label opacity-70 mt-2">
          {po.vendor_name} · {po.status}
        </div>
        <div className="mt-6 grid grid-cols-3 gap-4">
          <div>
            <div className="mono-label opacity-70">PO TOTAL</div>
            <div className="mt-1 text-lg tabular-nums">{formatMoney(po.total_amount, po.currency)}</div>
          </div>
          <div>
            <div className="mono-label opacity-70">CONSUMED</div>
            <div className="mt-1 text-lg tabular-nums">{formatMoney(po.consumed_amount, po.currency)}</div>
          </div>
          <div>
            <div className="mono-label opacity-70">REMAINING</div>
            <div className="mt-1 text-lg tabular-nums">{formatMoney(String(remaining), po.currency)}</div>
          </div>
        </div>
        <p className="mt-6 mono-label">
          {invoiceTotal == null
            ? "NO INVOICE TOTAL EXTRACTED"
            : covered
              ? "WITHIN REMAINING BALANCE"
              : "EXCEEDS REMAINING BALANCE"}
        </p>
      </div>
    </section>
  );
}

function PolicyChecks({ result }: { result: InvoiceResultRow | null }) {
  if (!result || Object.keys(result.rule_checks ?? {}).length === 0) return null;
  return (
    <section>
      <SectionTitle overline="POLICY" title="Rule checks" />
      <div className="mt-5">
        <RuleChecks checks={result.rule_checks} />
      </div>
    </section>
  );
}

function ModelBlock({ result }: { result: InvoiceResultRow | null }) {
  if (!result?.model_name) return null;
  return (
    <section className="rounded-xl bg-panel p-5">
      <div className="mono-label text-muted-foreground">MODEL</div>
      <div className="mt-1 flex items-baseline justify-between gap-4">
        <div className="font-mono">{result.model_name}</div>
        <div className="mono-label text-muted-foreground tabular-nums">{formatMs(result.model_latency_ms)}</div>
      </div>
    </section>
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
        <button className="w-full flex items-center justify-between py-3 px-4 rounded-xl bg-panel hover:bg-panel/80 text-left">
          <div>
            <div className="mono-label text-muted-foreground">RAW EXTRACTED TEXT</div>
            <div className="mt-1 text-sm text-foreground/70">What the extractor read from the PDF</div>
          </div>
          <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden />
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
