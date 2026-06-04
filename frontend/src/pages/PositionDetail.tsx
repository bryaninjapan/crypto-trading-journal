import { useParams, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../lib/useApi";
import { GlassCard } from "../components/GlassCard";
import { FillsTable } from "../components/FillsTable";
import { DirectionBadge, MarketBadge } from "../components/Badge";
import { Gauge } from "../components/Gauge";
import { Loading, ErrorBlock } from "../components/StateBlock";
import { fmtPnlWithUsd, fmtNum, fmtDuration, fmtTime, pnlClass } from "../lib/format";

export function PositionDetail() {
  const { id } = useParams();
  const pid = Number(id);
  const nav = useNavigate();
  const { data: p, error, loading } = useApi(() => api.position(pid), [pid]);

  if (loading) return <Loading />;
  if (error) return <ErrorBlock error={error} />;
  if (!p) return null;

  return (
    <div className="space-y-lg">
      <button
        onClick={() => nav(-1)}
        className="flex items-center gap-1 text-on-surface-variant hover:text-primary"
      >
        <span className="material-symbols-outlined">arrow_back</span>
        <span className="text-data-mono">Back</span>
      </button>


      {/* Summary Card */}
      <GlassCard className="flex flex-col gap-md md:flex-row md:justify-between">
        <div className="w-full space-y-2 md:w-1/2">
          <div className="flex items-center gap-2">
            <h2 className="font-sans text-headline-md font-bold">{p.symbol}</h2>
            <DirectionBadge direction={p.direction} />
            <MarketBadge market={p.market} />
          </div>
          <Row label="Open" value={`${fmtNum(p.avg_entry, 4)} @ ${fmtTime(p.open_time)}`} />
          <Row
            label="Close"
            value={p.close_time ? `${fmtNum(p.avg_exit, 4)} @ ${fmtTime(p.close_time)}` : "Open"}
          />
          <Row label="Qty" value={fmtNum(p.qty, 6)} />
          <Row label="Fills" value={String(p.num_fills)} />
        </div>
        <div className="flex w-full flex-col justify-end gap-2 border-t border-white/10 pt-md md:w-1/2 md:border-l md:border-t-0 md:pl-lg md:pt-0">
          <Row label="Hold Time" value={fmtDuration(p.hold_ms)} />
          <Row label="Fees" value={p.fees ? `${fmtNum(p.fees, 6)} ${p.fee_asset ?? ""}` : "—"} />
          <Row label="Total Funding" value={p.funding === null ? "N/A" : fmtNum(p.funding, 6)} />
          {p.close_price_usd && (
            <Row label="Close Price USD" value={`$${fmtNum(p.close_price_usd, 2)}`} />
          )}
          <div className="flex items-center justify-between">
            <span className="font-sans text-headline-md font-bold">Realised PNL</span>
            <span
              className={`font-mono text-headline-md ${pnlClass(p.realized_pnl)}`}
              title={
                p.pnl_asset && p.pnl_asset !== "USDT" && p.realized_pnl_usd != null
                  ? "USD 以现价估算，非成交当时价"
                  : undefined
              }
            >
              {fmtPnlWithUsd(p.realized_pnl, p.pnl_asset || "USDT", p.realized_pnl_usd)}
            </span>
          </div>
        </div>
      </GlassCard>

      {/* Fills Table (Full Width) */}
      <GlassCard className="!p-0 overflow-hidden">
        <div className="px-lg py-md">
          <span className="text-label-caps uppercase text-on-surface-variant">Trade Fills</span>
        </div>
        <div className="border-t border-white/[0.06] px-lg py-md">
          <FillsTable fills={p.fills || []} pnl_asset={p.pnl_asset ?? undefined} />
        </div>
      </GlassCard>

      {/* MAE/MFE Timeline */}
      {/* Metrics: 2-Column Grid */}
      <div className="grid grid-cols-1 gap-lg md:grid-cols-2">
        {/* Entry Quality Gauge */}
        <Gauge label="Entry Quality" value={p.entry_quality} />

        {/* Opportunity Capture Gauge */}
        <Gauge label="Opportunity Capture" value={p.opportunity_capture} />
      </div>

      {p.metrics_error && (
        <div className="text-data-mono text-bearish text-xs px-lg">
          指标计算失败：{p.metrics_error}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between font-mono text-data-mono">
      <span className="text-on-surface-variant">{label}</span>
      <span className="text-on-surface">{value}</span>
    </div>
  );
}

