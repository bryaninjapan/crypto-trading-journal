import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../lib/useApi";
import { GlassCard } from "../components/GlassCard";
import { KpiCard } from "../components/KpiCard";
import { BalanceDonut } from "../components/charts/BalanceDonut";
import { PlotlyChart } from "../components/charts/PlotlyChart";
import { Loading, ErrorBlock } from "../components/StateBlock";
import { fmtNum, fmtPnl, fmtDuration, pnlClass, MARKET_LABEL } from "../lib/format";

type Market = "spot" | "usdm" | "coinm";

export function Dashboard() {
  const [balanceMarket, setBalanceMarket] = useState<Market | "">("usdm");

  const summary = useApi(() => api.summary(), []);
  const analytics = useApi(() => api.analytics("usdm"), []);

  if (summary.loading || analytics.loading) return <Loading />;
  if (summary.error || analytics.error) return <ErrorBlock error={(summary.error ?? analytics.error)!} />;
  if (!summary.data || !analytics.data) return null;

  const data = summary.data;
  const ana = analytics.data;
  const pnl = data.kpi.futures_realized_pnl;
  const curve = data.equity_curve;

  // 按市場分組餘額
  const byMarket: Record<string, typeof data.balances> = {};
  for (const b of data.balances) (byMarket[b.market] ??= []).push(b);

  // 當前市場的餘額
  const marketBalances = balanceMarket ? byMarket[balanceMarket] || [] : data.balances;

  return (
    <div className="space-y-lg">
      <h1 className="font-sans text-headline-md font-bold">Dashboard</h1>

      {/* 頂部 Stat Cards + Donut */}
      <div className="grid grid-cols-1 gap-md md:grid-cols-[1fr_2fr]">
        {/* 左側：Donut */}
        <GlassCard className="!p-md">
          <span className="text-label-caps uppercase text-on-surface-variant">Total Balance</span>
          <div className="mt-2">
            <BalanceDonut balances={data.balances} />
          </div>
        </GlassCard>

        {/* 右側：2x2 Stat Cards */}
        <div className="grid grid-cols-2 gap-md">
          <KpiCard label="Futures PNL" value={fmtPnl(pnl)} valueClass={pnlClass(pnl)} />
          <KpiCard
            label="Win Rate"
            value={`${fmtNum(data.kpi.win_rate)}%`}
            valueClass={data.kpi.win_rate > 50 ? "text-bullish" : "text-bearish"}
          />
          <KpiCard label="Total Trades" value={fmtNum(data.kpi.total_positions, 0)} />
          <KpiCard label="Avg Hold" value={fmtDuration(ana.kpi?.avg_hold_ms ?? 0)} />
        </div>
      </div>

      {/* PNL 圖 */}
      <GlassCard noOverflow>
        <div className="flex items-center justify-between mb-2">
          <span className="font-sans text-headline-md font-bold">Equity Curve</span>
        </div>
        {curve.length > 0 && (
          <PlotlyChart
            height={300}
            data={[
              {
                x: curve.map((p) => new Date(p.t)),
                y: curve.map((p) => p.cum),
                type: "scatter",
                mode: "lines",
                fill: "tozeroy",
                line: { color: "#7bd0ff", width: 2, shape: "spline" },
                fillcolor: "rgba(123,208,255,0.08)",
              },
            ]}
          />
        )}
      </GlassCard>

      {/* Balances 表格 */}
      <GlassCard className="!p-0 overflow-hidden">
        <div className="border-b border-white/[0.08] px-lg py-md">
          <div className="flex items-center justify-between">
            <span className="text-label-caps uppercase text-on-surface-variant">Balances</span>
            <div className="flex gap-1 rounded-full border border-white/[0.08] bg-surface/50 p-1">
              {["usdm", "coinm", "spot"].map((m) => (
                <button
                  key={m}
                  onClick={() => setBalanceMarket(m as Market)}
                  className={`rounded-full px-2 py-1 text-data-mono text-xs transition-colors ${
                    balanceMarket === m
                      ? "bg-primary/20 text-primary"
                      : "text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  {MARKET_LABEL[m]}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-data-mono text-xs md:text-sm">
            <thead>
              <tr className="border-b border-white/[0.06] text-on-surface-variant">
                <th className="px-lg py-2 text-left">Asset</th>
                <th className="px-2 py-2 text-right">Amount</th>
                <th className="px-2 py-2 text-left">Allocation</th>
                <th className="px-lg py-2 text-right">Value (est.)</th>
              </tr>
            </thead>
            <tbody>
              {marketBalances.slice(0, 10).map((b) => {
                const total = marketBalances.reduce((s, x) => s + (x.balance ?? 0), 0);
                const pct = total > 0 ? ((b.balance ?? 0) / total) * 100 : 0;
                return (
                  <tr key={b.asset || "unknown"} className="border-b border-white/[0.06] hover:bg-surface-container/50">
                    <td className="px-lg py-2 font-semibold text-on-surface">{b.asset || "—"}</td>
                    <td className="px-2 py-2 text-right text-on-surface">{fmtNum(b.balance, 6)}</td>
                    <td className="px-2 py-2">
                      <div className="flex items-center gap-1">
                        <div className="h-1 flex-grow rounded-full bg-surface-container-highest max-w-[100px]">
                          <div
                            className="h-full rounded-full bg-primary"
                            style={{ width: `${Math.min(pct, 100)}%` }}
                          />
                        </div>
                        <span className="text-right min-w-[30px]">{fmtNum(pct, 1)}%</span>
                      </div>
                    </td>
                    <td className="px-lg py-2 text-right text-on-surface-variant">
                      {b.usd_value ? fmtNum(b.usd_value, 2) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </GlassCard>
    </div>
  );
}
