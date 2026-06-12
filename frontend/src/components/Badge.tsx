import { MARKET_LABEL } from "../lib/format";
import type { Direction, Market } from "../api/types";

export function DirectionBadge({ direction }: { direction: Direction }) {
  const long = direction === "Long";
  return (
    <span
      className={`pill ${
        long ? "bg-bullish/10 text-bullish" : "bg-bearish/10 text-bearish"
      }`}
    >
      {direction}
    </span>
  );
}

export function MarketBadge({ market }: { market: Market }) {
  const color: Record<Market, string> = {
    usdm: "bg-primary/10 text-primary",
    coinm: "bg-tertiary/10 text-tertiary",
  };
  return <span className={`pill ${color[market]}`}>{MARKET_LABEL[market]}</span>;
}

export function EstimatedBadge() {
  return (
    <span className="pill bg-white/5 text-on-surface-variant" title="现货 FIFO 估算，非交易所口径">
      EST
    </span>
  );
}
