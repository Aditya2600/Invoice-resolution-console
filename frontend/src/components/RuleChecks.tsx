import { CheckCircle2, XCircle } from "lucide-react";

import type { RuleCheck } from "@/lib/types";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  required_invoice_fields: "Required fields present",
  required_po_number: "PO number supplied",
  currency: "Currency allowed",
  invoice_arithmetic: "Subtotal plus tax reconciles",
  extraction_confidence: "Extraction confidence",
  semantic_duplicate: "Not a duplicate invoice",
  purchase_order_match: "Purchase-order match",
  atomic_po_reservation: "PO balance reserved atomically",
};

/** Every key except `passed` is check-specific context worth showing verbatim. */
function detailsOf(check: RuleCheck) {
  return Object.entries(check)
    .filter(([key, value]) => key !== "passed" && value !== null && value !== undefined && value !== "")
    .map(([key, value]) => [key.replaceAll("_", " "), Array.isArray(value) ? value.join(", ") : String(value)]);
}

export function RuleChecks({ checks }: { checks: Record<string, RuleCheck> }) {
  const entries = Object.entries(checks);
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">No policy checks were recorded for this run.</p>;
  }

  return (
    <ul className="flex flex-col divide-y divide-divider border-y border-divider">
      {entries.map(([key, check]) => {
        const details = detailsOf(check);
        return (
          <li key={key} className="flex gap-3 py-3">
            {check.passed ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
            ) : (
              <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
            )}
            <div className="min-w-0 flex-1">
              <p className={cn("text-sm font-medium", !check.passed && "text-destructive")}>
                <span className="sr-only">{check.passed ? "Passed: " : "Failed: "}</span>
                {LABELS[key] ?? key.replaceAll("_", " ")}
              </p>
              {details.length > 0 && (
                <dl className="mt-1 grid gap-x-4 gap-y-0.5 text-xs text-muted-foreground sm:grid-cols-2">
                  {details.map(([label, value]) => (
                    <div key={label} className="flex gap-2">
                      <dt className="capitalize">{label}:</dt>
                      <dd className="tabular-nums truncate">{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
