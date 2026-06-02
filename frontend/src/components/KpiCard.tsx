import type { ReactNode } from "react";
import { GlassCard } from "./GlassCard";

export function KpiCard({
  label,
  value,
  valueClass = "text-on-surface",
  sub,
}: {
  label: string;
  value: ReactNode;
  valueClass?: string;
  sub?: ReactNode;
}) {
  return (
    <GlassCard className="!p-md flex flex-col gap-xs">
      <span className="text-label-caps uppercase text-on-surface-variant">{label}</span>
      <span className={`font-mono text-headline-md ${valueClass}`}>{value}</span>
      {sub && <span className="font-mono text-data-mono text-on-surface-variant">{sub}</span>}
    </GlassCard>
  );
}
