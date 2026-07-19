import { useState } from "react";
import { Check, Lock, Minus, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { api, API_BASE_URL, ApiError } from "@/lib/api";
import { formatPercent } from "@/lib/format";
import { Button } from "@/components/ui/button";
import type { VendorRule } from "@/lib/types";
import rules from "@/data/vendor-rules.json";

const vendorRules = rules as Record<string, VendorRule>;

export function Settings() {
  const [resetting, setResetting] = useState(false);

  async function handleReset() {
    if (!window.confirm("Delete every job, document, and PO in this environment? Cannot be undone.")) return;
    setResetting(true);
    try {
      await api.resetDemo();
      toast.success("Demo data reset.");
      window.location.reload();
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Reset failed.");
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-5 md:px-8 py-10">
      <div className="mono-label text-muted-foreground">POLICY · READ-ONLY</div>
      <h1 className="mt-3 text-5xl md:text-6xl font-semibold tracking-tight leading-[0.95] max-w-3xl">
        The rules every invoice runs through.
      </h1>
      <p className="mt-6 max-w-2xl text-foreground/70">
        Enforced server-side. Edit <code className="font-mono">config/vendor_rules.json</code> and restart the worker.
      </p>

      <div className="mt-6 inline-flex items-center gap-2 mono-label rounded-full border border-divider bg-panel px-3 h-7">
        <Lock className="size-3.5" aria-hidden /> READ-ONLY
      </div>

      <section className="mt-8 rounded-2xl border border-divider bg-background overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <caption className="sr-only">Vendor rules enforced by the decision engine</caption>
          <thead>
            <tr className="mono-label text-muted-foreground text-left border-b border-divider">
              <th scope="col" className="px-6 py-3 font-normal">Vendor key</th>
              <th scope="col" className="px-6 py-3 font-normal">Amount tolerance</th>
              <th scope="col" className="px-6 py-3 font-normal">Min. auto-approve confidence</th>
              <th scope="col" className="px-6 py-3 font-normal">PO required</th>
              <th scope="col" className="px-6 py-3 font-normal">Currencies</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-divider">
            {Object.entries(vendorRules).map(([vendor, rule]) => (
              <tr key={vendor}>
                <td className="px-6 py-4 font-mono">{vendor}</td>
                <td className="px-6 py-4 tabular-nums">± {rule.amount_tolerance}</td>
                <td className="px-6 py-4 tabular-nums">{formatPercent(rule.minimum_auto_approve_confidence)}</td>
                <td className="px-6 py-4">
                  {rule.require_po_number ? (
                    <span className="inline-flex items-center gap-1.5 text-success">
                      <Check className="size-4" aria-hidden /> Required
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                      <Minus className="size-4" aria-hidden /> Optional
                    </span>
                  )}
                </td>
                <td className="px-6 py-4 font-mono">{rule.allowed_currencies.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="mt-10 rounded-2xl bg-ink text-electric-foreground p-6 md:p-8 relative overflow-hidden">
        <div className="absolute inset-0 opacity-30 texture-dots" aria-hidden="true" />
        <div className="relative">
          <div className="mono-label opacity-70">RUNTIME</div>
          <div className="mt-2 grid md:grid-cols-2 gap-4">
            <div>
              <div className="mono-label opacity-70">API BASE URL</div>
              <div className="mt-1 font-mono text-sm break-all">{API_BASE_URL}</div>
            </div>
            <div>
              <div className="mono-label opacity-70">POLL CADENCE</div>
              <div className="mt-1 font-mono text-sm">2500 ms while jobs are active</div>
            </div>
          </div>
        </div>
      </section>

      <section className="mt-10 rounded-2xl border border-divider bg-background p-6 md:p-8">
        <div className="mono-label text-muted-foreground">DEMO TOOLS</div>
        <p className="mt-2 max-w-2xl text-foreground/70 text-sm">
          Wipes every job, document, and purchase order in this environment. For recording clean demo takes only.
        </p>
        <Button variant="destructive" className="mt-4" disabled={resetting} onClick={handleReset}>
          <RotateCcw className="size-4" aria-hidden />
          {resetting ? "Resetting…" : "Reset demo data"}
        </Button>
      </section>
    </div>
  );
}
