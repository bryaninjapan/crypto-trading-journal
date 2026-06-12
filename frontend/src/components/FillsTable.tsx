import { fmtNum, fmtPnl, fmtTime } from "../lib/format";

export interface Fill {
  trade_id: number;
  side: "BUY" | "SELL";
  price: number;
  qty_base: number;
  realized_pnl: number | null;
  fee: number | null;
  fee_asset: string | null;
  trade_time: number;
}

export function FillsTable({ fills, pnl_asset }: { fills: Fill[]; pnl_asset?: string }) {
  if (!fills || fills.length === 0) {
    return <div className="text-data-mono text-on-surface-variant">No fills recorded</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-data-mono text-xs">
        <thead>
          <tr className="border-b border-white/[0.06] text-on-surface-variant">
            <th className="px-2 py-2 text-left">#</th>
            <th className="px-2 py-2 text-left">Side</th>
            <th className="px-2 py-2 text-right">Price</th>
            <th className="px-2 py-2 text-right">Qty</th>
            <th className="px-2 py-2 text-right">Fee</th>
            <th className="px-2 py-2 text-right">PNL</th>
            <th className="px-2 py-2 text-left">Time</th>
          </tr>
        </thead>
        <tbody>
          {fills.map((f, idx) => (
            <tr key={f.trade_id} className="border-b border-white/[0.06] hover:bg-surface-container/50">
              <td className="px-2 py-2 text-on-surface-variant">#{idx + 1}</td>
              <td className="px-2 py-2">
                <span className={`font-semibold ${f.side === "BUY" ? "text-bullish" : "text-bearish"}`}>
                  {f.side}
                </span>
              </td>
              <td className="px-2 py-2 text-right text-on-surface">{fmtNum(f.price, 6)}</td>
              <td className="px-2 py-2 text-right text-on-surface">{fmtNum(f.qty_base, 6)}</td>
              <td className="px-2 py-2 text-right text-on-surface-variant">{f.fee ? fmtNum(f.fee, 6) : "—"}</td>
              <td className="px-2 py-2 text-right font-semibold">
                {f.realized_pnl !== null && f.realized_pnl !== 0 ? (
                  <span className={f.realized_pnl > 0 ? "text-bullish" : "text-bearish"}>
                    {fmtPnl(f.realized_pnl, pnl_asset || "USDT")}
                  </span>
                ) : (
                  <span className="text-on-surface-variant">—</span>
                )}
              </td>
              <td className="px-2 py-2 text-on-surface-variant">{fmtTime(f.trade_time)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
