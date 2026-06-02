import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../lib/useApi";
import { GlassCard } from "../components/GlassCard";
import { DirectionBadge, MarketBadge, EstimatedBadge } from "../components/Badge";
import { Loading, ErrorBlock, Empty } from "../components/StateBlock";
import { fmtPnl, fmtNum, fmtDuration, fmtTime, pnlClass } from "../lib/format";

const MARKETS = ["", "usdm", "coinm", "spot"];

export function Journal() {
  const [market, setMarket] = useState("");
  const nav = useNavigate();
  const symbols = useApi(() => api.symbols(market || undefined), [market]);
  const positions = useApi(
    () => api.positions({ market: market || undefined, status: "closed", limit: 50 }),
    [market],
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
              <GlassCard key={`${s.market}-${s.symbol}`} hover className="!p-md">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-sans text-body-bold font-semibold">{s.symbol}</span>
                    <MarketBadge market={s.market} />
                    {s.is_estimated && <EstimatedBadge />}
                  </div>
                  <span className={`font-mono text-body-bold ${pnlClass(s.total_gain)}`}>
                    {fmtPnl(s.total_gain, s.pnl_asset || "USDT")}
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-4 gap-2 font-mono text-data-mono text-on-surface-variant">
                  <div>
                    <div className="text-[10px] uppercase">Trades</div>
                    <div className="text-on-surface">{s.trades}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase">Win</div>
                    <div className="text-on-surface">{fmtNum(s.win_rate)}%</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase">L / S</div>
                    <div className="text-on-surface">
                      {s.longs}/{s.shorts}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase">Avg Hold</div>
                    <div className="text-on-surface">{fmtDuration(s.avg_hold_ms)}</div>
                  </div>
                </div>
              </GlassCard>
            ))}
          </div>
        )}
      </section>

      {/* 最近持仓列表 → 详情 */}
      <section className="space-y-sm">
        <h2 className="text-label-caps uppercase text-on-surface-variant">Recent Positions</h2>
        {positions.loading ? (
          <Loading />
        ) : positions.error ? (
          <ErrorBlock error={positions.error} />
        ) : !positions.data?.positions.length ? (
          <Empty />
        ) : (
          <div className="space-y-2">
            {positions.data.positions.map((p) => (
              <button
                key={p.id}
                onClick={() => nav(`/positions/${p.id}`)}
                className="glass-card glass-card-hover flex w-full items-center justify-between !p-md text-left"
              >
                <div className="flex items-center gap-2">
                  <DirectionBadge direction={p.direction} />
                  <span className="font-sans text-body-bold font-semibold">{p.symbol}</span>
                  <span className="font-mono text-data-mono text-on-surface-variant">
                    {fmtTime(p.open_time)}
                  </span>
                </div>
                <div className="flex items-center gap-4">
                  <span className="font-mono text-data-mono text-on-surface-variant">
                    {fmtDuration(p.hold_ms)}
                  </span>
                  <span className={`font-mono text-body-bold ${pnlClass(p.realized_pnl)}`}>
                    {fmtPnl(p.realized_pnl, p.pnl_asset || "USDT")}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
