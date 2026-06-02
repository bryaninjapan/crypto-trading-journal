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

try:
    from .common import get_conn
    from . import klines as K
except ImportError:
    from common import get_conn
    import klines as K

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
    # 最新余额快照（按市场）
    bal = rows(
        "SELECT market, asset, free, locked, balance FROM balances "
        "WHERE exchange=%s AND snapshot_time=("
        "  SELECT MAX(snapshot_time) FROM balances WHERE exchange=%s) "
        "ORDER BY market, balance DESC",
        (EXCHANGE, EXCHANGE),
    )
    # KPI（已平仓）
    kpi = one(
        "SELECT COUNT(*) total, "
        "COUNT(*) FILTER (WHERE realized_pnl>0) wins, "
        "COUNT(*) FILTER (WHERE realized_pnl<0) losses, "
        "COALESCE(SUM(realized_pnl) FILTER (WHERE market='usdm'),0) usdm_pnl "
        "FROM positions WHERE exchange=%s AND close_time IS NOT NULL",
        (EXCHANGE,),
    ) or {}
    decided = (kpi.get("wins", 0) or 0) + (kpi.get("losses", 0) or 0)
    win_rate = round(kpi.get("wins", 0) / decided * 100, 2) if decided else 0.0
    # USD-M 权益曲线（按平仓日累计）
    daily = rows(
        "SELECT (close_time/86400000)*86400000 AS day_ms, SUM(realized_pnl) pnl "
        "FROM positions WHERE exchange=%s AND market='usdm' AND close_time IS NOT NULL "
        "GROUP BY day_ms ORDER BY day_ms",
        (EXCHANGE,),
    )
    equity = [{"t": int(d["day_ms"]), "cum": v}
              for d, v in zip(daily, cumsum([float(d["pnl"] or 0) for d in daily]))]
    return {
        "balances": bal,
        "kpi": {
            "total_positions": kpi.get("total", 0),
            "win_rate": win_rate,
            "usdm_realized_pnl": round(float(kpi.get("usdm_pnl", 0) or 0), 4),
            "pnl_asset": "USDT",
        },
        "equity_curve": equity,
        "note": "Consolidated PNL 暂仅 Binance（Flipster 延后）；统计基于已平仓持仓。",
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
    where = ["exchange=%s"]
    args = [EXCHANGE]
    if market:
        where.append("market=%s"); args.append(market)
    if symbol:
        where.append("symbol=%s"); args.append(symbol)
    if direction:
        where.append("direction=%s"); args.append(direction)
    if status == "open":
        where.append("close_time IS NULL")
    elif status == "closed":
        where.append("close_time IS NOT NULL")
    if start:
        where.append("open_time>=%s"); args.append(start)
    if end:
        where.append("open_time<=%s"); args.append(end)
    wsql = " AND ".join(where)
    total = one(f"SELECT COUNT(*) n FROM positions WHERE {wsql}", tuple(args))["n"]
    data = rows(
        f"SELECT id, market, symbol, direction, open_trade_id, open_time, close_time, "
        f"hold_ms, qty, avg_entry, avg_exit, realized_pnl, pnl_asset, is_estimated, "
        f"fees, fee_asset, funding, num_fills, mae, mfe, entry_quality, "
        f"opportunity_capture FROM positions WHERE {wsql} "
        f"ORDER BY {sort} {order.upper()} NULLS LAST LIMIT %s OFFSET %s",
        tuple(args) + (limit, offset),
    )
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
    closed = "exchange=%s AND market=%s AND close_time IS NOT NULL"
    args = (EXCHANGE, market)
    daily = rows(
        f"SELECT (close_time/86400000)*86400000 AS day_ms, SUM(realized_pnl) pnl "
        f"FROM positions WHERE {closed} GROUP BY day_ms ORDER BY day_ms", args)
    pnls = [float(d["pnl"] or 0) for d in daily]
    cum = cumsum(pnls)
    peak, dd = 0.0, []
    for c in cum:
        peak = max(peak, c)
        dd.append(round(c - peak, 8))
    equity = [{"t": int(d["day_ms"]), "cum": c, "drawdown": ddv}
              for d, c, ddv in zip(daily, cum, dd)]

    agg = one(
        f"SELECT COUNT(*) total, "
        f"COUNT(*) FILTER (WHERE realized_pnl>0) wins, "
        f"COUNT(*) FILTER (WHERE realized_pnl<0) losses, "
        f"COUNT(*) FILTER (WHERE direction='Long') longs, "
        f"COUNT(*) FILTER (WHERE direction='Short') shorts, "
        f"COALESCE(SUM(realized_pnl),0) total_pnl, "
        f"COALESCE(AVG(realized_pnl) FILTER (WHERE realized_pnl>0),0) avg_win, "
        f"COALESCE(AVG(realized_pnl) FILTER (WHERE realized_pnl<0),0) avg_loss, "
        f"COALESCE(AVG(hold_ms),0) avg_hold_ms "
        f"FROM positions WHERE {closed}", args) or {}
    wins, losses = agg.get("wins", 0), agg.get("losses", 0)
    decided = wins + losses
    win_rate = round(wins / decided * 100, 2) if decided else 0.0
    avg_win = float(agg.get("avg_win", 0) or 0)
    avg_loss = float(agg.get("avg_loss", 0) or 0)
    p_win = wins / decided if decided else 0
    # 期望值（每笔）= p_win*avg_win + p_loss*avg_loss
    expectancy = round(p_win * avg_win + (1 - p_win) * avg_loss, 4)
    ndays = len(daily)
    return {
        "market": market,
        "equity_curve": equity,
        "expectancy_donut": {"wins": wins, "losses": losses, "win_rate": win_rate},
        "long_short": {
            "longs": agg.get("longs", 0), "shorts": agg.get("shorts", 0),
            "long_pct": round(agg.get("longs", 0) /
                              max(agg.get("total", 1), 1) * 100, 2),
        },
        "statistics": {
            "total_gain_loss": round(float(agg.get("total_pnl", 0) or 0), 4),
            "trade_expectancy": expectancy,
            "avg_daily_gain": round(float(agg.get("total_pnl", 0) or 0) / ndays, 4)
                              if ndays else 0.0,
            "avg_hold_ms": int(agg.get("avg_hold_ms", 0) or 0),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
        },
        "pnl_asset": "USDT" if market != "coinm" else "coin",
        "note": "统计基于已平仓持仓；coinm 为币本位不可跨 symbol 相加。",
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
    where = ["exchange=%s", "close_time IS NOT NULL"]
    args = [EXCHANGE]
    if market:
        where.append("market=%s"); args.append(market)
    wsql = " AND ".join(where)
    data = rows(
        f"SELECT market, symbol, COUNT(*) trades, "
        f"COUNT(*) FILTER (WHERE realized_pnl>0) wins, "
        f"COUNT(*) FILTER (WHERE direction='Long') longs, "
        f"COUNT(*) FILTER (WHERE direction='Short') shorts, "
        f"COALESCE(SUM(realized_pnl),0) total_gain, "
        f"COALESCE(AVG(hold_ms),0) avg_hold_ms, "
        f"MAX(pnl_asset) pnl_asset, BOOL_OR(is_estimated) is_estimated "
        f"FROM positions WHERE {wsql} GROUP BY market, symbol "
        f"ORDER BY trades DESC", tuple(args))
    for d in data:
        dec = d["trades"]
        d["win_rate"] = round((d["wins"] or 0) / dec * 100, 2) if dec else 0.0
        d["total_gain"] = round(float(d["total_gain"] or 0), 4)
        d["avg_hold_ms"] = int(d["avg_hold_ms"] or 0)
    return {"symbols": data}


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
