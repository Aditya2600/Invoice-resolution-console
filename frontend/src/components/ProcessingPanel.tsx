import { motion, useReducedMotion } from "motion/react";

export function ProcessingPanel({ label = "Worker is running" }: { label?: string }) {
  const reduce = useReducedMotion();
  return (
    <div className="relative overflow-hidden rounded-2xl bg-electric text-electric-foreground p-8 md:p-10 border border-electric/40">
      <div
        className={`absolute inset-0 texture-pixel-field opacity-90 ${reduce ? "" : "animate-dither"}`}
        aria-hidden="true"
      />
      <div className="absolute inset-0 texture-dither opacity-40" aria-hidden="true" />
      <div className="relative flex flex-col gap-6">
        <div className="flex items-center gap-3">
          <motion.span
            aria-hidden
            className="size-2.5 rounded-full bg-white"
            animate={reduce ? undefined : { opacity: [0.4, 1, 0.4] }}
            transition={{ duration: 1.4, repeat: Infinity }}
          />
          <span className="mono-label">{label}</span>
        </div>
        <h2 className="text-4xl md:text-6xl font-semibold tracking-tight leading-[0.95] max-w-3xl">
          Processing.
        </h2>
      </div>
    </div>
  );
}
