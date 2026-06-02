#!/usr/bin/env python3
"""
common.py — Binance 交易记录抓取共享模块
被 backfill_binance.py 与 sync_binance.py 复用。

职责：
  - Binance client（含时钟同步，防 -1021）
  - 限速安全调用（节流 + -1003 重试）
  - fromId 正向分页（绕开 24h/7d 时间窗口限制）
  - 三市场字段归一化 → 统一 trades schema
  - Neon DB upsert（ON CONFLICT 去重）+ 水位线读取
  - symbol 发现（合约 income 自动发现 / 现货余额倒推 + 配置兜底）
  - Telegram 推送
"""
import os
import time
import requests
import psycopg2
from psycopg2.extras import execute_values
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ─── 配置 ────────────────────────────────────────────────────────────────────
EXCHANGE = "binance"
LIMIT = 1000                       # 单批最大条数
SPOT_QUOTE = "USDT"                # 现货只用 USDT 计价
SPOT_SEED_SYMBOLS = ["BTCUSDT", "ADAUSDT", "ETHUSDT"]  # 清仓也要抓的兜底清单
DUST_USD_THRESHOLD = 0.1           # USD 价值 < 此值的资产不计入余额倒推

_call_counter = [0]


# ─── Client ──────────────────────────────────────────────────────────────────
def make_client():
    """创建 client 并同步时钟，避免 -1021 timestamp 漂移。"""
    client = Client(os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"))
    server_time = client.get_server_time()["serverTime"]
    client.timestamp_offset = server_time - int(time.time() * 1000)
    return client


def safe_request(fn, **kwargs):
    """限速安全调用：每 10 次 sleep 0.3s；命中 -1003 等 5s 重试。"""
    while True:
        try:
            _call_counter[0] += 1
            if _call_counter[0] % 10 == 0:
                time.sleep(0.3)
            return fn(**kwargs)
        except BinanceAPIException as e:
            if e.code == -1003:
                print("    [RATE LIMIT -1003] wait 5s...")
                time.sleep(5)
                continue
            raise


def fetch_forward(fn, symbol, from_id):
    """
    用 fromId 正向分页抓取 symbol 的成交，从 from_id 开始一直到最新。
    返回原始 trade dict 列表（升序）。

    关键：fromId 模式不受 24h/7d 时间窗口限制，直接按 trade id 翻页。
    """
    out = []
    cur = from_id
    while True:
        batch = safe_request(fn, symbol=symbol, fromId=cur, limit=LIMIT)
        if not batch:
            break
        # fromId 是包含式（id >= cur），首批可能含已抓过的 cur 本身，靠 DB 去重兜底
        out.extend(batch)
        if len(batch) < LIMIT:
            break
        cur = batch[-1]["id"] + 1
    return out


# ─── 字段归一化 → 统一 schema ──────────────────────────────────────────────────
# 统一列顺序，与 DB INSERT 对齐
COLUMNS = [
    "exchange", "market", "symbol", "trade_id", "order_id", "side",
    "price", "qty_base", "quote_qty", "realized_pnl", "margin_asset",
    "position_side", "fee", "fee_asset", "is_maker", "trade_time",
]


def _f(v):
    return float(v) if v is not None else None


def normalize_spot(t):
    return (
        EXCHANGE, "spot", t["symbol"], int(t["id"]), int(t.get("orderId", 0)),
        "BUY" if t["isBuyer"] else "SELL",
        _f(t["price"]), _f(t["qty"]), _f(t.get("quoteQty")),
        None, None, None,                          # 现货无 realized_pnl/margin/position
        _f(t.get("commission")), t.get("commissionAsset"),
        bool(t["isMaker"]), int(t["time"]),
    )


def normalize_usdm(t):
    return (
        EXCHANGE, "usdm", t["symbol"], int(t["id"]), int(t.get("orderId", 0)),
        t["side"],
        _f(t["price"]), _f(t["qty"]), _f(t.get("quoteQty")),
        _f(t.get("realizedPnl")), t.get("marginAsset"), t.get("positionSide"),
        _f(t.get("commission")), t.get("commissionAsset"),
        bool(t["maker"]), int(t["time"]),
    )


def normalize_coinm(t):
    # COIN-M: qty 是合约张数，baseQty 才是币量；无 quoteQty
    return (
        EXCHANGE, "coinm", t["symbol"], int(t["id"]), int(t.get("orderId", 0)),
        t["side"],
        _f(t["price"]), _f(t.get("baseQty")), None,
        _f(t.get("realizedPnl")), t.get("marginAsset"), t.get("positionSide"),
        _f(t.get("commission")), t.get("commissionAsset"),
        bool(t["maker"]), int(t["time"]),
    )


# ─── DB ──────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5
    )


def get_watermark(conn, market, symbol):
    """返回 (exchange, market, symbol) 已入库的最大 trade_id；无数据返回 0。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(trade_id), 0) FROM trades "
            "WHERE exchange=%s AND market=%s AND symbol=%s",
            (EXCHANGE, market, symbol),
        )
        return cur.fetchone()[0]


def upsert_trades(conn, rows):
    """批量 upsert，ON CONFLICT(exchange,market,symbol,trade_id) 去重。返回实际新增行数。"""
    if not rows:
        return 0
    sql = (
        f"INSERT INTO trades ({','.join(COLUMNS)}) VALUES %s "
        "ON CONFLICT (exchange, market, symbol, trade_id) DO NOTHING"
    )
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
        inserted = cur.rowcount          # 实际写入数（已存在的被 ON CONFLICT 跳过）
    conn.commit()
    return inserted


# ─── symbol 发现 ──────────────────────────────────────────────────────────────
def discover_spot_symbols(client):
    """现货：(余额 USD 价值 >= 阈值的资产 × USDT) ∪ 种子清单，去重。"""
    symbols = set(SPOT_SEED_SYMBOLS)
    balances = client.get_account()["balances"]
    prices = {p["symbol"]: float(p["price"]) for p in client.get_all_tickers()}
    for b in balances:
        asset = b["asset"]
        total = float(b["free"]) + float(b["locked"])
        if total <= 0 or asset == SPOT_QUOTE:
            continue
        pair = f"{asset}{SPOT_QUOTE}"
        price = prices.get(pair)
        if price is None:
            continue                                   # 无 USDT 对，跳过
        if total * price < DUST_USD_THRESHOLD:
            continue                                   # 灰尘资产，过滤
        symbols.add(pair)
    return sorted(symbols)


def discover_futures_symbols(income_fn, lookback_ms=None):
    """
    合约：用 income 流水自动发现交易过的 symbol（income 不需要 symbol）。
    income 受 7 天窗口限制，回填时滑窗扫全程；增量时只看近窗。
    lookback_ms=None 表示扫满 1500 天（回填用）；否则只扫最近 lookback_ms（增量用）。
    """
    WINDOW = 7 * 24 * 3600 * 1000
    now = int(time.time() * 1000)
    start = now - (lookback_ms if lookback_ms else 1500 * 24 * 3600 * 1000)
    symbols = set()
    cur = now
    while cur > start:
        ws = max(start, cur - WINDOW)
        rows = safe_request(income_fn, startTime=ws, endTime=cur, limit=1000)
        for r in rows:
            if r.get("symbol"):
                symbols.add(r["symbol"])
        cur = ws - 1
    return sorted(symbols)


# ─── 资金费（funding）─────────────────────────────────────────────────────────
FUNDING_DDL = """
CREATE TABLE IF NOT EXISTS funding (
  id           SERIAL PRIMARY KEY,
  exchange     TEXT NOT NULL,
  market       TEXT NOT NULL,            -- usdm | coinm
  symbol       TEXT NOT NULL,
  income_id    BIGINT NOT NULL,          -- Binance tranId，去重锚
  amount       DOUBLE PRECISION,         -- 资金费金额（usdm:USDT, coinm:币本位）
  asset        TEXT,
  income_time  BIGINT NOT NULL,
  UNIQUE (exchange, market, income_id)
);
CREATE INDEX IF NOT EXISTS idx_funding_lookup ON funding (exchange, market, symbol, income_time);
"""


def fetch_funding(income_fn, lookback_ms=None):
    """
    用 income 接口拉 FUNDING_FEE 流水（与 discover_futures_symbols 同一接口）。
    income 受 7 天窗口限制：回填扫满 1500 天，增量只扫近窗。
    返回原始 income dict 列表。
    """
    WINDOW = 7 * 24 * 3600 * 1000
    now = int(time.time() * 1000)
    start = now - (lookback_ms if lookback_ms else 1500 * 24 * 3600 * 1000)
    out = []
    cur = now
    while cur > start:
        ws = max(start, cur - WINDOW)
        rows = safe_request(income_fn, incomeType="FUNDING_FEE",
                            startTime=ws, endTime=cur, limit=1000)
        out.extend(rows)
        cur = ws - 1
    return out


def normalize_funding(market, r):
    """income dict → funding 行（与 FUNDING_COLUMNS 对齐）。"""
    return (
        EXCHANGE, market, r.get("symbol") or "",
        int(r.get("tranId") or r.get("tradeId") or 0),
        _f(r.get("income")), r.get("asset"),
        int(r.get("time")),
    )


FUNDING_COLUMNS = ["exchange", "market", "symbol", "income_id",
                   "amount", "asset", "income_time"]


def upsert_funding(conn, rows):
    """ON CONFLICT(exchange,market,income_id) DO NOTHING。返回新增行数。"""
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.execute(FUNDING_DDL)
        sql = (
            f"INSERT INTO funding ({','.join(FUNDING_COLUMNS)}) VALUES %s "
            "ON CONFLICT (exchange, market, income_id) DO NOTHING"
        )
        execute_values(cur, sql, rows)
        n = cur.rowcount
    conn.commit()
    return n


# ─── 账户余额快照（balances）────────────────────────────────────────────────────
BALANCES_DDL = """
CREATE TABLE IF NOT EXISTS balances (
  id             SERIAL PRIMARY KEY,
  exchange       TEXT NOT NULL,
  market         TEXT NOT NULL,           -- spot | usdm | coinm
  asset          TEXT NOT NULL,
  free           DOUBLE PRECISION,
  locked         DOUBLE PRECISION,
  balance        DOUBLE PRECISION,        -- 钱包总额（free+locked / walletBalance）
  snapshot_time  BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_balances_latest ON balances (exchange, market, snapshot_time);
"""


def snapshot_balances(client, conn):
    """
    抓取三市场账户余额快照，存入 balances。返回写入行数。
    过滤掉余额为 0 的资产。
    """
    now = int(time.time() * 1000)
    rows = []

    # 现货
    for b in safe_request(client.get_account)["balances"]:
        free, locked = float(b["free"]), float(b["locked"])
        if free + locked <= 0:
            continue
        rows.append((EXCHANGE, "spot", b["asset"], free, locked, free + locked, now))

    # USD-M
    for b in safe_request(client.futures_account_balance):
        wb = float(b.get("balance", 0))
        if wb == 0:
            continue
        rows.append((EXCHANGE, "usdm", b["asset"], None, None, wb, now))

    # COIN-M
    for b in safe_request(client.futures_coin_account_balance):
        wb = float(b.get("balance", 0))
        if wb == 0:
            continue
        rows.append((EXCHANGE, "coinm", b["asset"], None, None, wb, now))

    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.execute(BALANCES_DDL)
        sql = ("INSERT INTO balances "
               "(exchange,market,asset,free,locked,balance,snapshot_time) VALUES %s")
        execute_values(cur, sql, rows)
        n = cur.rowcount
    conn.commit()
    return n


# ─── Telegram ─────────────────────────────────────────────────────────────────
def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] TOKEN/CHAT_ID 未配置，跳过推送")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[telegram] 推送失败: {e}")
