import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../lib/useApi";
import { GlassCard } from "../components/GlassCard";
import { PlotlyChart } from "../components/charts/PlotlyChart";
import { Loading, ErrorBlock } from "../components/StateBlock";
import { fmtPnl, fmtNum, fmtDuration, pnlClass } from "../lib/format";

type Market = "usdm" | "coinm" | "spot";

export function Analytics() {
  const [market, setMarket] = useState<Market>("usdm");
  const { data, error, loading } = useApi(() => api.analytics(market), [market]);

  if (loading) return <Loading />;
  if (error) return <ErrorBlock error={error} />;
  if (!data) return null;

  const c = data.equity_curve;
  const x = c.map((p) => new Date(p.t));
  const stats = data.statistics;
  const donut = data.expectancy_donut;

  return (
    <div className="space-y-lg">
      <div className="flex items-center justify-between">
        <h1 className="font-sans text-headline-md font-bold">Analytics</h1>
        <div className="flex gap-1 rounded-full border border-white/[0.08] bg-surface/50 p-1">
          {["usdm", "coinm", "spot"].map((m) => (
            <button
              key={m}
              onClick={() => setMarket(m as Market)}
              className={`rounded-full px-3 py-1 text-data-mono text-xs transition-colors ${
                market === m ? "bg-primary/20 text-primary" : "text-on-surface-variant"
              }`}
            >
              {m === "usdm" ? "USD-M" : m === "coinm" ? "COIN-M" : "Spot"}
            </button>
          ))}
        </div>
      </div>

      {/* 4 個圖表 Grid */}
      <div className="grid grid-cols-1 gap-md md:grid-cols-2">
        {/* 1. Equity Curve */}
        <GlassCard noOverflow>
          <div className="mb-2">
            <span className="text-label-caps uppercase text-on-surface-variant">Equity Curve</span>
          </div>
          <PlotlyChart
            height={240}
            data={[
              {
                x,
                y: c.map((p) => p.cum),
                type: "scatter",
                mode: "lines",
                fill: "tozeroy",
                line: { color: "#10B981", width: 2, shape: "spline" },
                fillcolor: "rgba(16,185,129,0.08)",
              },
            ]}
          />
        </GlassCard>

        {/* 2. Drawdown */}
        <GlassCard noOverflow>
          <div className="mb-2">
            <span className="text-label-caps uppercase text-on-surface-variant">Max Drawdown</span>
          </div>
          <PlotlyChart
            height={240}
            data={[
              {
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

        {/* 3. PNL Distribution */}
        <GlassCard noOverflow>
          <div className="mb-2">
            <span className="text-label-caps uppercase text-on-surface-variant">PNL Distribution</span>
          </div>
          <PlotlyChart
            height={240}
            data={[
              {
                x: ["Win", "Loss"],
                y: [donut.wins, donut.losses],
                type: "bar",
                marker: { color: ["#10B981", "#EF4444"] },
              } as any,
            ]}
            layout={{
              showlegend: false,
              xaxis: { type: "category" },
            }}
          />
        </GlassCard>

        {/* 4. Hold Histogram */}
        <GlassCard noOverflow>
          <div className="mb-2">
            <span className="text-label-caps uppercase text-on-surface-variant">Hold Distribution</span>
          </div>
          <PlotlyChart
            height={240}
            data={[
              {
                x: ["<1h", "1h-1d", "1d-1w", ">1w"],
                y: [18, 45, 67, 26],
                type: "bar",
                marker: { color: "#7bd0ff" },
              } as any,
            ]}
            layout={{
              showlegend: false,
              xaxis: { type: "category" },
            }}
          />
        </GlassCard>
      </div>

      {/* Expectancy Donut */}
      <GlassCard>
        <span className="text-label-caps uppercase text-on-surface-variant">Expectancy (Win/Loss)</span>
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

      {/* Long / Short Ratio */}
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

      {/* Statistics - Dual Column */}
      <GlassCard>
        <span className="text-label-caps uppercase text-on-surface-variant">Statistics</span>
        <div className="mt-4 grid grid-cols-2 gap-md">
          <div className="space-y-2">
            <Stat label="Total Gain/Loss" value={fmtPnl(stats.total_gain_loss)} cls={pnlClass(stats.total_gain_loss)} />
            <Stat label="Trade Expectancy" value={fmtPnl(stats.trade_expectancy)} cls={pnlClass(stats.trade_expectancy)} />
            <Stat label="Avg Daily Gain" value={fmtPnl(stats.avg_daily_gain)} cls={pnlClass(stats.avg_daily_gain)} />
          </div>
          <div className="space-y-2">
            <Stat label="Avg Win" value={fmtPnl(stats.avg_win)} cls="text-bullish" />
            <Stat label="Avg Loss" value={fmtPnl(stats.avg_loss)} cls="text-bearish" />
            <Stat label="Avg Hold" value={fmtDuration(stats.avg_hold_ms)} cls="text-on-surface" />
          </div>
        </div>
      </GlassCard>
    </div>
  );
}

function Stat({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="flex justify-between border-b border-white/[0.06] py-2 font-mono text-data-mono text-xs last:border-0">
      <span className="text-on-surface-variant">{label}</span>
      <span className={cls}>{value}</span>
    </div>
  );
}
