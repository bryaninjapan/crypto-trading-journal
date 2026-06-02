/** 半圆仪表盘（Entry Quality / Opportunity Capture），还原 Stitch detailed_trade_record。 */
export function Gauge({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  const v = value ?? 0;
  const rating =
    v >= 66 ? { text: "Good", color: "#10B981" } : v >= 33 ? { text: "Fair", color: "#f59e0b" } : { text: "Poor", color: "#EF4444" };
  // 半圆：周长的一半 = π*r
  const r = 52;
  const circ = Math.PI * r;
  const dash = (v / 100) * circ;
  return (
    <div className="glass-card !p-lg flex flex-col items-center justify-center gap-sm">
      <span className="w-full text-left text-data-mono text-on-surface-variant">{label}</span>
      <svg viewBox="0 0 120 70" className="w-32">
        <path
          d="M 8 64 A 52 52 0 0 1 112 64"
          fill="none"
          stroke="#31343f"
          strokeWidth="8"
          strokeLinecap="round"
        />
        <path
          d="M 8 64 A 52 52 0 0 1 112 64"
          fill="none"
          stroke={rating.color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circ}`}
          style={{ filter: `drop-shadow(0 0 6px ${rating.color}66)` }}
        />
      </svg>
      <div className="-mt-6 flex flex-col items-center">
        <span className="font-mono text-headline-md" style={{ color: rating.color }}>
          {value === null ? "—" : `${Math.round(v)}%`}
        </span>
        <span className="text-label-caps uppercase text-on-surface-variant">{rating.text}</span>
      </div>
    </div>
  );
}
