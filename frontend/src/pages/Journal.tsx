import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../lib/useApi";
import { GlassCard } from "../components/GlassCard";
import { Pagination } from "../components/Pagination";
import { TradeRow } from "../components/TradeRow";
import { MarketBadge, EstimatedBadge } from "../components/Badge";
import { Loading, ErrorBlock, Empty } from "../components/StateBlock";
import { fmtPnl, fmtNum, fmtDuration, pnlClass } from "../lib/format";

const MARKETS = ["", "usdm", "coinm", "spot"];

export function Journal() {
  const nav = useNavigate();
  const [market, setMarket] = useState("");
  const [limit, setLimit] = useState(20);
  const [offset, setOffset] = useState(0);

  const symbols = useApi(() => api.symbols(market || undefined), [market]);
  const positions = useApi(
    () => api.positions({ market: market || undefined, status: "closed", limit, offset }),
    [market, limit, offset],
  );

  return (
    <div className="space-y-lg">
      <div className="flex items-center justify-between">
        <h1 className="font-sans text-headline-md font-bold">Journal</h1>
        <div className="flex gap-1 rounded-full border border-white/[0.08] bg-surface/50 p-1">
          {MARKETS.map((m) => (
            <button
              key={m || "all"}
              onClick={() => setMarket(m)}
              className={`rounded-full px-3 py-1 text-data-mono transition-colors ${
                market === m ? "bg-primary/20 text-primary" : "text-on-surface-variant"
              }`}
            >
              {m === "" ? "All" : m === "usdm" ? "USD-M" : m === "coinm" ? "COIN-M" : "Spot"}
            </button>
          ))}
        </div>
      </div>

      {/* Traded Symbols Report */}
      <section className="space-y-sm">
        <h2 className="text-label-caps uppercase text-on-surface-variant">Traded Symbols</h2>
        {symbols.loading ? (
          <Loading />
        ) : symbols.error ? (
          <ErrorBlock error={symbols.error} />
        ) : !symbols.data?.symbols.length ? (
          <Empty />
        ) : (
          <div className="grid grid-cols-1 gap-md md:grid-cols-2">
            {symbols.data.symbols.map((s) => (
              <GlassCard
                key={`${s.market}-${s.symbol}`}
                hover
                className="!p-md cursor-pointer"
                onClick={() => nav(`/positions/${s.symbol}`)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-sans text-body-bold font-semibold">{s.symbol}</span>
                    <MarketBadge market={s.market} />
                  </div>
                </div>
                <div className="mt-3 font-mono text-data-mono text-on-surface-variant">
                  <div>
                    <span className="text-[10px] uppercase">Trades: </span>
                    <span className="text-on-surface">{s.trades}</span>
                  </div>
                </div>
              </GlassCard>
            ))}
          </div>
        )}
      </section>

      {/* Trade History Table */}
      <section className="space-y-sm">
        <h2 className="text-label-caps uppercase text-on-surface-variant">Trade History</h2>
        {positions.loading ? (
          <Loading />
        ) : positions.error ? (
          <ErrorBlock error={positions.error} />
        ) : !positions.data?.positions.length ? (
          <Empty />
        ) : (
          <GlassCard className="!p-0 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-data-mono text-xs md:text-sm">
                <thead>
                  <tr className="border-b border-white/[0.06] text-on-surface-variant">
                    <th className="px-lg py-2 text-left">#</th>
                    <th className="px-2 py-2 text-left">Symbol</th>
                    <th className="px-2 py-2 text-left">Open Price @ Time</th>
                    <th className="px-2 py-2 text-left">Hold</th>
                    <th className="px-2 py-2 text-left">Close Price @ Time</th>
                    <th className="px-lg py-2 text-right">PNL</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.data.positions.map((p, idx) => (
                    <TradeRow
                      key={p.id}
                      position={p}
                      rowNum={offset + idx + 1}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              limit={limit}
              setLimit={setLimit}
              offset={offset}
              setOffset={setOffset}
              total={positions.data.total}
            />
          </GlassCard>
        )}
      </section>
    </div>
  );
}
