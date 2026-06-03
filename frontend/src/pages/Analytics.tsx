import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../lib/useApi";
import { GlassCard } from "../components/GlassCard";
import { PlotlyChart } from "../components/charts/PlotlyChart";
import { Loading, ErrorBlock } from "../components/StateBlock";
import { fmtPnl, fmtNum, fmtDuration, pnlClass } from "../lib/format";

type Market = "usdm" | "coinm";

export function Analytics() {
  const [market, setMarket] = useState<Market>("usdm");
  const { data, error, loading } = useApi(() => api.analytics(market), [market]);

  if (loading) return <Loading />;
  if (error) return <ErrorBlock error={error} />;
  if (!data) return null;

  const kpi = data.kpi;
  const stats = data.statistics;
  const longs = data.longs;
  const shorts = data.shorts;

  return (
    <div className="space-y-lg">
      {/* Header + Market Filter */}
      <div className="flex items-center justify-between">
        <h1 className="font-sans text-headline-md font-bold">Analytics</h1>
        <div className="flex gap-1 rounded-full border border-white/[0.08] bg-surface/50 p-1">
          {["usdm", "coinm"].map((m) => (
            <button
              key={m}
              onClick={() => setMarket(m as Market)}
              className={`rounded-full px-3 py-1 text-data-mono text-xs transition-colors ${
                market === m ? "bg-primary/20 text-primary" : "text-on-surface-variant"
              }`}
            >
              {m === "usdm" ? "USD-M" : "COIN-M"}
            </button>
          ))}
        </div>
      </div>

      {/* KPI Row: 4 Cards */}
      <div className="grid grid-cols-1 gap-md md:grid-cols-4">
        {/* Total Trades */}
        <GlassCard>
          <div className="text-label-caps uppercase text-on-surface-variant">Total Trades</div>
          <div className="mt-3 text-headline-lg font-bold">{kpi.total_trades}</div>
        </GlassCard>

        {/* Avg Hold */}
        <GlassCard>
          <div className="text-label-caps uppercase text-on-surface-variant">Avg Hold</div>
          <div className="mt-3 text-headline-lg font-bold">{fmtDuration(kpi.avg_hold_ms)}</div>
        </GlassCard>

        {/* Win Rate Donut */}
        <GlassCard noOverflow>
          <div className="text-label-caps uppercase text-on-surface-variant">Win Rate</div>
          <PlotlyChart
            height={180}
            data={[
              {
                values: [kpi.win_rate, 100 - kpi.win_rate],
                labels: ["Wins", "Losses"],
                type: "pie",
                hole: 0.62,
                marker: { colors: ["#10B981", "#EF4444"] },
                textinfo: "none",
                sort: false,
              } as any,
            ]}
            layout={{
              showlegend: false,
              annotations: [
                {
                  text: `${fmtNum(kpi.win_rate)}%`,
                  font: { size: 20, color: "#e0e2ef" },
                  showarrow: false,
                },
              ],
            }}
          />
        </GlassCard>

        {/* Long/Short Ratio */}
        <GlassCard>
          <div className="text-label-caps uppercase text-on-surface-variant">Long / Short</div>
          <div className="mt-3 flex h-3 overflow-hidden rounded-full">
            <div className="h-full bg-bullish" style={{ width: `${kpi.long_pct}%` }} />
            <div className="h-full bg-bearish" style={{ width: `${100 - kpi.long_pct}%` }} />
          </div>
          <div className="mt-2 flex justify-between font-mono text-data-mono text-xs">
            <span className="text-bullish">{kpi.longs}</span>
            <span className="text-bearish">{kpi.shorts}</span>
          </div>
        </GlassCard>
      </div>

      {/* Statistics Section: 2-Column Table */}
      <GlassCard>
        <span className="text-label-caps uppercase text-on-surface-variant">Statistics</span>
        <div className="mt-4 grid grid-cols-2 gap-md">
          {/* Left Column */}
          <div className="space-y-0 border-r border-white/[0.06]">
            <StatRow label="Total Gain/Loss" value={fmtPnl(stats.total_gain_loss)} cls={pnlClass(stats.total_gain_loss)} />
            <StatRow label="Trade Expectancy" value={fmtPnl(stats.trade_expectancy)} cls={pnlClass(stats.trade_expectancy)} />
            <StatRow label="Avg Daily Gain" value={fmtPnl(stats.avg_daily_gain)} cls={pnlClass(stats.avg_daily_gain)} />
            <StatRow label="Avg Daily Volume" value={fmtPnl(stats.avg_daily_volume)} cls="text-on-surface" />
            <StatRow label="Largest Gain" value={fmtPnl(stats.largest_gain)} cls="text-bullish" />
            <StatRow label="Total Trades Volume" value={fmtPnl(stats.total_trades_volume)} cls="text-on-surface" />
          </div>

          {/* Right Column */}
          <div className="space-y-0">
            <StatRow label="Avg Trades/Day" value={fmtNum(stats.avg_trades_per_day)} cls="text-on-surface" />
            <StatRow label="Avg Trade Win" value={fmtPnl(stats.avg_trade_win)} cls="text-bullish" />
            <StatRow label="Avg Trade Loss" value={fmtPnl(stats.avg_trade_loss)} cls="text-bearish" />
            <StatRow label="Max Consecutive Win" value={`${stats.max_consecutive_win}`} cls="text-on-surface" />
            <StatRow label="Max Consecutive Loss" value={`${stats.max_consecutive_loss}`} cls="text-on-surface" />
            <StatRow label="Largest Losses" value={fmtPnl(stats.largest_losses)} cls="text-bearish" />
          </div>
        </div>
      </GlassCard>

      {/* Longs Section: 4-Column Cards */}
      <div>
        <h2 className="mb-md text-label-caps uppercase text-on-surface-variant">Longs</h2>
        <div className="grid grid-cols-1 gap-md md:grid-cols-4">
          {/* Longs Count */}
          <GlassCard>
            <div className="text-label-caps uppercase text-on-surface-variant">Longs</div>
            <div className="mt-3 text-center">
              <div className="text-headline-xl font-bold text-bullish">{longs.count}</div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-container">
                <div className="h-full bg-bullish" style={{ width: `${(longs.count / kpi.total_trades) * 100}%` }} />
              </div>
              <div className="mt-2 text-data-mono text-xs text-on-surface-variant">
                {fmtNum((longs.count / kpi.total_trades) * 100)}% of total
              </div>
            </div>
          </GlassCard>

          {/* Long Trade Win Ratio */}
          <GlassCard noOverflow>
            <div className="text-label-caps uppercase text-on-surface-variant">Long Trade Win Ratio</div>
            <PlotlyChart
              height={160}
              data={[
                {
                  values: [longs.wins, longs.losses],
                  labels: ["Wins", "Losses"],
                  type: "pie",
                  hole: 0.62,
                  marker: { colors: ["#10B981", "#EF4444"] },
                  textinfo: "none",
                  sort: false,
                } as any,
              ]}
              layout={{
                showlegend: false,
                annotations: [
                  {
                    text: `${fmtNum(longs.win_ratio)}%`,
                    font: { size: 18, color: "#e0e2ef" },
                    showarrow: false,
                  },
                ],
              }}
            />
            <div className="mt-2 text-center text-data-mono text-xs text-on-surface-variant">
              {longs.wins}W / {longs.losses}L
            </div>
          </GlassCard>

          {/* Avg Long Trade Duration */}
          <GlassCard>
            <div className="text-label-caps uppercase text-on-surface-variant">Avg Long Duration</div>
            <div className="mt-3 text-center">
              <div className="text-body-bold font-semibold text-on-surface">{fmtDuration(longs.avg_duration_ms)}</div>
              <div className="mt-3 text-data-mono text-xs text-on-surface-variant">
                Avg Win: {fmtPnl(longs.avg_win, "USDT")}
              </div>
              <div className="text-data-mono text-xs text-on-surface-variant">
                Avg Loss: {fmtPnl(longs.avg_loss, "USDT")}
              </div>
            </div>
          </GlassCard>

          {/* Total Long Realized PNL */}
          <GlassCard>
            <div className="text-label-caps uppercase text-on-surface-variant">Total Long Realised PNL</div>
            <div className={`mt-3 text-center text-headline-lg font-bold ${pnlClass(longs.total_realized_pnl)}`}>
              {fmtPnl(longs.total_realized_pnl, "USDT")}
            </div>
            <div className="mt-3 text-center text-data-mono text-xs text-on-surface-variant">
              Avg W: {fmtPnl(longs.avg_win, "USDT")} | Loss: {fmtPnl(longs.avg_loss, "USDT")}
            </div>
          </GlassCard>
        </div>
      </div>

      {/* Shorts Section: 4-Column Cards */}
      <div>
        <h2 className="mb-md text-label-caps uppercase text-on-surface-variant">Shorts</h2>
        <div className="grid grid-cols-1 gap-md md:grid-cols-4">
          {/* Shorts Count */}
          <GlassCard>
            <div className="text-label-caps uppercase text-on-surface-variant">Shorts</div>
            <div className="mt-3 text-center">
              <div className="text-headline-xl font-bold text-bearish">{shorts.count}</div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-container">
                <div className="h-full bg-bearish" style={{ width: `${(shorts.count / kpi.total_trades) * 100}%` }} />
              </div>
              <div className="mt-2 text-data-mono text-xs text-on-surface-variant">
                {fmtNum((shorts.count / kpi.total_trades) * 100)}% of total
              </div>
            </div>
          </GlassCard>

          {/* Short Trade Win Ratio */}
          <GlassCard noOverflow>
            <div className="text-label-caps uppercase text-on-surface-variant">Short Trade Win Ratio</div>
            <PlotlyChart
              height={160}
              data={[
                {
                  values: [shorts.wins, shorts.losses],
                  labels: ["Wins", "Losses"],
                  type: "pie",
                  hole: 0.62,
                  marker: { colors: ["#10B981", "#EF4444"] },
                  textinfo: "none",
                  sort: false,
                } as any,
              ]}
              layout={{
                showlegend: false,
                annotations: [
                  {
                    text: `${fmtNum(shorts.win_ratio)}%`,
                    font: { size: 18, color: "#e0e2ef" },
                    showarrow: false,
                  },
                ],
              }}
            />
            <div className="mt-2 text-center text-data-mono text-xs text-on-surface-variant">
              {shorts.wins}W / {shorts.losses}L
            </div>
          </GlassCard>

          {/* Avg Short Trade Duration */}
          <GlassCard>
            <div className="text-label-caps uppercase text-on-surface-variant">Avg Short Duration</div>
            <div className="mt-3 text-center">
              <div className="text-body-bold font-semibold text-on-surface">{fmtDuration(shorts.avg_duration_ms)}</div>
              <div className="mt-3 text-data-mono text-xs text-on-surface-variant">
                Avg Win: {fmtPnl(shorts.avg_win, "USDT")}
              </div>
              <div className="text-data-mono text-xs text-on-surface-variant">
                Avg Loss: {fmtPnl(shorts.avg_loss, "USDT")}
              </div>
            </div>
          </GlassCard>

          {/* Total Short Realized PNL */}
          <GlassCard>
            <div className="text-label-caps uppercase text-on-surface-variant">Total Short Realised PNL</div>
            <div className={`mt-3 text-center text-headline-lg font-bold ${pnlClass(shorts.total_realized_pnl)}`}>
              {fmtPnl(shorts.total_realized_pnl, "USDT")}
            </div>
            <div className="mt-3 text-center text-data-mono text-xs text-on-surface-variant">
              Avg W: {fmtPnl(shorts.avg_win, "USDT")} | Loss: {fmtPnl(shorts.avg_loss, "USDT")}
            </div>
          </GlassCard>
        </div>
      </div>
    </div>
  );
}

function StatRow({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="flex justify-between border-b border-white/[0.06] py-2 font-mono text-data-mono text-xs last:border-0">
      <span className="text-on-surface-variant">{label}</span>
      <span className={cls}>{value}</span>
    </div>
  );
}
