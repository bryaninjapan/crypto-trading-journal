import { Balance } from "../../api/types";
import { PlotlyChart } from "./PlotlyChart";
import { fmtNum } from "../../lib/format";

export function BalanceDonut({ balances }: { balances: Balance[] }) {
  // 過濾有效餘額 + 按大小排序
  const valid = balances
    .filter((b) => b.balance && b.balance > 0)
    .sort((a, b) => (b.balance ?? 0) - (a.balance ?? 0));

  if (!valid.length) {
    return <div className="text-data-mono text-on-surface-variant">No balances</div>;
  }

  // 前 4 大，其餘歸入 Others
  const topN = 4;
  const top = valid.slice(0, topN);
  const others = valid.slice(topN);
  const otherSum = others.reduce((s, b) => s + (b.balance ?? 0), 0);

  const labels = [...top.map((b) => b.asset), ...(otherSum > 0 ? ["Others"] : [])];
  const values = [...top.map((b) => b.balance ?? 0), ...(otherSum > 0 ? [otherSum] : [])];
  const total = values.reduce((s, v) => s + v, 0);

  // 配色循環
  const colors = ["#c0c1ff", "#7bd0ff", "#ddb7ff", "#10B981", "#EF4444"];

  return (
    <div className="space-y-2">
      <PlotlyChart
        height={180}
        data={[
          {
            values,
            labels,
            type: "pie",
            hole: 0.65,
            marker: { colors: colors.slice(0, labels.length) },
            textinfo: "none",
            hovertemplate: "<b>%{label}</b><br>%{value:.6g}<extra></extra>",
          } as any,
        ]}
        layout={{
          showlegend: true,
          legend: { orientation: "v", x: 1.02, y: 1, xanchor: "left", yanchor: "top" },
          annotations: [
            {
              text: `${fmtNum(total, 6)}`,
              font: { size: 18, color: "#e0e2ef" },
              showarrow: false,
            },
          ],
        }}
      />
    </div>
  );
}
