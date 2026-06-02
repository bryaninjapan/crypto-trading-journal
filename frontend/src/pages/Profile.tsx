import { api } from "../api/client";
import { useApi } from "../lib/useApi";
import { GlassCard } from "../components/GlassCard";
import { fmtTime } from "../lib/format";

// Profile：账户信息 + 数据新鲜度 + 同步状态（grill 决策 #11，无 Stitch 设计稿，自建）。
export function Profile() {
  const { data } = useApi(() => api.summary(), []);
  const latest = data?.balances?.length
    ? Math.max(...data.balances.map(() => Date.now())) // 占位：余额快照时间由后端补充
    : null;

  return (
    <div className="space-y-lg">
      <h1 className="font-sans text-headline-md font-bold">Profile</h1>

      <GlassCard className="space-y-2">
        <span className="text-label-caps uppercase text-on-surface-variant">Account</span>
        <Row label="Exchange" value="Binance" />
        <Row label="Markets" value="Spot · USD-M · COIN-M" />
        <Row label="Consolidated" value="仅 Binance（Flipster 延后）" />
      </GlassCard>

      <GlassCard className="space-y-2">
        <span className="text-label-caps uppercase text-on-surface-variant">Data Freshness</span>
        <Row label="Balance Snapshot" value={latest ? fmtTime(latest) : "无"} />
        <Row label="Positions" value={`${data?.kpi.total_positions ?? 0} 个`} />
      </GlassCard>

      <GlassCard className="space-y-2">
        <span className="text-label-caps uppercase text-on-surface-variant">Default Filters</span>
        <p className="text-data-mono text-on-surface-variant">
          统计页默认 USD-M（USDT 可比）。COIN-M 为币本位，不跨 symbol 合并。
          现货盈亏为 FIFO 估算（标注 EST）。
        </p>
      </GlassCard>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-b border-white/[0.06] py-2 font-mono text-data-mono last:border-0">
      <span className="text-on-surface-variant">{label}</span>
      <span className="text-on-surface">{value}</span>
    </div>
  );
}
