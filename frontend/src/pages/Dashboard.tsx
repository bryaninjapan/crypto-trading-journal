import { api } from "../api/client";
import { useApi } from "../lib/useApi";
import { GlassCard } from "../components/GlassCard";
import { KpiCard } from "../components/KpiCard";
import { PlotlyChart } from "../components/charts/PlotlyChart";
import { Loading, ErrorBlock } from "../components/StateBlock";
import { fmtNum, fmtPnl, pnlClass, MARKET_LABEL } from "../lib/format";

export function Dashboard() {
  const { data, error, loading } = useApi(() => api.summary(), []);
  if (loading) return <Loading />;
  if (error) return <ErrorBlock error={error} />;
  if (!data) return null;

  const pnl = data.kpi.usdm_realized_pnl;
  const curve = data.equity_curve;

  // 余额按市场分组
  const byMarket: Record<string, typeof data.balances> = {};
  for (const b of data.balances) (byMarket[b.market] ??= []).push(b);

  return (
    <div className="space-y-lg">
      <h1 className="font-sans text-headline-md font-bold">Dashboard</h1>

      {/* Consolidated PNL 大卡 */}
      <GlassCard noOverflow className="relative">
        <span className="text-label-caps uppercase text-on-surface-variant">
          Consolidated PNL (USD-M)
        </span>
        <div className={`mt-2 font-mono text-display-lg-mobile md:text-display-lg ${pnlClass(pnl)}`}>
          {fmtPnl(pnl)}
        </div>
        {curve.length > 0 && (
          <div className="mt-4">
            <PlotlyChart
              height={180}
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
          </div>
        )}
        <div className="mt-2 text-data-mono text-on-surface-variant">{data.note}</div>
      </GlassCard>

      {/* 账户余额（按市场） */}
      <div className="grid grid-cols-1 gap-md md:grid-cols-3">
        {Object.entries(byMarket).map(([market, items]) => (
          <GlassCard key={market} hover className="!p-md">
            <div className="text-label-caps uppercase text-on-surface-variant">
              {MARKET_LABEL[market]} Balance
            </div>
            <div className="mt-2 space-y-1">
              {items.slice(0, 4).map((b) => (
                <div key={b.asset} className="flex justify-between font-mono text-data-mono">
                  <span className="text-on-surface-variant">{b.asset}</span>
                  <span className="text-on-surface">{fmtNum(b.balance ?? 0, 6)}</span>
                </div>
              ))}
            </div>
          </GlassCard>
        ))}
        {data.balances.length === 0 && (
          <GlassCard className="!p-md text-on-surface-variant md:col-span-3">
            暂无余额快照 —— 跑一次 sync_binance 后生成。
          </GlassCard>
        )}
      </div>

      {/* KPI */}
      <div className="grid grid-cols-2 gap-md md:grid-cols-3">
        <KpiCard label="Total Positions" value={fmtNum(data.kpi.total_positions, 0)} />
        <KpiCard label="Win Rate" value={`${fmtNum(data.kpi.win_rate)}%`} />
        <KpiCard
          label="Total PNL"
          value={fmtPnl(pnl)}
          valueClass={pnlClass(pnl)}
        />
      </div>
    </div>
  );
}
