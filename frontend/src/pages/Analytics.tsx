import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../lib/useApi";
import { GlassCard } from "../components/GlassCard";
import { KpiCard } from "../components/KpiCard";
import { PlotlyChart } from "../components/charts/PlotlyChart";
import { Loading, ErrorBlock } from "../components/StateBlock";
import { fmtPnl, fmtNum, fmtDuration, pnlClass } from "../lib/format";

export function Analytics() {
  const [mode, setMode] = useState<"pnl" | "drawdown">("pnl");
  const { data, error, loading } = useApi(() => api.analytics("usdm"), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBlock error={error} />;
  if (!data) return null;

  const c = data.equity_curve;
  const x = c.map((p) => new Date(p.t));
  const stats = data.statistics;
  const donut = data.expectancy_donut;

  return (
    <div className="space-y-lg">
      <div>
        <h1 className="font-sans text-headline-md font-bold">Analytics</h1>
        <p className="text-data-mono text-on-surface-variant">{data.note}</p>
      </div>

      {/* 权益 / 回撤 切换 */}
      <GlassCard>
        <div className="mb-2 flex items-center justify-between">
          <span className="font-mono text-headline-md font-bold">
            {mode === "pnl" ? "PNL" : "Max Drawdown"}
          </span>
          <div className="flex gap-1 rounded-full border border-white/[0.08] p-1">
            {(["pnl", "drawdown"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`rounded-full px-3 py-1 text-data-mono transition-colors ${
                  mode === m ? "bg-primary/20 text-primary" : "text-on-surface-variant"
                }`}
              >
                {m === "pnl" ? "PNL" : "Drawdown"}
              </button>
            ))}
          </div>
        </div>
        <PlotlyChart
          height={300}
          data={[
            mode === "pnl"
              ? {
                  x,
                  y: c.map((p) => p.cum),
                  type: "scatter",
                  mode: "lines",
                  fill: "tozeroy",
                  line: { color: "#10B981", width: 2, shape: "spline" },
                  fillcolor: "rgba(16,185,129,0.08)",
                }
              : {
                  x,
                  y: c.map((p) => p.drawdown),
                  type: "scatter",
                  mode: "lines",
                  fill: "tozeroy",
                  line: { color: "#EF4444", width: 2 },
                  fillcolor: "rgba(239,68,68,0.08)",
                },
          ]}
        />
      </GlassCard>

      <div className="grid grid-cols-2 gap-md">
        <KpiCard label="Total Trades" value={fmtNum(donut.wins + donut.losses, 0)} />
        <KpiCard label="Avg Hold" value={fmtDuration(stats.avg_hold_ms)} />
      </div>

      {/* 期望值环（胜负） */}
      <GlassCard>
        <span className="text-label-caps uppercase text-on-surface-variant">Expectancy</span>
        <PlotlyChart
          height={260}
          data={[
            {
              values: [donut.wins, donut.losses],
              labels: ["Wins", "Losses"],
              type: "pie",
              hole: 0.62,
              marker: { colors: ["#10B981", "#EF4444"] },
              textinfo: "none",
              sort: false,
            } as any,
          ]}
          layout={{
            showlegend: true,
            annotations: [
              {
                text: `${fmtNum(donut.win_rate)}%`,
                font: { size: 26, color: "#e0e2ef" },
                showarrow: false,
              },
            ],
          }}
        />
        <div className="flex justify-around font-mono text-data-mono">
          <span className="text-bullish">{donut.wins}W</span>
          <span className="text-bearish">{donut.losses}L</span>
        </div>
      </GlassCard>

      {/* 多空比 */}
      <GlassCard>
        <span className="text-label-caps uppercase text-on-surface-variant">Long / Short Ratio</span>
        <div className="mt-3 flex h-3 overflow-hidden rounded-full">
          <div className="h-full bg-bullish" style={{ width: `${data.long_short.long_pct}%` }} />
          <div className="h-full bg-bearish" style={{ width: `${100 - data.long_short.long_pct}%` }} />
        </div>
        <div className="mt-2 flex justify-between font-mono text-data-mono">
          <span className="text-bullish">Long {data.long_short.longs}</span>
          <span className="text-bearish">Short {data.long_short.shorts}</span>
        </div>
      </GlassCard>

      {/* 统计 */}
      <GlassCard className="space-y-2">
        <span className="text-label-caps uppercase text-on-surface-variant">Statistics</span>
        <Stat label="Total Gain/Loss" value={fmtPnl(stats.total_gain_loss)} cls={pnlClass(stats.total_gain_loss)} />
        <Stat label="Trade Expectancy" value={fmtPnl(stats.trade_expectancy)} cls={pnlClass(stats.trade_expectancy)} />
        <Stat label="Avg Daily Gain" value={fmtPnl(stats.avg_daily_gain)} cls={pnlClass(stats.avg_daily_gain)} />
        <Stat label="Avg Win" value={fmtPnl(stats.avg_win)} cls="text-bullish" />
        <Stat label="Avg Loss" value={fmtPnl(stats.avg_loss)} cls="text-bearish" />
      </GlassCard>
    </div>
  );
}

function Stat({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="flex justify-between border-b border-white/[0.06] py-2 font-mono text-data-mono last:border-0">
      <span className="text-on-surface-variant">{label}</span>
      <span className={cls}>{value}</span>
    </div>
  );
}
