#!/usr/bin/env python3
"""
klines.py — 按需拉取 K 线 + 计算 MAE/MFE + 两个仪表盘指标（grill 决策 #4、#6）

不存全量 K 线：打开持仓详情时，按 open→close 时间窗去 Binance 拉那段 K 线，
算 MAE/MFE 与 gauges，回写 positions 行缓存（metrics_at 标记已算）。

指标定义（写入 docs/FRONTEND-REDESIGN.md）：
  MAE / MFE        —— 持仓窗口内相对均价入场的最大不利 / 有利波动（按方向，% 表示）
  OpportunityCapture = 实际捕获波动 / MFE（抓住了这波最大有利波动的百分之多少，0–100）
  EntryQuality       = 1 − |MAE| / (|MAE| + MFE)（入场后不利回撤占总波幅越小越高，0–100）
"""
import time

try:
    from .common import make_client, get_conn
except ImportError:
    from common import make_client, get_conn

_client = [None]


def _client_get():
    if _client[0] is None:
        _client[0] = make_client()
    return _client[0]


# ─── 选 interval：让窗口落在 ~200–600 根 K 线 ───────────────────────────────────
def pick_interval(span_ms):
    h = span_ms / 3600_000
    if h <= 8:
        return "1m"
    if h <= 48:
        return "15m"
    if h <= 24 * 14:
        return "1h"
    if h <= 24 * 90:
        return "4h"
    return "1d"


def fetch_klines(market, symbol, start_ms, end_ms, interval=None):
    """拉某市场某 symbol 在 [start,end] 的 K 线。返回 [{t,o,h,l,c,v}, ...]。"""
    client = _client_get()
    interval = interval or pick_interval(max(end_ms - start_ms, 60_000))
    # 给两端各留一点边距，避免开/平仓点贴边
    pad = max(int((end_ms - start_ms) * 0.05), 60_000)
    fn = {
        "spot": client.get_klines,
        "usdm": client.futures_klines,
        "coinm": client.futures_coin_klines,
    }[market]
    raw = fn(symbol=symbol, interval=interval,
             startTime=start_ms - pad, endTime=end_ms + pad, limit=1000)
    return [
        {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
         "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
        for k in raw
    ], interval


# ─── MAE/MFE + gauges ──────────────────────────────────────────────────────────
def compute_metrics(position, candles):
    """
    position: positions 行 dict（需 direction, avg_entry, avg_exit）。
    candles : fetch_klines 产出的 K 线列表。
    返回 {mae, mfe, entry_quality, opportunity_capture}（mae/mfe 为 %，gauges 为 0–100）。
    """
    entry = position.get("avg_entry")
    if not entry or not candles:
        return {"mae": None, "mfe": None,
                "entry_quality": None, "opportunity_capture": None}

    hi = max(c["h"] for c in candles)
    lo = min(c["l"] for c in candles)
    exit_px = position.get("avg_exit")
    is_long = position["direction"] == "Long"

    if is_long:
        mfe_px = max(hi - entry, 0.0)              # 有利：向上
        mae_px = min(lo - entry, 0.0)              # 不利：向下（负）
        captured = (exit_px - entry) if exit_px else 0.0
    else:
        mfe_px = max(entry - lo, 0.0)              # 有利：向下
        mae_px = min(entry - hi, 0.0)              # 不利：向上（负）
        captured = (entry - exit_px) if exit_px else 0.0

    mfe_pct = mfe_px / entry * 100
    mae_pct = mae_px / entry * 100                 # ≤ 0

    # OpportunityCapture：捕获 / 最大有利，clip 0–100
    if mfe_px > 1e-12:
        oc = max(min(captured / mfe_px, 1.0), 0.0) * 100
    else:
        oc = 0.0
    # EntryQuality：不利占总波幅越小越好
    denom = abs(mae_px) + mfe_px
    eq = (1 - abs(mae_px) / denom) * 100 if denom > 1e-12 else 100.0

    return {"mae": round(mae_pct, 4), "mfe": round(mfe_pct, 4),
            "entry_quality": round(eq, 2), "opportunity_capture": round(oc, 2)}


def ensure_metrics(conn, position):
    """
    若该持仓 metrics 未算（metrics_at IS NULL）且已平仓，则拉 K 线算并回写。
    返回 (metrics dict, candles list, interval)。
    """
    if position.get("metrics_at") is not None:
        return ({k: position.get(k) for k in
                 ("mae", "mfe", "entry_quality", "opportunity_capture")}, None, None)

    start = position["open_time"]
    end = position["close_time"] or int(time.time() * 1000)
    candles, interval = fetch_klines(position["market"], position["symbol"], start, end)
    metrics = compute_metrics(position, candles)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE positions SET mae=%s, mfe=%s, entry_quality=%s, "
            "opportunity_capture=%s, metrics_at=NOW() WHERE id=%s",
            (metrics["mae"], metrics["mfe"], metrics["entry_quality"],
             metrics["opportunity_capture"], position["id"]),
        )
    conn.commit()
    return metrics, candles, interval
