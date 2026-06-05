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


# ─── Binance 现价 / PNL → USD 换算（summary / positions / detail 共用）──────────
def _binance_client():
    """创建 Binance client；缺 key 或失败时返回 None。"""
    from binance.client import Client
    try:
        return Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET"))
    except Exception as e:
        print(f"[warning] 无法创建 Binance client: {e}")
        return None


def _load_prices(client=None):
    """返回 {symbol: price} 现价表；失败返回 {}。
    可传入已建好的 client 复用（如 summary 还要用 client 查余额）。"""
    try:
        client = client or _binance_client()
        if client is None:
            return {}
        return {t["symbol"]: float(t["price"]) for t in client.get_all_tickers()}
    except Exception as e:
        print(f"[warning] 无法从 Binance 获取价格: {e}")
        return {}


def _pnl_to_usd(pnl, asset, prices):
    """把以 `asset` 计价的金额换算成 USD。
    USDT → 已是 USD，原样返回；其他（COIN-M 的 ADA/BTC…）→ 乘以 {asset}USDT 现价。
    缺现价时返回 None（宁可不显示 USD，也不混入币本位数量）。
    注：用「现价」近似换算，非成交当时价（沿用 summary 的近似口径）。"""
    if pnl is None:
        return None
    pnl = float(pnl)
    if not asset or asset == "USDT":
        return round(pnl, 2)
    price = prices.get(f"{asset}USDT")
    return round(pnl * price, 2) if price is not None else None


# ─── Position Aggregation (fills → trades，FIFO 配對) ──────────────────────────
# 模型（對標 CoinMarketMan）：把每個平倉 fill 依 FIFO 與最早的開倉 fill 配對，每一段
# 「一個開倉 fill × 一個平倉 fill」的配對量 = 一筆已平交易（closed trade）。CMM 實測：
# trade #376 = SELL@75934.7(27/May 開空) + BUY@73405.3(30/May 平空)，open 永遠早於 close。
#
# 為什麼改掉舊的「累積 qty 歸零」狀態機（解掉 4 個症狀）：
#   1) 絕對 tolerance 1e-3 對不同幣量級失效：BTCUSD（qty 0.001~0.04）被亂切成 7 個錯誤
#      position；ADAUSD（qty 39~9770）一次都切不動，70 筆全黏成 1 坨。
#   2) 把「平掉舊單(BUY) + 開新單(SELL)」黏成同一假 position → open_time(開倉 SELL) 晚於
#      close_time(平倉 BUY) = 時光穿越（7 個 BTCUSD position 全穿越）。
#   3) PNL 出現在時間序第一筆（其實是平倉），看起來像開倉就有 PNL。
#   4) 未平倉殘量被當成假交易混進清單。
# FIFO 模型天然保證 open_time ≤ close_time（被配對的開倉 lot 必早於平倉 fill 進佇列），
# 且與幣量級無關。
#
# 方向（多空）一律採用 Binance 權威欄位 position_side（commit cbba99f）：
#   SHORT 倉：SELL=開倉、BUY=平倉；LONG 倉：BUY=開倉、SELL=平倉。
# 只有 position_side 缺失 / BOTH（USDM 單向模式舊資料）時，才退回依當前淨部位推斷。


def _seg_float(v):
    return float(v) if v is not None else 0.0


def _classify(fill, books):
    """判定一筆 fill 屬於哪個 book（LONG/SHORT）以及是開倉還是平倉。

    優先用 position_side；本資料集 position_side 100% NULL，故以 realized_pnl 為平倉錨：
    Binance 只在平倉結算 PNL，realized_pnl != 0 可靠標記平倉 fill。
    方向由 side 推：平倉 BUY→平 SHORT / SELL→平 LONG；開倉 BUY→開 LONG / SELL→開 SHORT。
    回傳 (book, is_open)。
    """
    side = fill.get("side")
    ps = fill.get("position_side")
    if ps in ("LONG", "SHORT"):
        is_open = (ps == "LONG" and side == "BUY") or (ps == "SHORT" and side == "SELL")
        return ps, is_open
    # position_side 缺失：以 realized_pnl 為平倉錨（不靠 net-position 猜開平）。
    is_close = abs(_seg_float(fill.get("realized_pnl"))) > 1e-12
    if is_close:
        return ("SHORT" if side == "BUY" else "LONG"), False
    return ("LONG" if side == "BUY" else "SHORT"), True


def _build_segment(market, symbol, book, open_fill, close_fill, matched_qty,
                   pnl_share, fee_share, seg_id, is_orphan=False):
    """把一段 FIFO 配對（一個開倉 fill × 一個平倉 fill）組成一筆已平交易 dict。

    open_fill 為 None 代表 orphan close（窗口前就開、佇列無對應開倉 lot）：open_time
    退回平倉時間（保證 open ≤ close），avg_entry/開倉資訊缺省，標記 is_orphan_close。
    回傳 dict 內含 fills=[開倉, 平倉]（時間順），供 detail 端點展開。
    """
    close_time = int(close_fill.get("trade_time", 0))
    if open_fill is not None:
        open_time     = int(open_fill.get("trade_time", 0))
        avg_entry     = round(_seg_float(open_fill.get("price")), 6)
        open_trade_id = open_fill.get("trade_id")
        margin_asset  = open_fill.get("margin_asset") or close_fill.get("margin_asset")
        seg_fills     = [open_fill, close_fill]
    else:
        open_time     = close_time          # orphan：無開倉時間，退回平倉時間
        avg_entry     = None
        open_trade_id = close_fill.get("trade_id")
        margin_asset  = close_fill.get("margin_asset")
        seg_fills     = [close_fill]

    pnl_asset = margin_asset or "USDT"
    fee_asset = close_fill.get("fee_asset") or pnl_asset

    return {
        "id":              seg_id,
        "market":          market,
        "symbol":          symbol,
        "direction":       "Short" if book == "SHORT" else "Long",
        "open_trade_id":   open_trade_id,
        "open_time":       open_time,
        "close_time":      close_time,
        "hold_ms":         close_time - open_time,
        "qty":             round(matched_qty, 8),
        "avg_entry":       avg_entry,
        "avg_exit":        round(_seg_float(close_fill.get("price")), 6),
        "realized_pnl":    round(pnl_share, 6),
        "pnl_asset":       pnl_asset,
        "is_estimated":    False,
        "is_orphan_close": is_orphan,
        "fees":            round(fee_share, 8),
        "fee_asset":       fee_asset,
        "funding":         0.0,
        "num_fills":       len(seg_fills),
        "fills":           seg_fills,
    }


def _preaggregate_by_order(fills):
    """order_id 預聚合：交易所把一張 order 分批成交（COIN-M 實測 29 張 order → 110 筆
    fill，同價同毫秒），raw fill 層級配對會把一張開倉單當成 N 個 lot，產生 N 筆 open/
    close/price 完全相同、只有 qty 不同的重複交易。FIFO 配對的正確單位是 order，不是 fill
    （對標 CMM 的「Avg Fill Price / Size」＝先合併同單 fill）。

    依 (market, symbol, order_id, side) 分組，每組併成一筆 synthetic fill：
      - qty_base    = Σ qty_base
      - price       = Σ(price×qty_base) / Σ qty_base（加權均價）
      - realized_pnl= Σ realized_pnl
      - fee         = Σ fee
      - trade_time  = 該 order 最後一筆 fill 的時間（= 完全成交時間）
      - id / trade_id = 取該 order 第一筆 fill（代表性，保證 detail 端點可由 id 反查回
        (market, symbol) 並重現同一 synthetic fill）
      - side / position_side / margin_asset / symbol / market 等同組一致，沿用第一筆。

    order_id 為 None 的舊資料 → 每筆各自獨立（不合併），避免 None 全併成一坨。
    回傳 synthetic fills list，餵給下游 FIFO（配對邏輯完全不動）。
    """
    from collections import defaultdict

    grouped = defaultdict(list)
    singles = []
    for f in fills:
        oid = f.get("order_id")
        if oid is None:
            singles.append(f)
            continue
        grouped[(f.get("market"), f.get("symbol"), oid, f.get("side"))].append(f)

    out = list(singles)
    for _key, order_fills in grouped.items():
        if len(order_fills) == 1:
            out.append(order_fills[0])
            continue
        ordered = sorted(order_fills, key=lambda x: (x.get("trade_time", 0), x.get("id", 0)))
        rep = dict(ordered[0])                       # 代表性 fill（id / trade_id / 共同欄位）
        total_qty = sum(_seg_float(f.get("qty_base")) for f in ordered)
        if total_qty > 0:
            vwap = sum(_seg_float(f.get("price")) * _seg_float(f.get("qty_base"))
                       for f in ordered) / total_qty
        else:
            vwap = _seg_float(rep.get("price"))
        rep["qty_base"]     = total_qty
        rep["price"]        = vwap
        rep["realized_pnl"] = sum(_seg_float(f.get("realized_pnl")) for f in ordered)
        rep["fee"]          = sum(_seg_float(f.get("fee")) for f in ordered)
        rep["trade_time"]   = ordered[-1].get("trade_time", rep.get("trade_time"))
        out.append(rep)

    return out


def build_positions_from_fills(fills):
    """將 fills 依 (market, symbol) 分組，用 FIFO 把每個平倉 fill 配對最早的開倉 fill，
    每段配對產生一筆已平交易（closed trade）。

    前置：先做 order_id 預聚合（_preaggregate_by_order），把同一張 order 被交易所拆成
    多筆的 fill 併回 order 層級，再餵給 FIFO，避免一張單被當 N 個 lot 產生重複交易。

    每筆交易：
      - open_time = 配對到的開倉 fill 時間；close_time = 該平倉 fill 時間（open ≤ close）。
      - qty = 該段配對量；avg_entry = 開倉價；avg_exit = 平倉價。
      - realized_pnl = 平倉 fill 的 realized_pnl 按配對量比例分攤（開倉 fill PNL=0）；
        分攤後全段加總 == 平倉 fill 原始 PNL，故總 PNL 與逐筆 fill 加總一致。
      - id = 平倉 fill 的 row id * 1000 + 段序號（全域唯一、可由 detail 反推）。
      - fills = [開倉 fill, 平倉 fill]（orphan 只含平倉），供前端展開。

    殘量處理：
      - 窗口結束佇列仍有開倉殘量（如 ADAUSD 殘 ~9721 ADA）= 未平倉，不產生 closed trade，
        排除在 Trade History 外（不做 open position UI）。
      - 平倉量超出佇列現有開倉量 = orphan close（窗口前就開、無對應開倉 fill），仍產生一筆
        交易（is_orphan_close=True），避免丟資料。
    """
    from collections import defaultdict, deque

    fills = _preaggregate_by_order(fills)

    groups = defaultdict(list)
    for f in fills:
        groups[(f.get("market"), f.get("symbol"))].append(f)

    EPS = 1e-9
    positions = []

    for (market, symbol), group_fills in groups.items():
        # 複合排序鍵：trade_time 為主，id（trades 表單調主鍵）為 tie-breaker，確保同毫秒
        # 大量 fill（COIN-M 常見）的順序與 SQL 物理回傳順序無關、完全確定。
        group_fills = sorted(group_fills, key=lambda x: (x.get("trade_time", 0), x.get("id", 0)))

        # 每個 book 維護開倉 lot 佇列：deque of [剩餘qty, open_fill]
        books = {"LONG": deque(), "SHORT": deque()}

        for f in group_fills:
            book, is_open = _classify(f, books)
            qty = _seg_float(f.get("qty_base"))
            if qty <= 0:
                continue

            if is_open:
                books[book].append([qty, f])
                continue

            # 平倉 fill：依 FIFO 消耗該 book 最早的開倉 lot
            lots        = books[book]
            close_total = qty
            close_pnl   = _seg_float(f.get("realized_pnl"))
            close_fee   = _seg_float(f.get("fee"))
            close_row   = f.get("id", 0)
            remaining   = qty
            seg_idx     = 0

            while remaining > EPS and lots:
                lot       = lots[0]
                open_fill = lot[1]
                matched   = min(remaining, lot[0])
                open_total = _seg_float(open_fill.get("qty_base")) or matched
                open_fee   = _seg_float(open_fill.get("fee"))
                # 平倉 PNL/手續費按配對量比例分攤；開倉手續費按其被消耗比例分攤
                pnl_share = close_pnl * (matched / close_total)
                fee_share = (open_fee * (matched / open_total)
                             + close_fee * (matched / close_total))
                positions.append(_build_segment(
                    market, symbol, book, open_fill, f, matched,
                    pnl_share, fee_share, close_row * 1000 + seg_idx))
                seg_idx    += 1
                lot[0]     -= matched
                remaining  -= matched
                if lot[0] <= EPS:
                    lots.popleft()

            if remaining > EPS:
                # orphan close：窗口前開倉，無對應 open lot，仍記一筆避免丟資料
                pnl_share = close_pnl * (remaining / close_total)
                fee_share = close_fee * (remaining / close_total)
                positions.append(_build_segment(
                    market, symbol, book, None, f, remaining,
                    pnl_share, fee_share, close_row * 1000 + seg_idx, is_orphan=True))

        # 佇列殘餘 = 未平倉，刻意不產生 closed trade

    return _merge_same_roundtrip(positions)


def _merge_same_roundtrip(positions):
    """FIFO 後的二次去重：同一張開倉單被多張平倉單（同價、同毫秒）平掉時，FIFO 會
    把該開倉單拆給每張平倉單，產生多筆 open/close 的 time+price 完全相同、只有 qty
    不同的段（例 METISUSDT 一張 4.0 開倉單被兩張 @9.86 同毫秒平倉單平掉 → 兩段）。
    這在 UI 上與 fill 切片重複無異，故依 (market, symbol, direction, open_time,
    avg_entry, close_time, avg_exit, is_orphan_close) 合併成一筆：qty/realized_pnl/
    fees 加總，fills 去重（共用的開倉單只算一次），id 取最小者（deterministic，
    detail 端點重跑同一流程可重現）。FIFO 配對本身完全不動。
    """
    from collections import OrderedDict

    merged = OrderedDict()
    for p in positions:
        key = (p["market"], p["symbol"], p["direction"], p["open_time"],
               p.get("avg_entry"), p["close_time"], p.get("avg_exit"),
               p.get("is_orphan_close"))
        if key not in merged:
            merged[key] = p
            continue
        m = merged[key]
        m["qty"]          = round(m["qty"] + p["qty"], 8)
        m["realized_pnl"] = round(m["realized_pnl"] + p["realized_pnl"], 6)
        m["fees"]         = round(m["fees"] + p["fees"], 8)
        m["id"]           = min(m["id"], p["id"])
        seen = {f.get("id") for f in m["fills"]}
        for f in p["fills"]:                      # 共用開倉單在多段重複，依 row id 去重
            if f.get("id") not in seen:
                m["fills"].append(f)
                seen.add(f.get("id"))
        m["fills"].sort(key=lambda f: (f.get("trade_time", 0), f.get("id", 0)))
        m["num_fills"] = len(m["fills"])

    return list(merged.values())


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
    client = _binance_client()
    prices = _load_prices(client)

    def to_usd(asset: str, amount: float) -> float | None:
        return _pnl_to_usd(amount, asset, prices)

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
            return _pnl_to_usd(pnl, coin, prices)
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

    # COIN-M 的 realized_pnl 以标的币（pnl_asset，如 ADA/BTC）计价，附带换算好的
    # USD 估值供前端并排显示；USDM 已是 USD。仅对当前页换算，省一次大批量计算。
    prices = _load_prices()
    for p in data:
        p.pop("fills", None)  # 清單不展開 fills（raw fill dict 含 Decimal/datetime），detail 才用
        p["realized_pnl_usd"] = _pnl_to_usd(p.get("realized_pnl"), p.get("pnl_asset"), prices)

    return {"total": total, "limit": limit, "offset": offset, "positions": data}


# ─── /api/positions/{trade_id} ───────────────────────────────────────────────────
@app.get("/api/positions/{trade_id}", dependencies=[Depends(auth)])
def position_detail(trade_id: int):
    # trade_id 為聚合後交易的合成 id = 平倉 fill 的 row id * 1000 + 段序號。
    # 反推平倉 fill 的 row id → 定位 (market, symbol)，再用同一 FIFO 聚合找回該段交易，
    # 與 build_positions_from_fills 完全一致（不再各自重跑狀態機，避免邏輯漂移）。
    close_row = trade_id // 1000
    anchor = one("SELECT * FROM trades WHERE id = %s", (close_row,))
    if not anchor:
        raise HTTPException(status_code=404, detail="Position not found")

    same_symbol = rows(
        "SELECT * FROM trades WHERE market=%s AND symbol=%s ORDER BY trade_time ASC, id ASC",
        (anchor["market"], anchor["symbol"]),
    )

    # 聚合並用合成 id 找出該段交易；build_positions_from_fills 已把配對到的開倉/平倉
    # fill 掛在 position["fills"]（時間順：開倉在前、平倉在後）。
    positions = build_positions_from_fills(same_symbol)
    target = next((p for p in positions if p["id"] == trade_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Position not found")

    target_fills = target.pop("fills", [])

    # COIN-M 的 realized_pnl 以标的币计价，附带 USD 估值供前端并排显示。
    prices = _load_prices()
    target["realized_pnl_usd"] = _pnl_to_usd(
        target.get("realized_pnl"), target.get("pnl_asset"), prices
    )

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

    # ── 單一事實來源：聚合後 positions（回合）──
    # 所有 Long/Short 與勝負統計都以 position 級計算。原先 fill 級路徑（按 DB 的
    # position_side 欄位分桶 + 用每筆 fill 的 realized_pnl 數勝負）有兩個 bug：
    #   1) count 用 position 級、W/L 用 fill 級 → 一個回合多筆平倉 fill 會貢獻多個
    #      W/L，wins+losses 永遠對不上 count；
    #   2) count 的方向曾用「首筆 fill side」啟發式推斷，COIN-M 空單（SELL 開→BUY
    #      平）若抓取窗口從平倉 BUY 開始，首筆是 BUY → 被誤判成 LONG，憑空生出假
    #      多頭回合。現已改為優先採用 Binance 權威欄位 position_side（見 _classify）。
    # 改為：方向一律用 position 的 direction，勝負一律看 position 淨 realized_pnl 正負。
    agg_positions = build_positions_from_fills(trades)

    # ── Binance 现价：COIN-M position realized_pnl → USD 换算 ──
    # build_positions_from_fills 產出的 position realized_pnl 是各 fill realized_pnl
    # 的原始計價單位直接相加：USD-M 為 USDT，COIN-M 為標的币（ADA/BTC 顆數）。
    # 與 /api/summary（見 DECISION-LOG-coinm-pnl-usd）一致，COIN-M 需乘現價換算成 USD
    # 後才能比較/累加。勝負分類只看正負號，換算乘正數現價不改變符號，故方向桶歸屬不受影響；
    # 但 avg_win/avg_loss/total_realized_pnl 的「金額」必須換算才有物理意義。
    prices = {}
    if market == "coinm":
        from binance.client import Client
        try:
            client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_SECRET"))
            prices = {t["symbol"]: float(t["price"]) for t in client.get_all_tickers()}
        except Exception as e:
            print(f"[warning] analytics 无法从 Binance 获取价格: {e}")

    def net_pnl(p):
        """position 淨 realized_pnl，換算為 USD。
        usdm → 已是 USDT 原樣返回；coinm → 乘標的币現價；
        缺現價返回 None（該 position 排除統計，不把币本位數量混進 USD 合計）。"""
        pnl = float(p.get("realized_pnl") or 0)
        if market == "usdm":
            return pnl
        coin = p.get("pnl_asset") or p.get("symbol", "").replace("USD_PERP", "").replace("USD", "")
        price = prices.get(f"{coin}USDT") if coin else None
        return pnl * price if price is not None else None

    # 每個 position 的淨 PnL（USD）；None=缺現價，排除
    pos_pnls = [(p, net_pnl(p)) for p in agg_positions]
    pos_pnls = [(p, v) for (p, v) in pos_pnls if v is not None]

    # 頂層勝負（position 級）：淨 PnL >0 勝、<0 負、==0 不計
    decided = [(p, v) for (p, v) in pos_pnls if v != 0]
    wins   = len([1 for (_, v) in decided if v > 0])
    losses = len([1 for (_, v) in decided if v < 0])
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

    total_pnl = sum(v for (_, v) in pos_pnls)

    def direction_stats(direction):
        """某方向（"Long"/"Short"）的 position 級統計。"""
        items     = [(p, v) for (p, v) in pos_pnls if p["direction"] == direction]
        win_pnls  = [v for (_, v) in items if v > 0]
        loss_pnls = [v for (_, v) in items if v < 0]
        nw, nl = len(win_pnls), len(loss_pnls)
        return {
            # count 含缺現價被排除者，故恆有 wins+losses ≤ count
            "count": len([p for p in agg_positions if p["direction"] == direction]),
            "win_ratio": round(nw / (nw + nl) * 100, 2) if (nw + nl) > 0 else 0,
            "wins": nw,
            "losses": nl,
            "avg_duration_ms": avg_hold_ms,
            "total_realized_pnl": round(sum(v for (_, v) in items), 2),
            "avg_win": round(sum(win_pnls) / nw, 2) if nw > 0 else 0,
            "avg_loss": round(sum(loss_pnls) / nl, 2) if nl > 0 else 0,
        }

    # 平均持倉時間：從聚合後 positions 計算
    closed_positions = [p for p in agg_positions if p.get("hold_ms") is not None]
    avg_hold_ms = int(sum(p["hold_ms"] for p in closed_positions) / len(closed_positions)) if closed_positions else 0

    long_stats  = direction_stats("Long")
    short_stats = direction_stats("Short")

    # 頂層平均盈虧（position 級，USD）
    all_win_pnls  = [v for (_, v) in decided if v > 0]
    all_loss_pnls = [v for (_, v) in decided if v < 0]
    avg_trade_win  = round(sum(all_win_pnls)  / len(all_win_pnls),  2) if all_win_pnls  else 0
    avg_trade_loss = round(sum(all_loss_pnls) / len(all_loss_pnls), 2) if all_loss_pnls else 0
    largest_gain   = round(max(all_win_pnls,  default=0), 2)
    largest_losses = round(min(all_loss_pnls, default=0), 2)

    # 最大連勝/連敗（按回合完成時間順序遍歷有勝負的 position）
    # agg_positions 是按 (market,symbol) 分組產出、非全域時間序，故先按 close_time 排序。
    streak_positions = sorted(
        decided, key=lambda pv: (pv[0].get("close_time") or pv[0].get("open_time") or 0)
    )
    max_consec_win = max_consec_loss = cur_win = cur_loss = 0
    for (_, v) in streak_positions:
        if v > 0:
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
            "largest_gain": largest_gain,
            "total_trades_volume": round(total_trades_volume, 2),
            "avg_trades_per_day": round(len(agg_positions) / time_span_days, 2),
            "avg_trade_win": avg_trade_win,
            "avg_trade_loss": avg_trade_loss,
            "largest_losses": largest_losses,
            "max_consecutive_win": max_consec_win,
            "max_consecutive_loss": max_consec_loss,
        },
        "longs": long_stats,
        "shorts": short_stats,
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
