import { useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, XCircle } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useResolveReview, useReviewCandidates } from "@/hooks/queries";
import { formatMoney } from "@/lib/format";
import type { JobDetail, PurchaseOrder, ReviewAction } from "@/lib/types";
import { cn } from "@/lib/utils";

function remaining(po: PurchaseOrder) {
  return String(Number(po.total_amount) - Number(po.consumed_amount));
}

/** The reasons the pipeline recorded, plus every rule check that actually failed. */
function failures(detail: JobDetail) {
  const result = detail.result;
  if (!result) return { reasons: [], checks: [] };
  const fromChecks = Object.entries(result.rule_checks)
    .filter(([, check]) => check.passed === false)
    .map(([key]) => key.replaceAll("_", " "));
  return { reasons: result.reasons ?? [], checks: fromChecks };
}

/**
 * The only place a NEEDS_REVIEW invoice can be resolved. Approval re-runs the backend's
 * deterministic policy checks — this panel never decides an outcome on its own.
 */
export function ReviewPanel({ detail }: { detail: JobDetail }) {
  const { job, result } = detail;
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const [selectedPo, setSelectedPo] = useState<string | null>(result?.matched_po?.po_number ?? null);
  const [pending, setPending] = useState<ReviewAction | null>(null);

  const candidates = useReviewCandidates(job.job_id, true);
  const resolve = useResolveReview(job.job_id);
  const problems = failures(detail);
  const extraction = result?.extraction;
  const currency = result?.matched_po?.currency ?? extraction?.currency;

  const canSubmit = reviewer.trim().length > 0 && note.trim().length > 0 && !resolve.isPending;

  function submit(action: ReviewAction) {
    setPending(action);
    resolve.mutate(
      {
        action,
        reviewer_name: reviewer.trim(),
        note: note.trim(),
        selected_po_number: action === "APPROVE" ? selectedPo : null,
      },
      {
        onSuccess: (data) => toast.success(data.message),
        onError: (error) => toast.error(error instanceof Error ? error.message : "Resolution failed."),
        onSettled: () => setPending(null),
      },
    );
  }

  return (
    <section aria-labelledby="review-heading" className="rounded-2xl border border-foreground/10 bg-acid p-6 md:p-8">
      <div className="flex items-center gap-2 mono-label">
        <span className="inline-flex size-6 items-center justify-center rounded-full bg-foreground text-background">
          <AlertTriangle className="size-4" aria-hidden />
        </span>
        NEEDS YOUR INPUT
      </div>
      <h2 id="review-heading" className="mt-4 text-2xl md:text-3xl font-semibold tracking-tight">
        Resolve this invoice
      </h2>

      <div className="mt-5 grid gap-5 md:grid-cols-2">
          <div>
            <div className="mono-label text-muted-foreground">WHY IT STOPPED</div>
            <ul className="mt-2 space-y-1.5 text-sm text-foreground/80">
              {problems.reasons.map((reason) => (
                <li key={reason} className="flex gap-2">
                  <XCircle className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-hidden />
                  {reason}
                </li>
              ))}
              {problems.reasons.length === 0 && <li>The run needs a human confirmation before payment.</li>}
            </ul>
            {problems.checks.length > 0 && (
              <p className="mt-3 text-xs text-muted-foreground">Failed checks: {problems.checks.join(", ")}</p>
            )}
          </div>

          <div>
            <div className="mono-label text-muted-foreground">EXTRACTED</div>
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-muted-foreground">Vendor</dt>
              <dd className="truncate">{extraction?.vendor_name ?? "—"}</dd>
              <dt className="text-muted-foreground">Invoice no.</dt>
              <dd className="truncate">{extraction?.invoice_number ?? "—"}</dd>
              <dt className="text-muted-foreground">PO on invoice</dt>
              <dd className="truncate">{extraction?.po_number ?? "—"}</dd>
              <dt className="text-muted-foreground">Total</dt>
              <dd>{formatMoney(extraction?.total, currency)}</dd>
            </dl>
          </div>
      </div>

      <div className="mt-6">
        <div className="mono-label text-muted-foreground">PURCHASE ORDER</div>
        {candidates.isLoading && <p className="mt-2 text-sm text-muted-foreground">Loading open purchase orders…</p>}
        {candidates.data?.length === 0 && (
          <p className="mt-2 text-sm text-muted-foreground">
            No open purchase order exists for this vendor. Import one before approving.
          </p>
        )}
        <div className="mt-2 flex flex-col gap-2">
          {candidates.data?.map((po) => (
            <label
              key={po.po_number}
              className={cn(
                "flex cursor-pointer items-center justify-between gap-4 rounded-xl border px-4 py-3 text-sm",
                selectedPo === po.po_number ? "border-foreground bg-background" : "border-foreground/15",
              )}
            >
              <span className="flex items-center gap-3 min-w-0">
                <input
                  type="radio"
                  name="selected-po"
                  className="accent-foreground"
                  checked={selectedPo === po.po_number}
                  onChange={() => setSelectedPo(po.po_number)}
                />
                <span className="truncate font-medium">{po.po_number}</span>
                <span className="truncate text-muted-foreground">{po.vendor_name}</span>
              </span>
              <span className="shrink-0 text-right">
                <span className="block">{formatMoney(remaining(po), po.currency)}</span>
                <span className="mono-label text-muted-foreground">REMAINING</span>
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="mt-6 grid gap-3 md:grid-cols-2">
        <label className="text-sm">
          <span className="mono-label text-muted-foreground">YOUR NAME</span>
          <Input
            className="mt-1.5 bg-background"
            value={reviewer}
            onChange={(event) => setReviewer(event.target.value)}
            placeholder="Priya Sharma"
          />
        </label>
        <label className="text-sm">
          <span className="mono-label text-muted-foreground">NOTE (REQUIRED)</span>
          <Input
            className="mt-1.5 bg-background"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Checked against the signed PO."
          />
        </label>
      </div>

      <div className="mt-5 flex flex-wrap gap-3">
        <Button
          className="rounded-full"
          disabled={!canSubmit || !selectedPo}
          onClick={() => submit("APPROVE")}
        >
          {pending === "APPROVE" ? (
            <Loader2 className="size-4 mr-1 animate-spin" aria-hidden />
          ) : (
            <CheckCircle2 className="size-4 mr-1" aria-hidden />
          )}
          Approve and allocate
        </Button>
        <Button
          variant="outline"
          className="rounded-full border-foreground/20"
          disabled={!canSubmit}
          onClick={() => submit("REJECT")}
        >
          {pending === "REJECT" ? (
            <Loader2 className="size-4 mr-1 animate-spin" aria-hidden />
          ) : (
            <XCircle className="size-4 mr-1" aria-hidden />
          )}
          Reject
        </Button>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Approval re-runs every policy check against the selected purchase order and consumes its balance in one
        transaction. A closed PO or an insufficient balance is refused.
      </p>
    </section>
  );
}

/** Read-only trail for jobs a reviewer already resolved. */
export function ReviewHistory({ detail }: { detail: JobDetail }) {
  if (detail.review_actions.length === 0) return null;
  return (
    <div className="mt-6 rounded-2xl border border-divider p-5">
      <div className="mono-label text-muted-foreground">HUMAN REVIEW</div>
      <ul className="mt-3 space-y-3 text-sm">
        {detail.review_actions.map((action) => (
          <li key={action.id}>
            <span className="font-medium">{action.reviewer_name}</span>{" "}
            {action.action === "APPROVE" ? "approved" : "rejected"} this invoice
            {action.selected_po_number ? ` on ${action.selected_po_number}` : ""}.
            <p className="text-muted-foreground">{action.note}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
