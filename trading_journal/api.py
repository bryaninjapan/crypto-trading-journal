#!/usr/bin/env python3
"""
api.py — Trading Journal JSON API + SPA 托管（前端重做后的新生产 app）

- 所有数据来自 positions / balances / funding / trades 表。
- 端点见下方路由；统计页默认 usdm（USDT，可比），coinm 币本位单独看。
- 认证：HTTP Basic（同源，沿用 DASHBOARD_USER/PASS）。
- 生产：FastAPI 同时托管 frontend 打包产物（static/，html=True）。

启动：
  uvicorn trading_journal.api:app --host 0.0.0.0 --port 8000
"""
import os
import time
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .common import get_conn
from . import klines as K

load_dotenv()
app = FastAPI(title="Trading Journal API")
security = HTTPBasic()
EXCHANGE = "binance"


# ─── 认证 ──────────────────────────────────────────────────────────────────────
def auth(cred: HTTPBasicCredentials = Depends(security)):
    u_ok = secrets.compare_digest(cred.username, os.getenv("DASHBOARD_USER", ""))
    p_ok = secrets.compare_digest(cred.password, os.getenv("DASHBOARD_PASS", ""))
    if not (u_ok and p_ok):
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})
    return cred


# ─── DB helper ─────────────────────────────────────────────────────────────────
def rows(sql, args=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def cumsum(series):
    acc = 0.0
    out = []
    for x in series:
        acc += x
        out.append(round(acc, 8))
    return out


# ─── Position Aggregation (fills → positions) ─────────────────────────────────
def _build_one_position(market, symbol, pos_side, fills):
    """把同一 position cycle 的 fills 組成一個 position dict。"""
    if pos_side == "SHORT":
        open_fills  = [f for f in fills if f.get("side") == "SELL"]
        close_fills = [f for f in fills if f.get("side") == "BUY"]
    else:  # LONG
        open_fills  = [f for f in fills if f.get("side") == "BUY"]
        close_fills = [f for f in fills if f.get("side") == "SELL"]

    # 若全是同一 side（資料異常），fallback
    if not open_fills:
        open_fills = fills
    open_fills  = sorted(open_fills,  key=lambda x: x.get("trade_time", 0))
    close_fills = sorted(close_fills, key=lambda x: x.get("trade_time", 0))

    open_time  = int(open_fills[0].get("trade_time", 0))
    close_time = int(close_fills[-1].get("trade_time", 0)) if close_fills else None

    open_qty  = sum(float(f.get("qty_base", 0)) for f in open_fills)
    close_qty = sum(float(f.get("qty_base", 0)) for f in close_fills)

    avg_entry = (
        sum(float(f.get("price", 0)) * float(f.get("qty_base", 0)) for f in open_fills) / open_qty
        if open_qty > 0 else 0.0
    )
    avg_exit = (
        sum(float(f.get("price", 0)) * float(f.get("qty_base", 0)) for f in close_fills) / close_qty
        if close_qty > 0 else None
    )

    realized_pnl = sum(float(f.get("realized_pnl", 0)) for f in fills)
    fees         = sum(float(f.get("fee", 0) or 0) for f in fills)
    hold_ms      = (close_time - open_time) if close_time else None

    pnl_asset = fills[0].get("margin_asset") or "USDT"
    fee_asset = fills[0].get("fee_asset") or pnl_asset
    pos_id    = fills[0].get("trade_id")  # 以第一筆 fill 的 trade_id 作為 position ID

    return {
        "id":            pos_id,
        "market":        market,
        "symbol":        symbol,
        "direction":     "Short" if pos_side == "SHORT" else "Long",
        "open_trade_id": pos_id,
        "open_time":     open_time,
        "close_time":    close_time,
        "hold_ms":       hold_ms,
        "qty":           round(open_qty, 8),
        "avg_entry":     round(avg_entry, 6),
        "avg_exit":      round(avg_exit, 6) if avg_exit is not None else None,
        "realized_pnl":  round(realized_pnl, 6),
        "pnl_asset":     pnl_asset,
        "is_estimated":  False,
        "fees":          round(fees, 8),
        "fee_asset":     fee_asset,
        "funding":       0.0,
        "num_fills":     len(fills),
    }


def build_positions_from_fills(fills):
    """
    將 fills 按 (market, symbol) 分組，
    再用最簡單的狀態機：累積 qty 為 0 時即為 cycle 邊界。

    核心算法：
    - BUY = +qty, SELL = -qty
    - 累積 cum_qty，當 cum_qty 接近 0（|cum_qty| < tolerance）時，cycle 完成
    - 即使 cum_qty 跨越 0（短暫變反向），也在通過 0 時截斷
    """
    from collections import defaultdict

    groups = defaultdict(list)
    for f in fills:
        if not f.get("position_side"):
            continue
        key = (f.get("market"), f.get("symbol"))
        groups[key].append(f)

    positions = []
    TOLERANCE = 1e-3  # cum_qty 接近 0 的閾值（0.001 = tolerance for micro-positions）

    for (market, symbol), group_fills in groups.items():
        # 複合排序鍵：trade_time 為主，id（trades 表單調主鍵）為 tie-breaker。
        # COIN-M 有大量同毫秒 fill（單一 trade_time 多達 20 筆），只按 trade_time
        # 排序時 Python 穩定排序會保留輸入的物理順序——而不同 SQL 查詢回傳的物理
        # 順序不同，導致狀態機算出不同的 cycle 邊界、position 數飄移。加上 id 後
        # 排序完全確定，與輸入順序無關。
        group_fills = sorted(group_fills, key=lambda x: (x.get("trade_time", 0), x.get("id", 0)))

        cycle_fills = []
        cum_qty = 0.0

        for f in group_fills:
            side = f.get("side")
            qty = float(f.get("qty_base", 0))
            delta = qty if side == "BUY" else -qty

            prev_cum = cum_qty
            cum_qty += delta

            # 檢測：(1) cum_qty 跨越 0，或 (2) cum_qty 回到平衡狀態
            crossed_zero = (prev_cum * cum_qty < 0)  # 符號改變 = 跨越 0
            is_balanced = (abs(cum_qty) < TOLERANCE)

            # 總是先加入當前 fill
            cycle_fills.append(f)

            # 然後檢測是否要結束 cycle（當前 fill 已包含）
            if (crossed_zero or is_balanced) and cycle_fills:
                # 結束當前 cycle
                if cycle_fills[0].get("side") == "BUY":
                    inferred_pos_side = "LONG"
                else:
                    inferred_pos_side = "SHORT"
                positions.append(_build_one_position(market, symbol, inferred_pos_side, cycle_fills))
                cycle_fills = []
                # 如果平衡，重置狀態
                if is_balanced:
                    cum_qty = 0.0

        # 殘餘
        if cycle_fills:
            if cycle_fills[0].get("side") == "BUY":
                inferred_pos_side = "LONG"
            else:
                inferred_pos_side = "SHORT"
            positions.append(_build_one_position(market, symbol, inferred_pos_side, cycle_fills))

    return positions


# ─── /api/summary ──────────────────────────────────────────────────────────────
@app.get("/api/summary", dependencies=[Depends(auth)])
def summary():
    # 从数据库查询真实数据
    trades = rows("SELECT * FROM trades ORDER BY trade_time ASC, id ASC")

    # 用聚合後 positions 計算 total_positions 和 win_rate（Step 5 fix）
    futures_fills = [t for t in trades if t.get("market") in ("usdm", "coinm")]
    agg_positions = build_positions_from_fills(futures_fills)
    # 只計已平倉 position（與 Journal 的 status=closed 口徑一致）；
    # 未平倉部位不計入 Dashboard 的 Total Trades。
    total_positions = len([p for p in agg_positions if p.get("close_time") is not None])

    # 勝率：以聚合 position 的總 PNL 正負計算。
    # 注：勝負只看「正負號」，而 USD 換算是乘以正數現價，不改變符號，
    #     故 COIN-M 的計價單位混亂不影響勝率分類，這裡無需換算。
    pnl_positions = [p for p in agg_positions if p.get("realized_pnl", 0) != 0]
    if pnl_positions:
        wins = len([p for p in pnl_positions if p["realized_pnl"] > 0])
        win_rate = (wins / len(pnl_positions)) * 100
    else:
        win_rate = 0.0

    # ── Binance 现价：用于 COIN-M PNL → USD 换算 + 余额 USD 估值 ──
    # COIN-M（币本位）的 realized_pnl 以「标的币」计价，不是 USD：
    #   ADAUSD_PERP → realized_pnl 是 ADA 颗数，BTCUSD_PERP → BTC 数量；
    #   USDM 则已是 USDT。三种单位不能直接相加（否则 -432 = ADA颗数+BTC+USDT，
    #   没有物理意义）。必须先把 COIN-M 用标的币现价换算成 USD 再累加。
    # 注：用「现价」近似换算，非成交当时价。精确版需按 trade_time 取历史 kline，
    #     留作后续迭代（见 decision-log）。
    from binance.client import Client
    prices = {}
    client = None
    try:
        client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET"))
        prices = {t["symbol"]: float(t["price"]) for t in client.get_all_tickers()}
    except Exception as e:
        print(f"[warning] 无法从 Binance 获取价格: {e}")

    def to_usd(asset: str, amount: float) -> float | None:
        if asset == "USDT":
            return round(amount, 2)
        pair = f"{asset}USDT"
        if pair in prices:
            return round(amount * prices[pair], 2)
        return None

    def realized_pnl_usd(trade) -> float | None:
        """单笔期货 fill 的 realized_pnl 换算成 USD。
        usdm  → 已是 USDT，原样返回。
        coinm → 以标的币计价（margin_asset，如 ADA/BTC），乘现价换算。
                缺现价时返回 None（宁可排除该笔，也不把币本位数量混进 USD 合计）。"""
        pnl = float(trade.get("realized_pnl") or 0)
        market = trade.get("market")
        if market == "usdm":
            return pnl
        if market == "coinm":
            coin = trade.get("margin_asset") or trade.get("symbol", "").replace("USD_PERP", "")
            price = prices.get(f"{coin}USDT") if coin else None
            return pnl * price if price is not None else None
        return None

    # ── 期货 PNL 合计 + 权益曲线（均换算为 USD 后累加）──
    futures_pnl = 0.0
    equity_curve = []
    cum_pnl = 0.0
    skipped = 0
    for trade in trades:
        if trade.get("market") not in ("usdm", "coinm"):
            continue
        usd = realized_pnl_usd(trade)
        if usd is None:
            skipped += 1
            continue
        futures_pnl += usd
        if usd == 0:
            continue  # 跳过开仓 fill（PNL=0），不画点
        cum_pnl += usd
        equity_curve.append({
            "t": int(trade.get("trade_time", 0)),
            "cum": round(cum_pnl, 4),
        })
    if skipped:
        print(f"[warning] {skipped} 笔 COIN-M PNL 因缺现价无法换算 USD，已从核心数字排除")

    # ── 从 Binance 查询真实余额（复用上面的 client / prices / to_usd）──
    balances = []
    if client is not None:
        try:
            # USDM（灰尘过滤）
            for b in client.futures_account_balance():
                balance = float(b.get("balance", 0))
                if balance > 0.001:
                    asset = b["asset"]
                    balances.append({
                        "market": "usdm",
                        "asset": asset,
                        "free": balance,
                        "locked": 0.0,
                        "balance": balance,
                        "usd_value": to_usd(asset, balance),
                    })
            # COINM（灰尘过滤）
            for b in client.futures_coin_account_balance():
                balance = float(b.get("balance", 0))
                if balance > 0.0001:
                    asset = b["asset"]
                    balances.append({
                        "market": "coinm",
                        "asset": asset,
                        "free": balance,
                        "locked": 0.0,
                        "balance": balance,
                        "usd_value": to_usd(asset, balance),
                    })
        except Exception as e:
            print(f"[warning] 无法从 Binance 查询余额: {e}")
            balances = []

    return {
        "balances": balances,
        "kpi": {
            "total_positions": total_positions,
            "win_rate": round(win_rate, 2),
            "futures_realized_pnl": round(futures_pnl, 2),
            "pnl_asset": "USDT",
        },
        "equity_curve": equity_curve,
    }


# ─── /api/positions ────────────────────────────────────────────────────────────
@app.get("/api/positions", dependencies=[Depends(auth)])
def list_positions(
    market: str = Query(None, pattern="^(usdm|coinm)$"),
    symbol: str = None,
    direction: str = Query(None, pattern="^(Long|Short)$"),
    status: str = Query(None, pattern="^(open|closed)$"),
    start: int = None,
    end: int = None,
    sort: str = Query("open_time", pattern="^(open_time|symbol|realized_pnl)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # Step 5 fix: 聚合 fills → positions
    sql = "SELECT * FROM trades WHERE market IN ('usdm','coinm') ORDER BY trade_time ASC, id ASC"
    all_fills = rows(sql)
    all_positions = build_positions_from_fills(all_fills)

    # 過濾
    if market:
        all_positions = [p for p in all_positions if p["market"] == market]
    if symbol:
        all_positions = [p for p in all_positions if p["symbol"] == symbol]
    if direction:
        all_positions = [p for p in all_positions if p["direction"] == direction]
    if status == "closed":
        all_positions = [p for p in all_positions if p.get("close_time") is not None]
    elif status == "open":
        all_positions = [p for p in all_positions if p.get("close_time") is None]

    # 排序
    sort_key = {"open_time": "open_time", "symbol": "symbol", "realized_pnl": "realized_pnl"}.get(sort, "open_time")
    reverse = (order == "desc")
    all_positions.sort(key=lambda x: (x.get(sort_key) or 0), reverse=reverse)

    total = len(all_positions)
    data  = all_positions[offset:offset + limit]

    return {"total": total, "limit": limit, "offset": offset, "positions": data}


# ─── /api/positions/{trade_id} ───────────────────────────────────────────────────
@app.get("/api/positions/{trade_id}", dependencies=[Depends(auth)])
def position_detail(trade_id: int):
    # 找到 fill 所屬的 position cycle，回傳聚合結果 + 該 position 的 fills
    anchor = one("SELECT * FROM trades WHERE trade_id = %s", (trade_id,))
    if not anchor:
        raise HTTPException(status_code=404, detail="Position not found")

    # 取同一 (market, symbol) 的所有 fills（不按 position_side 篩選）
    same_symbol = rows(
        "SELECT * FROM trades WHERE market=%s AND symbol=%s AND position_side IS NOT NULL ORDER BY trade_time ASC, id ASC",
        (anchor["market"], anchor["symbol"]),
    )

    # 聚合並找出包含 trade_id 的那個 position
    positions = build_positions_from_fills(same_symbol)
    target = next((p for p in positions if p["id"] == trade_id), None)

    if not target:
        # fallback: 用 anchor fill 本身組成單筆
        target = _build_one_position(
            anchor["market"], anchor["symbol"],
            anchor["position_side"] or "LONG", [anchor]
        )
        target_fills = [anchor]
    else:
        # 重新聚合以找出這個 position 對應的 fills
        # 使用與 build_positions_from_fills 相同的狀態機邏輯
        from collections import defaultdict
        groups = defaultdict(list)
        for f in same_symbol:
            key = (f.get("market"), f.get("symbol"))
            groups[key].append(f)

        target_fills = []
        TOLERANCE = 1e-3  # 與 build_positions_from_fills 保持一致

        for (market, symbol), group_fills in groups.items():
            if market == anchor["market"] and symbol == anchor["symbol"]:
                # 與 build_positions_from_fills 一致的複合排序鍵（trade_time, id）
                group_fills = sorted(group_fills, key=lambda x: (x.get("trade_time", 0), x.get("id", 0)))

                # 重新執行狀態機（與 build_positions_from_fills 邏輯一致）
                cycle_fills = []
                cum_qty = 0.0

                for f in group_fills:
                    side = f.get("side")
                    qty = float(f.get("qty_base", 0))
                    delta = qty if side == "BUY" else -qty

                    prev_cum = cum_qty
                    cum_qty += delta

                    # 檢測：(1) cum_qty 跨越 0，或 (2) cum_qty 回到平衡狀態
                    crossed_zero = (prev_cum * cum_qty < 0)
                    is_balanced = (abs(cum_qty) < TOLERANCE)

                    # 總是先加入當前 fill
                    cycle_fills.append(f)

                    # 然後檢測是否要結束 cycle
                    if (crossed_zero or is_balanced) and cycle_fills:
                        # 結束當前 cycle，檢查是否包含 trade_id
                        if any(cf["trade_id"] == trade_id for cf in cycle_fills):
                            target_fills = cycle_fills
                            break
                        cycle_fills = []
                        # 如果平衡，重置狀態
                        if is_balanced:
                            cum_qty = 0.0

                # 如果還沒找到，檢查最後一個 cycle
                if not target_fills and cycle_fills:
                    if any(cf["trade_id"] == trade_id for cf in cycle_fills):
                        target_fills = cycle_fills

    # 如果仍無 fills，使用 target 的 num_fills 作為後備
    if not target_fills:
        target_fills = same_symbol[:target.get("num_fills", 1)]

    return {
        **target,
        "exchange": EXCHANGE,
        "fills": [
            {
                "trade_id":     f["trade_id"],
                "side":         f["side"],
                "price":        float(f["price"]),
                "qty_base":     float(f["qty_base"]),
                "realized_pnl": float(f["realized_pnl"] or 0),
                "fee":          float(f["fee"] or 0),
                "fee_asset":    f["fee_asset"],
                "trade_time":   int(f["trade_time"]),
            }
            for f in target_fills
        ],
    }


# ─── /api/positions/{id}/klines ──────────────────────────────────────────────────
@app.get("/api/positions/{pid}/klines", dependencies=[Depends(auth)])
def position_klines(pid: int, interval: str = Query("1h", pattern="^(15m|1h)$")):
    # MOCK 蠟燭圖數據
    import datetime
    import random
    random.seed(42 + pid)

    symbols_list = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT"]
    markets_list = ["usdm", "usdm", "usdm", "coinm", "spot"]

    sym_idx = (pid - 1) % len(symbols_list)
    market = markets_list[sym_idx]
    symbol = symbols_list[sym_idx]

    base_date = datetime.datetime(2026, 1, 1)
    open_ts = int((base_date + datetime.timedelta(days=(pid-1)//2)).timestamp() * 1000)
    close_ts = open_ts + random.randint(3600000, 432000000)

    # 根據 interval 設置蠟燭間隔
    if interval == "15m":
        candle_interval_ms = 15 * 60 * 1000  # 15 分鐘
    else:  # 1h
        candle_interval_ms = 60 * 60 * 1000  # 1 小時

    # 生成蠟燭圖數據
    candles = []
    price = round(random.uniform(1000, 50000), 2)
    ts = open_ts
    while ts <= close_ts:
        o = price
        c = price + round(random.uniform(-100, 100), 2)
        h = max(o, c) + abs(round(random.uniform(0, 200), 2))
        l = min(o, c) - abs(round(random.uniform(0, 200), 2))
        v = round(random.uniform(0.1, 10), 2)
        candles.append({
            "t": ts,
            "o": round(o, 2),
            "h": round(h, 2),
            "l": round(l, 2),
            "c": round(c, 2),
            "v": v,
        })
        price = c
        ts += candle_interval_ms  # 使用動態間隔

    avg_entry = round(random.uniform(1000, 50000), 2)
    avg_exit = round(random.uniform(1000, 50000), 2)

    return {
        "symbol": symbol,
        "market": market,
        "interval": interval,
        "candles": candles,
        "markers": {
            "entry": {"t": open_ts, "price": avg_entry},
            "exit": {"t": close_ts, "price": avg_exit},
        },
    }


@app.get("/api/positions/{pid}/mae-mfe-timeline", dependencies=[Depends(auth)])
def position_mae_mfe_timeline(pid: int):
    import datetime
    import random
    random.seed(42 + pid)

    base_date = datetime.datetime(2026, 1, 1)
    open_ts = int((base_date + datetime.timedelta(days=(pid-1)//2)).timestamp() * 1000)
    close_ts = open_ts + random.randint(3600000, 432000000)

    hold_duration = close_ts - open_ts
    num_points = min(50, max(10, hold_duration // (3600000)))  # 10-50 時間點

    timeline = []
    for i in range(num_points + 1):
        t = open_ts + int((hold_duration / num_points) * i)
        progress = i / num_points

        mae = -abs(round(random.uniform(0, 50) * progress, 2))
        mfe = round(random.uniform(0, 100) * progress, 2)

        timeline.append({
            "t": t,
            "mae": mae,
            "mfe": mfe,
        })

    return {"timeline": timeline}


# ─── /api/analytics ──────────────────────────────────────────────────────────────
@app.get("/api/analytics", dependencies=[Depends(auth)])
def analytics(market: str = Query("usdm", pattern="^(usdm|coinm)$")):
    # 严格按 market 过滤交易
    trades = rows("SELECT * FROM trades WHERE market = %s ORDER BY trade_time ASC, id ASC", (market,))

    total_trades = len(trades)
    total_pnl = sum(float(t.get("realized_pnl", 0)) for t in trades)

    # Step 2 fix: 按 position_side 分類（不是 side）
    # SHORT position: position_side=="SHORT"；opening fill=SELL, closing fill=BUY
    # LONG  position: position_side=="LONG"；opening fill=BUY,  closing fill=SELL
    long_trades  = [t for t in trades if t.get("position_side") == "LONG"]
    short_trades = [t for t in trades if t.get("position_side") == "SHORT"]
    # fallback（無 position_side 的舊資料）
    if not long_trades and not short_trades:
        long_trades  = [t for t in trades if t.get("side") == "BUY"]
        short_trades = [t for t in trades if t.get("side") == "SELL"]

    # 勝負統計：只看有 PNL 的 fill（closing fills）
    pnl_trades = [t for t in trades if float(t.get("realized_pnl", 0)) != 0]
    wins   = len([t for t in pnl_trades if float(t.get("realized_pnl", 0)) > 0])
    losses = len([t for t in pnl_trades if float(t.get("realized_pnl", 0)) < 0])
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

    # 多頭統計（按 position_side=LONG）
    long_pnl_trades  = [t for t in long_trades  if float(t.get("realized_pnl", 0)) != 0]
    long_pnl         = sum(float(t.get("realized_pnl", 0)) for t in long_trades)
    long_wins        = len([t for t in long_pnl_trades if float(t.get("realized_pnl", 0)) > 0])
    long_losses      = len([t for t in long_pnl_trades if float(t.get("realized_pnl", 0)) < 0])
    long_wins_pnl    = sum(float(t.get("realized_pnl", 0)) for t in long_pnl_trades if float(t.get("realized_pnl", 0)) > 0) or 0.01
    long_losses_pnl  = sum(float(t.get("realized_pnl", 0)) for t in long_pnl_trades if float(t.get("realized_pnl", 0)) < 0) or -0.01

    # 空頭統計（按 position_side=SHORT）
    short_pnl_trades = [t for t in short_trades if float(t.get("realized_pnl", 0)) != 0]
    short_pnl        = sum(float(t.get("realized_pnl", 0)) for t in short_trades)
    short_wins       = len([t for t in short_pnl_trades if float(t.get("realized_pnl", 0)) > 0])
    short_losses     = len([t for t in short_pnl_trades if float(t.get("realized_pnl", 0)) < 0])
    short_wins_pnl   = sum(float(t.get("realized_pnl", 0)) for t in short_pnl_trades if float(t.get("realized_pnl", 0)) > 0) or 0.01
    short_losses_pnl = sum(float(t.get("realized_pnl", 0)) for t in short_pnl_trades if float(t.get("realized_pnl", 0)) < 0) or -0.01

    # 平均持倉時間：從聚合後 positions 計算
    agg_positions = build_positions_from_fills(trades)
    closed_positions = [p for p in agg_positions if p.get("hold_ms") is not None]
    avg_hold_ms = int(sum(p["hold_ms"] for p in closed_positions) / len(closed_positions)) if closed_positions else 0

    # 最大連勝/連敗（按時間順序遍歷有 PNL 的 fill）
    max_consec_win = max_consec_loss = cur_win = cur_loss = 0
    for t in pnl_trades:
        if float(t.get("realized_pnl", 0)) > 0:
            cur_win += 1; cur_loss = 0
            max_consec_win = max(max_consec_win, cur_win)
        else:
            cur_loss += 1; cur_win = 0
            max_consec_loss = max(max_consec_loss, cur_loss)

    # 日均計算（以有交易的時間跨度為準）
    if trades:
        time_span_days = max(1, (max(int(t.get("trade_time", 0)) for t in trades) -
                                  min(int(t.get("trade_time", 0)) for t in trades)) / 86400000)
    else:
        time_span_days = 1

    # 成交量（USD notional）：USD-M 用 quote_qty；COIN-M 無 quote_qty，回退 price*qty_base
    def _notional(t):
        qq = t.get("quote_qty")
        if qq is not None:
            return abs(float(qq))
        return abs(float(t.get("price", 0)) * float(t.get("qty_base", 0)))

    total_trades_volume = sum(_notional(t) for t in trades)
    # 交易天數：以 distinct UTC 日期計（trade_time 為毫秒 epoch）
    trade_days = len({int(t.get("trade_time", 0)) // 86400000 for t in trades}) or 1
    avg_daily_volume = total_trades_volume / trade_days

    return {
        "market": market,
        "kpi": {
            "total_trades": len(agg_positions),
            "avg_hold_ms": avg_hold_ms,
            "win_rate": round(win_rate, 2),
            "longs": len([p for p in agg_positions if p["direction"] == "Long"]),
            "shorts": len([p for p in agg_positions if p["direction"] == "Short"]),
            "long_pct": round(
                len([p for p in agg_positions if p["direction"] == "Long"]) / len(agg_positions) * 100, 2
            ) if agg_positions else 0,
        },
        "statistics": {
            "total_gain_loss": round(total_pnl, 2),
            "trade_expectancy": round(total_pnl / len(agg_positions) if agg_positions else 0, 2),
            "avg_daily_gain": round(total_pnl / time_span_days, 2),
            "avg_daily_volume": round(avg_daily_volume, 2),
            "largest_gain": round(max((float(t.get("realized_pnl", 0)) for t in trades if float(t.get("realized_pnl", 0)) > 0), default=0), 2),
            "total_trades_volume": round(total_trades_volume, 2),
            "avg_trades_per_day": round(len(agg_positions) / time_span_days, 2),
            "avg_trade_win": round((long_wins_pnl + short_wins_pnl - 0.02) / (long_wins + short_wins) if (long_wins + short_wins) > 0 else 0, 2),
            "avg_trade_loss": round((long_losses_pnl + short_losses_pnl + 0.02) / (long_losses + short_losses) if (long_losses + short_losses) > 0 else 0, 2),
            "largest_losses": round(min((float(t.get("realized_pnl", 0)) for t in trades if float(t.get("realized_pnl", 0)) < 0), default=0), 2),
            "max_consecutive_win": max_consec_win,
            "max_consecutive_loss": max_consec_loss,
        },
        "longs": {
            "count": len([p for p in agg_positions if p["direction"] == "Long"]),
            "win_ratio": round(long_wins / (long_wins + long_losses) * 100 if (long_wins + long_losses) > 0 else 0, 2),
            "wins": long_wins,
            "losses": long_losses,
            "avg_duration_ms": avg_hold_ms,
            "total_realized_pnl": round(long_pnl, 2),
            "avg_win": round(long_wins_pnl / long_wins if long_wins > 0 else 0, 2),
            "avg_loss": round(long_losses_pnl / long_losses if long_losses > 0 else 0, 2),
        },
        "shorts": {
            "count": len([p for p in agg_positions if p["direction"] == "Short"]),
            "win_ratio": round(short_wins / (short_wins + short_losses) * 100 if (short_wins + short_losses) > 0 else 0, 2),
            "wins": short_wins,
            "losses": short_losses,
            "avg_duration_ms": avg_hold_ms,
            "total_realized_pnl": round(short_pnl, 2),
            "avg_win": round(short_wins_pnl / short_wins if short_wins > 0 else 0, 2),
            "avg_loss": round(short_losses_pnl / short_losses if short_losses > 0 else 0, 2),
        },
        "pnl_asset": "USDT",
    }


# ─── /api/reports ────────────────────────────────────────────────────────────────
@app.get("/api/reports", dependencies=[Depends(auth)])
def reports(market: str = Query("usdm", pattern="^(usdm|coinm)$")):
    closed = "exchange=%s AND market=%s AND close_time IS NOT NULL"
    args = (EXCHANGE, market)
    # 多空对比
    ls = rows(
        f"SELECT direction, COUNT(*) n, "
        f"COUNT(*) FILTER (WHERE realized_pnl>0) wins, "
        f"COALESCE(SUM(realized_pnl),0) pnl, COALESCE(AVG(hold_ms),0) avg_hold "
        f"FROM positions WHERE {closed} GROUP BY direction", args)
    # 按平仓星期几（0=周日）
    by_day = rows(
        f"SELECT EXTRACT(DOW FROM to_timestamp(close_time/1000))::int dow, "
        f"COALESCE(SUM(realized_pnl),0) pnl, COUNT(*) n "
        f"FROM positions WHERE {closed} GROUP BY dow ORDER BY dow", args)
    # 按平仓小时（UTC）
    by_hour = rows(
        f"SELECT EXTRACT(HOUR FROM to_timestamp(close_time/1000))::int hour, "
        f"COALESCE(SUM(realized_pnl),0) pnl, COUNT(*) n "
        f"FROM positions WHERE {closed} GROUP BY hour ORDER BY hour", args)
    # 持仓时长分桶
    duration = rows(
        f"SELECT CASE "
        f" WHEN hold_ms < 3600000 THEN '<1h' "
        f" WHEN hold_ms < 86400000 THEN '1h-1d' "
        f" WHEN hold_ms < 604800000 THEN '1d-1w' "
        f" ELSE '>1w' END bucket, "
        f"COUNT(*) n, COALESCE(SUM(realized_pnl),0) pnl "
        f"FROM positions WHERE {closed} GROUP BY bucket", args)
    # 规模分桶（按名义额 qty*avg_entry）
    size = rows(
        f"SELECT CASE "
        f" WHEN qty*avg_entry < 100 THEN '<100' "
        f" WHEN qty*avg_entry < 1000 THEN '100-1k' "
        f" WHEN qty*avg_entry < 10000 THEN '1k-10k' "
        f" ELSE '>10k' END bucket, "
        f"COUNT(*) n, COALESCE(SUM(realized_pnl),0) pnl "
        f"FROM positions WHERE {closed} AND avg_entry IS NOT NULL GROUP BY bucket", args)
    return {
        "market": market,
        "long_short": ls,
        "by_day_of_week": by_day,
        "by_hour": by_hour,
        "duration_buckets": duration,
        "size_buckets": size,
        "pnl_asset": "USDT" if market != "coinm" else "coin",
    }


# ─── /api/symbols ────────────────────────────────────────────────────────────────
@app.get("/api/symbols", dependencies=[Depends(auth)])
def symbols(market: str = Query(None, pattern="^(usdm|coinm)$")):
    # 从数据库查询实际交易过的符号（动态）
    if market:
        trades = rows(
            "SELECT symbol, market, COUNT(*) as trade_count FROM trades WHERE market = %s GROUP BY symbol, market ORDER BY trade_count DESC",
            (market,)
        )
    else:
        trades = rows(
            "SELECT symbol, market, COUNT(*) as trade_count FROM trades GROUP BY symbol, market ORDER BY trade_count DESC"
        )

    # 转换格式供前端消费
    return {
        "symbols": [
            {"symbol": t["symbol"], "market": t["market"], "trades": t["trade_count"]}
            for t in trades
        ]
    }


@app.get("/api/health")
def health():
    return {"ok": True, "ts": int(time.time() * 1000)}


# ─── SPA 静态托管（放在所有 /api 路由之后）────────────────────────────────────────
_static_dir = os.path.join(os.path.dirname(__file__), "static")
_index = os.path.join(_static_dir, "index.html")
_assets = os.path.join(_static_dir, "assets")

if os.path.isdir(_assets):
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")

if os.path.isfile(_index):
    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """SPA 客户端路由兜底：存在的静态文件直接返回，否则回 index.html。"""
        if full_path.startswith("api"):
            raise HTTPException(404, "not found")
        candidate = os.path.join(_static_dir, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(_index)
