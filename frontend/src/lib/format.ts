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

// COIN-M 的 realized PNL 以标的币（ADA/BTC…）计价，需并排显示换算后的 USD 估值。
// usdm（asset==="USDT"）维持纯 $ 显示；coinm 显示「-172.500000 ADA (≈ -$78.20)」。
// pnlUsd 缺失（缺现价）时只显示币本位部分。
export function fmtPnlWithUsd(
  n: number | null | undefined,
  asset = "USDT",
  pnlUsd?: number | null,
): string {
  const base = fmtPnl(n, asset);
  if (asset === "USDT" || pnlUsd === null || pnlUsd === undefined) return base;
  const sign = pnlUsd > 0 ? "+" : pnlUsd < 0 ? "-" : "";
  return `${base} (≈ ${sign}$${fmtNum(Math.abs(pnlUsd), 2)})`;
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
