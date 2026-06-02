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
    # MOCK 數據用於前端驗證
    import datetime
    import random
    random.seed(42)  # 固定種子
    base_date = datetime.datetime(2026, 1, 1)
    equity = []
    cum_pnl = 1000.0
    trend = 25.0  # 平緩上升趨勢
    for i in range(120):
        # 趨勢 + 隨機波動
        daily_pnl = trend + random.uniform(-60, 80)
        cum_pnl += daily_pnl
        equity.append({
            "t": int((base_date + datetime.timedelta(days=i)).timestamp() * 1000),
            "cum": round(cum_pnl, 2)
        })

    return {
        "balances": [
            {"market": "usdm", "asset": "USDT", "free": 5000.0, "locked": 0.0, "balance": 5000.0},
            {"market": "usdm", "asset": "BTC", "free": 0.5, "locked": 0.0, "balance": 0.5},
            {"market": "usdm", "asset": "ETH", "free": 2.0, "locked": 0.0, "balance": 2.0},
            {"market": "coinm", "asset": "USDT", "free": 3000.0, "locked": 0.0, "balance": 3000.0},
            {"market": "spot", "asset": "BNB", "free": 10.0, "locked": 0.0, "balance": 10.0},
        ],
        "kpi": {
            "total_positions": 156,
            "win_rate": 62.5,
            "usdm_realized_pnl": 3250.75,
            "pnl_asset": "USDT",
        },
        "equity_curve": equity,
        "note": "MOCK 數據用於前端驗證",
    }


# ─── /api/positions ────────────────────────────────────────────────────────────
@app.get("/api/positions", dependencies=[Depends(auth)])
def list_positions(
    market: str = Query(None, pattern="^(spot|usdm|coinm)$"),
    symbol: str = None,
    direction: str = Query(None, pattern="^(Long|Short)$"),
    status: str = Query(None, pattern="^(open|closed)$"),
    start: int = None,
    end: int = None,
    sort: str = Query("open_time", pattern="^(open_time|close_time|realized_pnl|hold_ms)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # MOCK 數據用於前端驗證
    import datetime
    import random
    random.seed(42)

    base_date = datetime.datetime(2026, 1, 1)
    symbols_list = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT"]
    markets_list = ["usdm", "usdm", "usdm", "coinm", "spot"]
    directions_list = ["Long", "Short"]

    all_positions = []
    for i in range(156):
        sym_idx = i % len(symbols_list)
        open_ts = int((base_date + datetime.timedelta(days=i//2)).timestamp() * 1000)
        close_ts = open_ts + random.randint(3600000, 432000000)  # 1 hour to 5 days
        pnl = random.uniform(-500, 800)

        pos = {
            "id": i + 1,
            "market": markets_list[sym_idx],
            "symbol": symbols_list[sym_idx],
            "direction": random.choice(directions_list),
            "open_trade_id": 0,
            "open_time": open_ts,
            "close_time": close_ts,
            "hold_ms": close_ts - open_ts,
            "qty": round(random.uniform(0.01, 10), 2),
            "avg_entry": round(random.uniform(1000, 50000), 2),
            "avg_exit": round(random.uniform(1000, 50000), 2),
            "realized_pnl": round(pnl, 2),
            "pnl_asset": "USDT",
            "is_estimated": False,
            "fees": round(abs(pnl) * 0.001, 2),
            "fee_asset": "USDT",
            "funding": 0.0,
            "num_fills": random.randint(1, 3),
            "mae": round(random.uniform(-500, 0), 2),
            "mfe": round(random.uniform(0, 500), 2),
            "entry_quality": round(random.random(), 2),
            "opportunity_capture": round(random.random(), 2),
        }

        # Filter by market
        if market and pos["market"] != market:
            continue
        # Filter by symbol
        if symbol and pos["symbol"] != symbol:
            continue
        # Filter by direction
        if direction and pos["direction"] != direction:
            continue
        # Filter by status
        if status == "open" and pos["close_time"] is not None:
            continue
        elif status == "closed" and pos["close_time"] is None:
            continue

        all_positions.append(pos)

    # Sort
    reverse = order == "desc"
    all_positions.sort(key=lambda x: x[sort], reverse=reverse)

    # Paginate
    total = len(all_positions)
    data = all_positions[offset:offset + limit]

    return {"total": total, "limit": limit, "offset": offset, "positions": data}


# ─── /api/positions/{id} ─────────────────────────────────────────────────────────
@app.get("/api/positions/{pid}", dependencies=[Depends(auth)])
def position_detail(pid: int):
    p = one("SELECT * FROM positions WHERE id=%s AND exchange=%s", (pid, EXCHANGE))
    if not p:
        raise HTTPException(404, "position not found")
    # 懒计算 MAE/MFE + gauges（仅已平仓且未算过）
    if p.get("close_time") is not None and p.get("metrics_at") is None:
        conn = get_conn()
        try:
            metrics, _, _ = K.ensure_metrics(conn, p)
            p.update(metrics)
        except Exception as e:                # K 线拉取失败不阻塞详情
            p["metrics_error"] = str(e)
        finally:
            conn.close()
    # 组成详情 fill 列表
    fills = rows(
        "SELECT trade_id, side, price, qty_base, realized_pnl, fee, fee_asset, "
        "trade_time FROM trades WHERE exchange=%s AND market=%s AND symbol=%s "
        "AND trade_time BETWEEN %s AND %s ORDER BY trade_time ASC",
        (EXCHANGE, p["market"], p["symbol"], p["open_time"],
         p["close_time"] or int(time.time() * 1000)),
    )
    p["fills"] = fills
    return p


# ─── /api/positions/{id}/klines ──────────────────────────────────────────────────
@app.get("/api/positions/{pid}/klines", dependencies=[Depends(auth)])
def position_klines(pid: int, interval: str = None):
    p = one("SELECT * FROM positions WHERE id=%s AND exchange=%s", (pid, EXCHANGE))
    if not p:
        raise HTTPException(404, "position not found")
    start = p["open_time"]
    end = p["close_time"] or int(time.time() * 1000)
    try:
        candles, used = K.fetch_klines(p["market"], p["symbol"], start, end, interval)
    except Exception as e:
        raise HTTPException(502, f"klines fetch failed: {e}")
    return {
        "symbol": p["symbol"], "market": p["market"], "interval": used,
        "candles": candles,
        "markers": {
            "entry": {"t": p["open_time"], "price": p["avg_entry"]},
            "exit": {"t": p["close_time"], "price": p["avg_exit"]}
                    if p["close_time"] else None,
        },
    }


# ─── /api/analytics ──────────────────────────────────────────────────────────────
@app.get("/api/analytics", dependencies=[Depends(auth)])
def analytics(market: str = Query("usdm", pattern="^(usdm|coinm|spot)$")):
    # MOCK 數據用於前端驗證 - CMM Analytics 風格
    import datetime
    import random
    random.seed(42)  # 固定種子
    base_date = datetime.datetime(2026, 1, 1)

    # 生成交易數據
    symbols_list = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT"]
    directions = ["Long", "Short"]

    total_trades = 0
    total_pnl = 0.0
    total_hold_ms = 0
    wins = 0
    losses = 0
    long_trades = []
    short_trades = []
    daily_pnls = {}

    for i in range(156):
        sym_idx = i % len(symbols_list)
        direction = directions[i % 2]
        open_ts = int((base_date + datetime.timedelta(days=i//2)).timestamp() * 1000)
        close_ts = open_ts + random.randint(3600000, 432000000)  # 1h to 5d
        pnl = random.uniform(-500, 800)
        hold_ms = close_ts - open_ts

        # 按市場篩選
        if market == "usdm" and sym_idx > 2:
            continue
        elif market == "coinm" and sym_idx != 3:
            continue
        elif market == "spot" and sym_idx != 4:
            continue

        total_trades += 1
        total_pnl += pnl
        total_hold_ms += hold_ms
        if pnl > 0:
            wins += 1
        else:
            losses += 1

        # 按天聚合 PNL
        day = datetime.datetime.fromtimestamp(open_ts / 1000).date()
        daily_pnls[day] = daily_pnls.get(day, 0.0) + pnl

        trade = {
            "direction": direction,
            "pnl": pnl,
            "hold_ms": hold_ms,
        }

        if direction == "Long":
            long_trades.append(trade)
        else:
            short_trades.append(trade)

    # 計算統計值
    avg_hold_ms = total_hold_ms // total_trades if total_trades > 0 else 0
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

    long_wins = sum(1 for t in long_trades if t["pnl"] > 0)
    long_losses = sum(1 for t in long_trades if t["pnl"] <= 0)
    short_wins = sum(1 for t in short_trades if t["pnl"] > 0)
    short_losses = sum(1 for t in short_trades if t["pnl"] <= 0)

    long_pnl = sum(t["pnl"] for t in long_trades)
    short_pnl = sum(t["pnl"] for t in short_trades)

    long_avg_hold = sum(t["hold_ms"] for t in long_trades) // len(long_trades) if long_trades else 0
    short_avg_hold = sum(t["hold_ms"] for t in short_trades) // len(short_trades) if short_trades else 0

    long_wins_pnl = sum(t["pnl"] for t in long_trades if t["pnl"] > 0) or 1
    long_losses_pnl = sum(t["pnl"] for t in long_trades if t["pnl"] <= 0) or -1
    short_wins_pnl = sum(t["pnl"] for t in short_trades if t["pnl"] > 0) or 1
    short_losses_pnl = sum(t["pnl"] for t in short_trades if t["pnl"] <= 0) or -1

    # 日數
    num_days = len(daily_pnls) or 1

    return {
        "market": market,
        "kpi": {
            "total_trades": total_trades,
            "avg_hold_ms": avg_hold_ms,
            "win_rate": round(win_rate, 2),
            "longs": len(long_trades),
            "shorts": len(short_trades),
            "long_pct": round(len(long_trades) / total_trades * 100, 2) if total_trades > 0 else 0,
        },
        "statistics": {
            "total_gain_loss": round(total_pnl, 2),
            "trade_expectancy": round(total_pnl / total_trades if total_trades > 0 else 0, 2),
            "avg_daily_gain": round(total_pnl / num_days, 2),
            "avg_daily_volume": round(total_pnl * 2.5, 2),  # mock: ~volume multiplier
            "largest_gain": round(max((t["pnl"] for t in long_trades + short_trades if t["pnl"] > 0), default=0), 2),
            "total_trades_volume": round(total_pnl * 30, 2),
            "avg_trades_per_day": round(total_trades / num_days, 2),
            "avg_trade_win": round(long_wins_pnl / long_wins if long_wins > 0 else 0, 2),
            "avg_trade_loss": round(long_losses_pnl / long_losses if long_losses > 0 else 0, 2),
            "max_consecutive_win": 9,  # mock
            "max_consecutive_loss": 14,  # mock
            "largest_losses": round(min((t["pnl"] for t in long_trades + short_trades if t["pnl"] < 0), default=0), 2),
        },
        "longs": {
            "count": len(long_trades),
            "win_ratio": round(long_wins / len(long_trades) * 100 if long_trades else 0, 2),
            "wins": long_wins,
            "losses": long_losses,
            "avg_duration_ms": long_avg_hold,
            "total_realized_pnl": round(long_pnl, 2),
            "avg_win": round(long_wins_pnl / long_wins if long_wins > 0 else 0, 2),
            "avg_loss": round(long_losses_pnl / long_losses if long_losses > 0 else 0, 2),
        },
        "shorts": {
            "count": len(short_trades),
            "win_ratio": round(short_wins / len(short_trades) * 100 if short_trades else 0, 2),
            "wins": short_wins,
            "losses": short_losses,
            "avg_duration_ms": short_avg_hold,
            "total_realized_pnl": round(short_pnl, 2),
            "avg_win": round(short_wins_pnl / short_wins if short_wins > 0 else 0, 2),
            "avg_loss": round(short_losses_pnl / short_losses if short_losses > 0 else 0, 2),
        },
        "pnl_asset": "USDT" if market != "coinm" else "coin",
        "note": "MOCK 數據用於前端驗證",
    }


# ─── /api/reports ────────────────────────────────────────────────────────────────
@app.get("/api/reports", dependencies=[Depends(auth)])
def reports(market: str = Query("usdm", pattern="^(usdm|coinm|spot)$")):
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
def symbols(market: str = Query(None, pattern="^(spot|usdm|coinm)$")):
    # MOCK 數據用於前端驗證
    mock_symbols = [
        {
            "market": "usdm",
            "symbol": "BTCUSDT",
            "trades": 28,
            "wins": 18,
            "longs": 15,
            "shorts": 13,
            "total_gain": 2850.50,
            "win_rate": 64.29,
            "avg_hold_ms": 86400000,
            "pnl_asset": "USDT",
            "is_estimated": False,
        },
        {
            "market": "usdm",
            "symbol": "ETHUSDT",
            "trades": 32,
            "wins": 19,
            "longs": 18,
            "shorts": 14,
            "total_gain": 1950.75,
            "win_rate": 59.38,
            "avg_hold_ms": 72000000,
            "pnl_asset": "USDT",
            "is_estimated": False,
        },
        {
            "market": "usdm",
            "symbol": "BNBUSDT",
            "trades": 24,
            "wins": 16,
            "longs": 12,
            "shorts": 12,
            "total_gain": 1200.25,
            "win_rate": 66.67,
            "avg_hold_ms": 108000000,
            "pnl_asset": "USDT",
            "is_estimated": False,
        },
        {
            "market": "coinm",
            "symbol": "ADAUSDT",
            "trades": 45,
            "wins": 26,
            "longs": 22,
            "shorts": 23,
            "total_gain": 450.50,
            "win_rate": 57.78,
            "avg_hold_ms": 54000000,
            "pnl_asset": "USDT",
            "is_estimated": False,
        },
        {
            "market": "spot",
            "symbol": "XRPUSDT",
            "trades": 27,
            "wins": 19,
            "longs": 27,
            "shorts": 0,
            "total_gain": 550.75,
            "win_rate": 70.37,
            "avg_hold_ms": 180000000,
            "pnl_asset": "USDT",
            "is_estimated": False,
        },
    ]

    # Filter by market
    if market:
        mock_symbols = [s for s in mock_symbols if s["market"] == market]

    # Sort by trades descending
    mock_symbols.sort(key=lambda x: x["trades"], reverse=True)

    return {"symbols": mock_symbols}


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
