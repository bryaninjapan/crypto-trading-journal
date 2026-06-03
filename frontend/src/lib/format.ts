export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPnl(n: number | null | undefined, asset = "USDT"): string {
  if (n === null || n === undefined) return "—";
  const sign = n > 0 ? "+" : "";
  const unit = asset === "USDT" ? "$" : "";
  const suffix = asset && asset !== "USDT" ? ` ${asset}` : "";
  return `${sign}${unit}${fmtNum(n, asset === "USDT" ? 2 : 6)}${suffix}`;
}

export function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${fmtNum(n, 2)}%`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (!ms) return "—";
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const parts: string[] = [];
  if (d) parts.push(`${d}d`);
  if (h || d) parts.push(`${h}h`);
  parts.push(`${m}m`);
  return parts.join(" ");
}

export function fmtTime(ms: number | null | undefined): string {
  if (!ms) return "—";
  const dt = new Date(ms);
  return dt.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const pnlClass = (n: number | null | undefined) =>
  n === null || n === undefined || n === 0
    ? "text-on-surface-variant"
    : n > 0
      ? "text-bullish glow-bullish"
      : "text-bearish glow-bearish";

export const MARKET_LABEL: Record<string, string> = {
  usdm: "USD-M",
  coinm: "COIN-M",
};
