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


# ─── Position Aggregation (fills → trades，淨部位配對) ─────────────────────────
# 模型（對標使用者驗證過的 process_trades_v7）：每個平倉「事件」吸收它依時間序消耗的
# 反向開倉事件 = 一筆已平交易。平倉錨 = realized_pnl != 0（Binance 只在平倉結算 PNL）。
# 詳見 build_positions_from_fills docstring。
#
# 關鍵單位（曾是 2x 顆粒度與幽靈未平倉的根因）：
#   COIN-M 反向合約的部位量是「合約張數」(quote_qty)，不是幣量(qty_base)——幣量在開/平
#   價不同時不守恆（開空 284 張 @0.825 = 3442 幣，平回 284 張 @0.235 = 14650 幣），會使
#   配對碎裂並造出假未平倉殘量。USD-M 線性合約則 qty_base 即張數=幣量。見 _qty_unit。
# 方向：開倉 BUY→Long / SELL→Short；平倉吸收的反向開倉決定該筆方向。


def _seg_float(v):
    return float(v) if v is not None else 0.0


def _qty_unit(f):
    """部位數量單位：COIN-M 反向合約以「合約張數」(quote_qty) 計，USD-M 線性合約以
    qty_base（=幣量=張數）。COIN-M 用幣量(qty_base) 會使開平不守恆、配對碎裂 —— 這是
    舊版 build_positions_from_fills 2x 顆粒度與幽靈未平倉的根因（見 DECISION-LOG）。"""
    v = f.get("quote_qty") if f.get("market") == "coinm" else f.get("qty_base")
    return _seg_float(v)


def _build_position(market, symbol, book, consumed, close_ev, is_orphan=False):
    """把「一個平倉事件 + 它消耗的開倉事件們」組成一筆已平交易（對標 v7：一個平倉事件
    = 一筆 trade，多個被消耗的開倉合併成加權均價的單一進場）。

    consumed = [(open_event, taken_qty), ...]（orphan close 時為空）。
    realized_pnl/fee 取該平倉事件的全額（每個平倉事件只產生一筆交易，故全資料集守恆）；
    開倉手續費按被消耗比例分攤。id = 平倉事件首筆 fill 的 row id * 1000（detail 可反推）。
    """
    EPS = 1e-9
    close_time = close_ev["time"]
    close_fill0 = close_ev["fills"][0]

    if consumed:
        tot   = sum(t for _e, t in consumed)
        entry = (sum(e["price"] * t for e, t in consumed) / tot) if tot > EPS else 0.0
        open_ev0      = consumed[0][0]
        open_time     = open_ev0["time"]
        open_trade_id = open_ev0["fills"][0].get("trade_id")
        margin_asset  = (open_ev0["fills"][0].get("margin_asset")
                         or close_fill0.get("margin_asset"))
        open_fee      = sum(e["fee"] * (t / e["qty"]) for e, t in consumed if e["qty"] > EPS)
        open_fills    = [rf for e, _t in consumed for rf in e["fills"]]
        qty           = tot
        avg_entry     = round(entry, 6)
    else:
        open_time     = close_time          # orphan close：無開倉事件，退回平倉時間
        open_trade_id = close_fill0.get("trade_id")
        margin_asset  = close_fill0.get("margin_asset")
        open_fee      = 0.0
        open_fills    = []
        qty           = close_ev["qty"]
        avg_entry     = None

    # pnl_asset = realized_pnl / funding 的計價幣。本資料集 margin_asset 全 NULL，故 COIN-M
    # 需由 symbol 反推標的幣（ADAUSD_PERP→ADA、BTCUSD_PERP→BTC），否則會誤標成 USDT、
    # 使 list/detail 的 realized_pnl_usd / funding_usd 不換算（COIN-M 顆數被當 USDT）。
    if margin_asset:
        pnl_asset = margin_asset
    elif market == "coinm":
        pnl_asset = symbol.split("USD")[0]
    else:
        pnl_asset = "USDT"
    fee_asset = close_fill0.get("fee_asset") or pnl_asset
    seg_fills = sorted(open_fills + close_ev["fills"],
                       key=lambda f: (f.get("trade_time", 0), f.get("id", 0)))

    return {
        "id":              close_fill0.get("id", 0) * 1000,
        "market":          market,
        "symbol":          symbol,
        "direction":       "Short" if book == "SHORT" else "Long",
        "open_trade_id":   open_trade_id,
        "open_time":       open_time,
        "close_time":      close_time,
        "hold_ms":         close_time - open_time,
        "qty":             round(qty, 8),
        "avg_entry":       avg_entry,
        "avg_exit":        round(close_ev["price"], 6),
        "realized_pnl":    round(close_ev["pnl"], 6),
        "pnl_asset":       pnl_asset,
        "is_estimated":    False,
        "is_orphan_close": is_orphan,
        "fees":            round(open_fee + close_ev["fee"], 8),
        "fee_asset":       fee_asset,
        "funding":         0.0,
        "num_fills":       len(seg_fills),
        "fills":           seg_fills,
    }


def _attach_funding(positions, income_rows):
    """把 FUNDING_FEE 流水歸進各 position 的 funding（單位同 pnl_asset：COIN-M 標的幣、USD-M USDT）。

    歸屬規則：每筆 funding 指派給同 symbol「close_time >= funding.trade_time 的最早平倉交易」
    （= 該筆資金費由其後第一筆平倉認列）。如此每筆 funding 只算一次、Σfunding 守恆，且不會
    因 per-平倉事件顆粒度的重疊時間窗而重複計。發生在該 symbol 最後平倉之後的 funding（仍持倉）
    無人認領 → 略過（屬未平倉部位，本表本就不顯示）。
    """
    import bisect
    from collections import defaultdict
    if not income_rows:
        return
    by_sym = defaultdict(list)
    for p in positions:
        if p.get("close_time") is not None:
            by_sym[p["symbol"]].append(p)
    closes = {}
    for sym, plist in by_sym.items():
        plist.sort(key=lambda p: p["close_time"])
        closes[sym] = [p["close_time"] for p in plist]
    for r in income_rows:
        sym = r.get("symbol")
        plist = by_sym.get(sym)
        if not plist:
            continue
        t = int(r.get("trade_time") or 0)
        i = bisect.bisect_left(closes[sym], t)
        if i < len(plist):
            plist[i]["funding"] = round((plist[i].get("funding") or 0.0) + float(r.get("amount") or 0), 8)


def _load_funding(symbol=None):
    """讀 binance_income 的 FUNDING_FEE 流水（symbol 給定則只取該 symbol）。表不存在則回空。"""
    try:
        if symbol:
            return rows("SELECT symbol, amount, trade_time FROM binance_income "
                        "WHERE trans_type='FUNDING_FEE' AND symbol=%s", (symbol,))
        return rows("SELECT symbol, amount, trade_time FROM binance_income "
                    "WHERE trans_type='FUNDING_FEE'")
    except Exception:
        return []


def build_positions_from_fills(fills, income_rows=None):
    """將 fills 依 (market, symbol) 分組，用「淨部位配對」把每個平倉事件吸收它消耗的
    開倉事件，產生一筆已平交易（closed trade）。對標使用者驗證過的 process_trades_v7。

    流程（每個 symbol）：
      1. 事件化：同 (trade_time, side, is_close) 的 raw fill 併成一個事件（加權均價、
         Σqty、Σpnl、Σfee）。is_close 以 realized_pnl != 0 為錨（Binance 只在平倉結算 PNL）。
         配對單位是「事件」不是 raw fill —— 交易所把一張單拆成多筆同毫秒 fill。
      2. 數量單位：COIN-M 用合約張數(quote_qty)、USD-M 用 qty_base（見 _qty_unit）。
      3. 淨部位配對：每個平倉事件依時間序消耗最早的反向開倉事件；一個平倉事件 = 一筆
         交易（消耗的多個開倉合併成加權均價進場）。一張大開倉被多次平倉 → 拆成多筆
         交易（每次平倉一筆），這是正確顆粒度，取代舊 FIFO「每 lot×平倉一段」的 2x 碎裂。

    每筆交易：
      - open_time = 第一個被消耗開倉事件的時間；close_time = 平倉事件時間。
      - qty = 被消耗開倉量合計（COIN-M 為張數）；avg_entry/avg_exit = 量加權均價。
      - realized_pnl = 平倉事件全額 PNL（每事件只記一次 → 全資料集守恆）。
      - id = 平倉事件首筆 fill 的 row id * 1000（detail 端點可反推 (market, symbol)）。

    殘量處理：
      - 平倉事件無對應反向開倉可消耗 = orphan close（窗口前就開），仍記一筆
        （is_orphan_close=True）避免丟 PNL。
      - 視窗結束佇列仍有開倉殘量 = 未平倉部位，不產生 closed trade（無 open position UI）。
    """
    from collections import defaultdict, OrderedDict

    groups = defaultdict(list)
    for f in fills:
        groups[(f.get("market"), f.get("symbol"))].append(f)

    EPS = 1e-9
    positions = []

    for (market, symbol), group_fills in groups.items():
        group_fills = sorted(group_fills, key=lambda x: (x.get("trade_time", 0), x.get("id", 0)))

        # ── 1+2. 事件化（同 time/side/is_close 併一筆，量用市場對應單位）──
        ev_map = OrderedDict()
        for f in group_fills:
            is_close = abs(_seg_float(f.get("realized_pnl"))) > 1e-12
            key = (int(f.get("trade_time", 0)), f.get("side"), is_close)
            ev = ev_map.get(key)
            if ev is None:
                ev = {"time": key[0], "side": key[1], "is_close": is_close,
                      "qty": 0.0, "pxw": 0.0, "pnl": 0.0, "fee": 0.0, "fills": []}
                ev_map[key] = ev
            q = _qty_unit(f)
            ev["qty"] += q
            ev["pxw"] += q * _seg_float(f.get("price"))
            ev["pnl"] += _seg_float(f.get("realized_pnl"))
            ev["fee"] += _seg_float(f.get("fee"))
            ev["fills"].append(f)
        events = sorted(ev_map.values(),
                        key=lambda e: (e["time"], e["fills"][0].get("id", 0)))
        for e in events:
            e["price"] = (e["pxw"] / e["qty"]) if e["qty"] > EPS else 0.0

        # ── 3. 淨部位配對 ──
        open_entries = []  # [{side, remaining, event}]
        for ev in events:
            if not ev["is_close"]:
                if ev["qty"] > EPS:
                    open_entries.append({"side": ev["side"], "remaining": ev["qty"], "event": ev})
                continue
            opp = "SELL" if ev["side"] == "BUY" else "BUY"
            need = ev["qty"]
            consumed = []
            for oe in open_entries:
                if oe["side"] == opp and need > EPS and oe["remaining"] > EPS:
                    take = min(need, oe["remaining"])
                    need -= take
                    oe["remaining"] -= take
                    consumed.append((oe["event"], take))
            open_entries = [oe for oe in open_entries if oe["remaining"] > EPS]
            if consumed:
                book = "LONG" if consumed[0][0]["side"] == "BUY" else "SHORT"
                positions.append(_build_position(market, symbol, book, consumed, ev))
            elif need > EPS:
                # orphan close：無對應開倉可消耗，仍記一筆（方向由平倉 side 反推）
                book = "SHORT" if ev["side"] == "BUY" else "LONG"
                positions.append(_build_position(market, symbol, book, [], ev, is_orphan=True))

        # 佇列殘餘 = 未平倉，刻意不產生 closed trade

    # 傳入 income 流水時，把 FUNDING_FEE 歸進各 position 的 funding（不傳則 funding 維持 0.0）。
    _attach_funding(positions, income_rows)
    return positions


# ─── /api/summary ──────────────────────────────────────────────────────────────
@app.get("/api/summary", dependencies=[Depends(auth)])
def summary():
    # 从数据库查询真实数据
    trades = rows("SELECT * FROM trades ORDER BY trade_time ASC, id ASC")

    # 用聚合後 positions 計算 total_positions 和 win_rate（Step 5 fix）
    futures_fills = [t for t in trades if t.get("market") in ("usdm", "coinm")]
    agg_positions = build_positions_from_fills(futures_fills, _load_funding())
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
    all_positions = build_positions_from_fills(all_fills, _load_funding())

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
        p["funding_usd"] = _pnl_to_usd(p.get("funding"), p.get("pnl_asset"), prices)

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
    positions = build_positions_from_fills(same_symbol, _load_funding(anchor["symbol"]))
    target = next((p for p in positions if p["id"] == trade_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Position not found")

    target_fills = target.pop("fills", [])

    # COIN-M 的 realized_pnl 以标的币计价，附带 USD 估值供前端并排显示。
    prices = _load_prices()
    target["realized_pnl_usd"] = _pnl_to_usd(
        target.get("realized_pnl"), target.get("pnl_asset"), prices
    )
    target["funding_usd"] = _pnl_to_usd(target.get("funding"), target.get("pnl_asset"), prices)

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
    #      多頭回合。現已改為以 realized_pnl != 0 為平倉錨的淨部位配對（見 build_positions_from_fills）。
    # 方向一律用 position 的 direction，勝負一律看 position 淨 realized_pnl 正負。
    # 註：此處不需 per-position funding，故不傳 income（funding 維持 0，不影響 pnl 統計）。
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

    # 成交量（USD notional）：
    #   USD-M  → quote_qty 即 USDT notional。
    #   COIN-M → quote_qty 是「合約張數」(非 USD，見 _qty_unit / normalize_coinm)，
    #            USD notional = price(USD/幣) × qty_base(幣量)。
    def _notional(t):
        if t.get("market") == "coinm":
            return abs(float(t.get("price", 0)) * float(t.get("qty_base", 0)))
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
