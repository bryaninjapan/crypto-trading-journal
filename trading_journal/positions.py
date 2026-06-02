#!/usr/bin/env python3
"""
positions.py — 把逐笔 fill 聚合成「持仓」（净持仓归零边界模型）

模型（grill 决策 #3）：
  按 (exchange, market, symbol, direction) 时间序遍历 fill，维护带符号净仓位 net_qty。
  - net_qty 从 0 离开 = 开仓；回到 0 = 平仓；中间加减仓算同一持仓。
  - 合约：区间内 realizedPnl 累加 = 该持仓总盈亏（开仓 fill 的 realizedPnl 为 0）。
  - 现货：无 realizedPnl，区间内 FIFO 配对估算（is_estimated=True）。
  - 符号翻转（一笔 fill 反手）：在归零点切分，旧持仓平仓、残量开新持仓。

产出 position 字段见下方 POSITION_COLUMNS。MAE/MFE/gauges 不在此计算
（grill #4：按需拉 K 线时懒计算并回写），此处留空。

可作为库（build_positions）或 CLI（python -m trading_journal.positions 重建 positions 表）使用。
"""
import time

try:                       # 包模式：python -m trading_journal.positions
    from .common import EXCHANGE, get_conn
except ImportError:        # 目录内脚本模式：python positions.py
    from common import EXCHANGE, get_conn

# ─── 持仓表 schema ─────────────────────────────────────────────────────────────
POSITION_COLUMNS = [
    "exchange", "market", "symbol", "direction",
    "open_trade_id", "close_trade_id", "open_time", "close_time", "hold_ms",
    "qty", "avg_entry", "avg_exit",
    "realized_pnl", "pnl_asset", "is_estimated",
    "fees", "fee_asset", "funding", "num_fills",
    "close_price_usd",
]

DDL = """
CREATE TABLE IF NOT EXISTS positions (
  id                   SERIAL PRIMARY KEY,
  exchange             TEXT NOT NULL,
  market               TEXT NOT NULL,
  symbol               TEXT NOT NULL,
  direction            TEXT NOT NULL,            -- Long | Short
  open_trade_id        BIGINT NOT NULL,          -- 首笔 fill 的 trade_id（稳定锚）
  close_trade_id       BIGINT,                   -- 末笔（平仓）fill
  open_time            BIGINT NOT NULL,
  close_time           BIGINT,                   -- NULL = 仍持仓
  hold_ms              BIGINT,
  qty                  DOUBLE PRECISION,         -- 累计开仓量（base）
  avg_entry            DOUBLE PRECISION,
  avg_exit             DOUBLE PRECISION,
  realized_pnl         DOUBLE PRECISION,         -- 合约真实 / 现货 FIFO 估算
  pnl_asset            TEXT,                     -- USDT | BTC | ADA ...
  is_estimated         BOOLEAN DEFAULT FALSE,    -- 现货估算为 TRUE
  fees                 DOUBLE PRECISION,
  fee_asset            TEXT,
  funding              DOUBLE PRECISION,         -- 持仓窗口内 FUNDING_FEE 求和
  num_fills            INT,
  close_price_usd      DOUBLE PRECISION,         -- Coin-M 平仓时的 USD 价格
  -- 懒计算（详情页首次打开时按 K 线回填）
  mae                  DOUBLE PRECISION,
  mfe                  DOUBLE PRECISION,
  entry_quality        DOUBLE PRECISION,
  opportunity_capture  DOUBLE PRECISION,
  metrics_at           TIMESTAMPTZ,              -- NULL = MAE/MFE 未算
  built_at             TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (exchange, market, symbol, direction, open_trade_id)
);
CREATE INDEX IF NOT EXISTS idx_positions_lookup ON positions (exchange, market, symbol);
CREATE INDEX IF NOT EXISTS idx_positions_close ON positions (close_time);
CREATE INDEX IF NOT EXISTS idx_positions_open ON positions (open_time);
"""

EPS = 1e-9


# ─── 取 fill ───────────────────────────────────────────────────────────────────
def fetch_fills(conn, market=None, symbol=None):
    """按时间升序取 fill。返回 dict 列表。"""
    sql = (
        "SELECT market, symbol, trade_id, side, price, qty_base, quote_qty, "
        "realized_pnl, margin_asset, position_side, fee, fee_asset, trade_time "
        "FROM trades WHERE exchange=%s"
    )
    args = [EXCHANGE]
    if market:
        sql += " AND market=%s"
        args.append(market)
    if symbol:
        sql += " AND symbol=%s"
        args.append(symbol)
    sql += " ORDER BY symbol, trade_time ASC, trade_id ASC"
    with conn.cursor() as cur:
        cur.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_funding_map(conn):
    """
    返回 {(market, symbol): [(time_ms, amount), ...]} 供按持仓窗口求和。
    funding 表不存在时返回空 dict（Total Funding 显示 N/A）。
    """
    fmap = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('public.funding') IS NOT NULL"
        )
        if not cur.fetchone()[0]:
            return fmap
        cur.execute(
            "SELECT market, symbol, income_time, amount FROM funding "
            "WHERE exchange=%s ORDER BY income_time ASC",
            (EXCHANGE,),
        )
        for market, symbol, t, amt in cur.fetchall():
            fmap.setdefault((market, symbol), []).append((int(t), float(amt or 0)))
    return fmap


# ─── 单 bucket 聚合 ────────────────────────────────────────────────────────────
def _bucket_key(f):
    """
    合约 hedge 模式按 position_side 分桶（LONG/SHORT 独立持仓）；
    单向模式(BOTH) 与现货按 symbol 整体净额分桶。
    """
    ps = (f.get("position_side") or "BOTH").upper()
    if ps in ("LONG", "SHORT"):
        return (f["market"], f["symbol"], ps)
    return (f["market"], f["symbol"], "NET")


def _signed(f):
    """开仓方向带符号量：BUY +、SELL −（base 量）。"""
    qty = float(f["qty_base"] or 0)
    return qty if f["side"] == "BUY" else -qty


def _pnl_asset(market, f):
    if market == "usdm":
        return f.get("margin_asset") or "USDT"
    if market == "coinm":
        return f.get("margin_asset")          # 币本位：BTC / ADA ...
    return "USDT"                              # 现货估算口径


def _finalize(market, symbol, direction, fills, net_reached_zero):
    """把一段同持仓的 fill 列表收成一个 position dict。"""
    entry_side = "BUY" if direction == "Long" else "SELL"
    entry_qty = entry_px = 0.0
    exit_qty = exit_px = 0.0
    fees = 0.0
    fee_asset = None
    realized = 0.0
    has_realized = False
    for f in fills:
        qb = float(f["qty_base"] or 0)
        px = float(f["price"] or 0)
        if f["side"] == entry_side:
            entry_qty += qb
            entry_px += px * qb
        else:
            exit_qty += qb
            exit_px += px * qb
        if f.get("fee") is not None:
            fees += float(f["fee"])
            fee_asset = fee_asset or f.get("fee_asset")
        if f.get("realized_pnl") is not None:
            realized += float(f["realized_pnl"])
            has_realized = True

    open_t = int(fills[0]["trade_time"])
    close_t = int(fills[-1]["trade_time"]) if net_reached_zero else None
    # 未平仓持仓的 realized_pnl 应为 0（仅平仓后计算）
    final_pnl = realized if (has_realized and net_reached_zero) else (0.0 if has_realized else None)
    return {
        "exchange": EXCHANGE,
        "market": market,
        "symbol": symbol,
        "direction": direction,
        "open_trade_id": int(fills[0]["trade_id"]),
        "close_trade_id": int(fills[-1]["trade_id"]) if net_reached_zero else None,
        "open_time": open_t,
        "close_time": close_t,
        "hold_ms": (close_t - open_t) if close_t else None,
        "qty": entry_qty,
        "avg_entry": (entry_px / entry_qty) if entry_qty > EPS else None,
        "avg_exit": (exit_px / exit_qty) if exit_qty > EPS else None,
        # 合约用交易所 realizedPnl；现货（无 realizedPnl）后续由 _spot_fifo 覆盖
        "realized_pnl": final_pnl,
        "pnl_asset": _pnl_asset(market, fills[0]),
        "is_estimated": market == "spot",
        "fees": fees,
        "fee_asset": fee_asset,
        "funding": None,                       # 后填
        "num_fills": len(fills),
        "_fills": fills,                       # 临时：供 spot FIFO / funding 用，入库前剔除
    }


def _spot_fifo_pnl(fills):
    """现货持仓（净额归零的完整区间）FIFO 估算已实现盈亏(USDT，已扣手续费)。"""
    lots = []   # [{price, qty, fee}]
    pnl = 0.0
    for f in fills:
        qty = float(f["qty_base"] or 0)
        price = float(f["price"] or 0)
        fee = float(f["fee"] or 0)
        if f["side"] == "BUY":
            lots.append({"price": price, "qty": qty, "fee": fee})
        else:
            remain = qty
            cost = 0.0
            buy_fee = 0.0
            while remain > EPS and lots:
                lot = lots[0]
                take = min(remain, lot["qty"])
                cost += take * lot["price"]
                buy_fee += lot["fee"] * (take / lot["qty"]) if lot["qty"] else 0.0
                lot["qty"] -= take
                remain -= take
                if lot["qty"] <= EPS:
                    lots.pop(0)
            proceeds = (qty - remain) * price
            pnl += proceeds - cost - fee - buy_fee
    return pnl


def build_positions(fills, funding_map=None):
    """
    fills: 已按 (symbol, trade_time, trade_id) 升序的 fill dict 列表。
    返回 position dict 列表（含未平仓持仓，close_time=None）。
    """
    funding_map = funding_map or {}
    # 分桶
    buckets = {}
    for f in fills:
        buckets.setdefault(_bucket_key(f), []).append(f)

    positions = []
    for (market, symbol, _ps), bfills in buckets.items():
        net = 0.0
        cur_fills = []
        for f in bfills:
            s = _signed(f)
            if abs(net) <= EPS and not cur_fills:
                direction = "Long" if s > 0 else "Short"
            prev = net
            net += s
            # 符号翻转：在归零点切分（旧持仓平掉，残量开新仓）
            if cur_fills and prev * net < -EPS:
                # 翻转 fill 不属于旧持仓，只作为新持仓起点（避免重复计算 realized_pnl）
                positions.append(_finalize(market, symbol, direction, cur_fills, True))
                cur_fills = [f]                # 翻转 fill 作为新持仓的起点
                direction = "Long" if net > 0 else "Short"
                continue
            cur_fills.append(f)
            if abs(net) <= EPS:                # 归零 → 平仓
                positions.append(_finalize(market, symbol, direction, cur_fills, True))
                cur_fills = []
                net = 0.0
        if cur_fills:                          # 仍持仓
            positions.append(_finalize(market, symbol, direction, cur_fills, False))

    # 现货 FIFO 估算 + funding 窗口求和，然后剔除临时 _fills
    for p in positions:
        fl = p.pop("_fills")
        if p["market"] == "spot":
            p["realized_pnl"] = _spot_fifo_pnl(fl)
        series = funding_map.get((p["market"], p["symbol"]))
        if series and p["open_time"] is not None:
            hi = p["close_time"] if p["close_time"] is not None else int(time.time() * 1000)
            p["funding"] = sum(a for t, a in series if p["open_time"] <= t <= hi)
    return positions


# ─── 入库 ──────────────────────────────────────────────────────────────────────
def upsert_positions(conn, positions):
    """ON CONFLICT(exchange,market,symbol,direction,open_trade_id) DO UPDATE。
    未平仓持仓后续可能新增 fill，故 UPDATE 覆盖；已平仓的稳定。
    若聚合结果改变（如平仓），不重算 MAE/MFE（保留懒计算缓存）。"""
    if not positions:
        return 0
    from psycopg2.extras import execute_values
    cols = POSITION_COLUMNS
    update_set = ",".join(
        f"{c}=EXCLUDED.{c}" for c in cols
        if c not in ("exchange", "market", "symbol", "direction", "open_trade_id")
    )
    sql = (
        f"INSERT INTO positions ({','.join(cols)}) VALUES %s "
        "ON CONFLICT (exchange, market, symbol, direction, open_trade_id) "
        f"DO UPDATE SET {update_set}, built_at=NOW()"
    )
    rows = [tuple(p[c] for c in cols) for p in positions]
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
        n = cur.rowcount
    conn.commit()
    return n


def rebuild(conn, market=None):
    """从 trades 全量重建 positions（幂等）。返回写入数。"""
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()
    fills = fetch_fills(conn, market=market)
    funding_map = fetch_funding_map(conn)
    positions = build_positions(fills, funding_map)
    return upsert_positions(conn, positions), len(positions)


def main():
    from dotenv import load_dotenv
    load_dotenv()
    conn = get_conn()
    try:
        written, total = rebuild(conn)
        print(f"positions 重建完成：聚合 {total} 个持仓，写入/更新 {written} 行")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
