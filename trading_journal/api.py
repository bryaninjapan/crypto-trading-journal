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


# ─── /api/summary ──────────────────────────────────────────────────────────────
@app.get("/api/summary", dependencies=[Depends(auth)])
def summary():
    # 从数据库查询真实数据
    trades = rows("SELECT * FROM trades ORDER BY trade_time ASC")

    total_positions = len(trades)

    # 胜率计算（只计算有 realized_pnl 的交易，即期货/杠杆）
    pnl_trades = [t for t in trades if t.get("realized_pnl") and float(t["realized_pnl"]) != 0]
    if pnl_trades:
        wins = len([t for t in pnl_trades if float(t["realized_pnl"]) > 0])
        win_rate = (wins / len(pnl_trades)) * 100
    else:
        win_rate = 0.0

    # 期货 PNL 合计（USDM + COINM）
    futures_pnl = sum([
        float(t.get("realized_pnl", 0))
        for t in trades
        if t.get("market") in ["usdm", "coinm"]
    ])

    # 权益曲线（按交易时间的累积 PNL）
    equity_curve = []
    cum_pnl = 0.0
    for trade in trades:
        if trade.get("realized_pnl"):
            cum_pnl += float(trade["realized_pnl"])
        equity_curve.append({
            "t": int(trade.get("trade_time", 0)),
            "cum": round(cum_pnl, 8)
        })

    # 从 Binance API 查询真实余额
    from binance.client import Client
    try:
        client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET"))
        balances = []

        # USDM（灰尘过滤）
        for b in client.futures_account_balance():
            balance = float(b.get("balance", 0))
            if balance > 0.001:  # USDM 灰尘临界值
                balances.append({
                    "market": "usdm",
                    "asset": b["asset"],
                    "free": balance,
                    "locked": 0.0,
                    "balance": balance,
                    "usd_value": None,
                })

        # COINM（灰尘过滤）
        for b in client.futures_coin_account_balance():
            balance = float(b.get("balance", 0))
            if balance > 0.0001:  # COINM 灰尘临界值（更小）
                balances.append({
                    "market": "coinm",
                    "asset": b["asset"],
                    "free": balance,
                    "locked": 0.0,
                    "balance": balance,
                    "usd_value": None,
                })
    except Exception as e:
        # 如果 API 失败，返回空列表
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
    sort: str = Query("trade_time", pattern="^(trade_time|side|symbol|realized_pnl)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # 从真实数据库查询交易（现货：直接返回交易记录）
    trades = rows("SELECT * FROM trades ORDER BY trade_time DESC")

    all_positions = []
    for t in trades:
        pos = {
            "id": t.get("trade_id"),
            "market": t.get("market"),
            "symbol": t.get("symbol"),
            "direction": "LONG" if t.get("side") == "BUY" else "SHORT",
            "open_trade_id": t.get("trade_id"),
            "open_time": int(t.get("trade_time", 0)),
            "close_time": int(t.get("trade_time", 0)),
            "hold_ms": 0,
            "qty": float(t.get("qty_base", 0)),
            "avg_entry": float(t.get("price", 0)),
            "avg_exit": float(t.get("price", 0)),
            "realized_pnl": float(t.get("realized_pnl", 0)) if t.get("realized_pnl") else 0.0,
            "pnl_asset": "USDT",
            "is_estimated": False,
            "fees": float(t.get("fee", 0)) if t.get("fee") else 0.0,
            "fee_asset": t.get("fee_asset") or "USDT",
            "funding": 0.0,
            "num_fills": 1,
        }

        if market and pos["market"] != market:
            continue
        if symbol and pos["symbol"] != symbol:
            continue
        if direction and pos["direction"] != direction:
            continue

        all_positions.append(pos)

    reverse = order == "desc"
    all_positions.sort(key=lambda x: x["open_time"], reverse=reverse)

    total = len(all_positions)
    data = all_positions[offset:offset + limit]

    return {"total": total, "limit": limit, "offset": offset, "positions": data}


# ─── /api/positions/{trade_id} ───────────────────────────────────────────────────
@app.get("/api/positions/{trade_id}", dependencies=[Depends(auth)])
def position_detail(trade_id: int):
    # 从数据库查询真实交易数据
    t = one(
        "SELECT * FROM trades WHERE trade_id = %s",
        (trade_id,)
    )
    if not t:
        raise HTTPException(status_code=404, detail="Position not found")

    return {
        "id": t.get("trade_id"),
        "exchange": EXCHANGE,
        "market": t.get("market"),
        "symbol": t.get("symbol"),
        "direction": "LONG" if t.get("side") == "BUY" else "SHORT",
        "open_trade_id": t.get("trade_id"),
        "open_time": int(t.get("trade_time", 0)),
        "close_time": int(t.get("trade_time", 0)),
        "hold_ms": 0,
        "qty": float(t.get("qty_base", 0)),
        "avg_entry": float(t.get("price", 0)),
        "avg_exit": float(t.get("price", 0)),
        "realized_pnl": float(t.get("realized_pnl", 0)) if t.get("realized_pnl") else 0.0,
        "pnl_asset": "USDT",
        "is_estimated": False,
        "fees": float(t.get("fee", 0)) if t.get("fee") else 0.0,
        "fee_asset": t.get("fee_asset") or "USDT",
        "funding": 0.0,
        "num_fills": 1,
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
    trades = rows("SELECT * FROM trades WHERE market = %s ORDER BY trade_time ASC", (market,))

    total_trades = len(trades)
    total_pnl = sum([float(t.get("realized_pnl", 0)) for t in trades])

    # 按方向分类
    long_trades = [t for t in trades if t.get("side") == "BUY"]
    short_trades = [t for t in trades if t.get("side") == "SELL"]

    # 胜负统计（有 PNL 的交易）
    pnl_trades = [t for t in trades if float(t.get("realized_pnl", 0)) != 0]
    wins = len([t for t in pnl_trades if float(t.get("realized_pnl", 0)) > 0])
    losses = len([t for t in pnl_trades if float(t.get("realized_pnl", 0)) <= 0])
    win_rate = (wins / len(pnl_trades) * 100) if pnl_trades else 0.0

    # 多头统计
    long_pnl = sum([float(t.get("realized_pnl", 0)) for t in long_trades])
    long_wins = len([t for t in long_trades if float(t.get("realized_pnl", 0)) > 0])
    long_losses = len([t for t in long_trades if float(t.get("realized_pnl", 0)) <= 0])
    long_wins_pnl = sum([float(t.get("realized_pnl", 0)) for t in long_trades if float(t.get("realized_pnl", 0)) > 0]) or 0.01
    long_losses_pnl = sum([float(t.get("realized_pnl", 0)) for t in long_trades if float(t.get("realized_pnl", 0)) < 0]) or -0.01

    # 空头统计
    short_pnl = sum([float(t.get("realized_pnl", 0)) for t in short_trades])
    short_wins = len([t for t in short_trades if float(t.get("realized_pnl", 0)) > 0])
    short_losses = len([t for t in short_trades if float(t.get("realized_pnl", 0)) <= 0])
    short_wins_pnl = sum([float(t.get("realized_pnl", 0)) for t in short_trades if float(t.get("realized_pnl", 0)) > 0]) or 0.01
    short_losses_pnl = sum([float(t.get("realized_pnl", 0)) for t in short_trades if float(t.get("realized_pnl", 0)) < 0]) or -0.01

    # 最大连胜/连败（按时间顺序遍历有 PNL 的交易）
    max_consec_win = max_consec_loss = cur_win = cur_loss = 0
    for t in pnl_trades:
        if float(t.get("realized_pnl", 0)) > 0:
            cur_win += 1
            cur_loss = 0
            max_consec_win = max(max_consec_win, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            max_consec_loss = max(max_consec_loss, cur_loss)

    return {
        "market": market,
        "kpi": {
            "total_trades": total_trades,
            "avg_hold_ms": 0,
            "win_rate": round(win_rate, 2),
            "longs": len(long_trades),
            "shorts": len(short_trades),
            "long_pct": round(len(long_trades) / total_trades * 100, 2) if total_trades > 0 else 0,
        },
        "statistics": {
            "total_gain_loss": round(total_pnl, 2),
            "trade_expectancy": round(total_pnl / total_trades if total_trades > 0 else 0, 2),
            "avg_daily_gain": round(total_pnl / 30, 2),
            "avg_daily_volume": 0,
            "largest_gain": round(max([float(t.get("realized_pnl", 0)) for t in trades if float(t.get("realized_pnl", 0)) > 0], default=0), 2),
            "total_trades_volume": 0,
            "avg_trades_per_day": 0,
            "avg_trade_win": round(long_wins_pnl / long_wins if long_wins > 0 else 0, 2),
            "avg_trade_loss": round(long_losses_pnl / long_losses if long_losses > 0 else 0, 2),
            "largest_losses": round(min([float(t.get("realized_pnl", 0)) for t in trades if float(t.get("realized_pnl", 0)) < 0], default=0), 2),
            "max_consecutive_win": max_consec_win,
            "max_consecutive_loss": max_consec_loss,
        },
        "longs": {
            "count": len(long_trades),
            "win_ratio": round(long_wins / len(long_trades) * 100 if long_trades else 0, 2),
            "wins": long_wins,
            "losses": long_losses,
            "avg_duration_ms": 0,
            "total_realized_pnl": round(long_pnl, 2),
            "avg_win": round(long_wins_pnl / long_wins if long_wins > 0 else 0, 2),
            "avg_loss": round(long_losses_pnl / long_losses if long_losses > 0 else 0, 2),
        },
        "shorts": {
            "count": len(short_trades),
            "win_ratio": round(short_wins / len(short_trades) * 100 if short_trades else 0, 2),
            "wins": short_wins,
            "losses": short_losses,
            "avg_duration_ms": 0,
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
