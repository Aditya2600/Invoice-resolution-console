import { cn } from "@/lib/utils";

const TONE = {
  default: "bg-panel text-foreground",
  electric: "bg-electric text-electric-foreground",
  warning: "bg-acid text-foreground",
  success: "bg-success/15 text-foreground",
  destructive: "bg-destructive/10 text-foreground",
} as const;

export function KpiTile({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: number | string;
  tone?: keyof typeof TONE;
}) {
  return (
    <div className={cn("relative overflow-hidden rounded-2xl p-6 md:p-7 border border-divider", TONE[tone])}>
      <div className="mono-label opacity-70">{label}</div>
      <div className="mt-3 text-5xl md:text-6xl font-semibold tracking-tight tabular-nums">{value}</div>
    </div>
  );
}
