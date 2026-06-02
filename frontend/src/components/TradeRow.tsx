import { useNavigate } from "react-router-dom";
import { Position } from "../api/types";
import { DirectionBadge } from "./Badge";
import { fmtNum, fmtPnl, fmtTime, fmtDuration, pnlClass } from "../lib/format";

export function TradeRow({ position, rowNum }: { position: Position; rowNum: number }) {
  const nav = useNavigate();
  return (
    <tr
      onClick={() => nav(`/positions/${position.id}`)}
      className="cursor-pointer border-b border-white/[0.06] hover:bg-surface-container/50"
    >
      {/* Row number */}
      <td className="px-lg py-2 text-data-mono text-xs text-on-surface-variant">#{rowNum}</td>

      {/* Symbol + Direction */}
      <td className="px-2 py-2">
        <div className="flex items-center gap-2">
          <DirectionBadge direction={position.direction} />
          <span className="font-sans text-body-bold font-semibold text-on-surface">
            {position.symbol}
          </span>
        </div>
      </td>

      {/* Open Price @ Time */}
      <td className="px-2 py-2 text-data-mono text-xs">
        <div className="text-on-surface">{fmtNum(position.avg_entry, 6)}</div>
        <div className="text-on-surface-variant text-[10px]">{fmtTime(position.open_time)}</div>
      </td>

      {/* Hold Duration */}
      <td className="px-2 py-2 text-data-mono text-xs text-on-surface">
        {fmtDuration(position.hold_ms)}
      </td>

      {/* Close Price @ Time */}
      <td className="px-2 py-2 text-data-mono text-xs">
        {position.close_time ? (
          <>
            <div className="text-on-surface">{fmtNum(position.avg_exit, 6)}</div>
            <div className="text-on-surface-variant text-[10px]">{fmtTime(position.close_time)}</div>
          </>
        ) : (
          <div className="text-on-surface-variant">—</div>
        )}
      </td>

      {/* Realized PNL */}
      <td className={`px-lg py-2 text-right text-data-mono text-xs font-semibold ${pnlClass(position.realized_pnl)}`}>
        {fmtPnl(position.realized_pnl, position.pnl_asset || "USDT")}
      </td>
    </tr>
  );
}
