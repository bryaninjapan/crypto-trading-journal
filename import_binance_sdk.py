#!/usr/bin/env python3
"""
Binance 数据导入 - Spot + USD-M + Coin-M 期货（诊断版）
每一步都打印 API 响应，便于诊断问题
"""

import os
import time
import sys
import psycopg2
from psycopg2.extras import execute_batch
from binance.client import Client
from binance.exceptions import BinanceAPIException

# 支持本地和 VM 环境
ENV_FILE = ".env" if os.path.exists(".env") else "/home/ubuntu/trading-journal/.env"

def load_env():
    """读取 .env 文件（不用 load_dotenv，避免环境污染）"""
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                key, val = line.strip().split("=", 1)
                env[key] = val
    return env

class DataProcessor:
    def __init__(self, db_url):
        self.db_url = db_url
        self.conn = None
        self.trades_buffer = []
        self.import_stats = {
            'spot': {'symbols': {}, 'total': 0},
            'usdm': {'symbols': {}, 'total': 0},
            'coinm': {'symbols': {}, 'total': 0},
        }

    def connect(self):
        try:
            self.conn = psycopg2.connect(self.db_url)
            print("[✓] PostgreSQL 连接成功")
        except Exception as e:
            print(f"[✗] PostgreSQL 连接失败: {e}")
            raise

    def clear_trades(self):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM trades")
        self.conn.commit()
        print("[✓] 已清空旧数据")

    def insert_trade(self, trade_dict, market):
        """插入一条交易，带诊断"""
        # COINM quote_qty 可能是 None，计算为 price × qty_base
        quote_qty = trade_dict.get('quoteQty') or trade_dict.get('quote_qty')
        if quote_qty is None:
            try:
                quote_qty = float(trade_dict.get('price', 0)) * float(trade_dict.get('qty', trade_dict.get('baseQty', 0)))
            except:
                quote_qty = 0
        else:
            quote_qty = float(quote_qty)

        self.trades_buffer.append((
            'binance',  # exchange
            market,
            trade_dict.get('symbol'),
            trade_dict.get('id'),  # trade_id
            trade_dict.get('orderId'),
            trade_dict.get('side'),
            float(trade_dict.get('price', 0)),
            float(trade_dict.get('qty', trade_dict.get('baseQty', 0))),
            quote_qty,
            float(trade_dict.get('realizedPnl', 0)),
            trade_dict.get('marginAsset'),
            trade_dict.get('positionSide'),
            float(trade_dict.get('commission', 0)),
            trade_dict.get('commissionAsset'),
            trade_dict.get('isMaker', trade_dict.get('maker', False)),
            int(trade_dict.get('time', 0)),
        ))

    def flush(self, market, symbol):
        if not self.trades_buffer:
            return

        try:
            cur = self.conn.cursor()
            sql = """
                INSERT INTO trades
                (exchange, market, symbol, trade_id, order_id, side, price, qty_base,
                 quote_qty, realized_pnl, margin_asset, position_side, fee, fee_asset, is_maker, trade_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (exchange, market, symbol, trade_id) DO NOTHING
            """
            execute_batch(cur, sql, self.trades_buffer, page_size=500)
            self.conn.commit()
            count = len(self.trades_buffer)
            print(f"    ✓ 插入 {count} 条到 DB")
            self.import_stats[market]['symbols'][symbol] = count
            self.import_stats[market]['total'] += count
            self.trades_buffer = []
        except Exception as e:
            print(f"    ✗ DB 插入失败: {e}")
            self.trades_buffer = []

    def close(self):
        if self.conn:
            self.conn.close()

    def print_stats(self):
        print("\n" + "="*70)
        print("导入统计：")
        for market in ['spot', 'usdm', 'coinm']:
            stats = self.import_stats[market]
            print(f"\n{market.upper()}:")
            for symbol, count in stats['symbols'].items():
                print(f"  {symbol}: {count}")
            print(f"  小计: {stats['total']}")
        total = sum(m['total'] for m in self.import_stats.values())
        print(f"\n全部: {total} 条")
        print("="*70)


def fetch_spot_trades(client, symbol):
    """Spot 现货交易"""
    endpoint = "GET /api/v3/myTrades"
    try:
        trades = client.get_my_trades(symbol=symbol, limit=1000)
        print(f"  {symbol:10} {endpoint:30} → {len(trades):4} 条")
        return trades
    except BinanceAPIException as e:
        print(f"  {symbol:10} {endpoint:30} → ✗ [{e.code}] {e.message}")
        return []
    except Exception as e:
        print(f"  {symbol:10} {endpoint:30} → ✗ {type(e).__name__}: {e}")
        return []


def discover_usdm_symbols(client, start_ms):
    """
    Step A3: 用 income history 發現每個符號的最早交易時間。
    返回 dict {symbol: earliest_ms}
    注意：Binance /fapi/v1/userTrades startTime 只返回後 7 天窗口，
    因此每個符號必須用自己的 earliest income time 作 startTime。
    """
    print(f"  [discovery] 查詢 USDM income history...")
    symbol_earliest = {}  # sym -> earliest income time
    try:
        inc = client.futures_income_history(
            incomeType='REALIZED_PNL', limit=1000, startTime=start_ms
        )
        for item in inc:
            sym = item.get('symbol', '').strip()
            t = item.get('time', 0)
            if sym and t:
                if sym not in symbol_earliest or t < symbol_earliest[sym]:
                    symbol_earliest[sym] = t
        from datetime import datetime
        print(f"  [discovery] 找到 {len(symbol_earliest)} 個 USDM 符號: {sorted(symbol_earliest.keys())}")
        for sym, t in sorted(symbol_earliest.items()):
            print(f"    {sym}: earliest={datetime.fromtimestamp(t/1000)}")
    except Exception as e:
        print(f"  [discovery] ✗ income history 失敗: {e}")
    return symbol_earliest


def discover_coinm_symbols(client, start_ms):
    """用 COINM income history 發現每個符號的最早交易時間。"""
    print(f"  [discovery] 查詢 COINM income history...")
    symbol_earliest = {}
    try:
        inc = client.futures_coin_income_history(
            incomeType='REALIZED_PNL', limit=1000, startTime=start_ms
        )
        for item in inc:
            sym = item.get('symbol', '').strip()
            t = item.get('time', 0)
            if sym and t:
                if sym not in symbol_earliest or t < symbol_earliest[sym]:
                    symbol_earliest[sym] = t
        print(f"  [discovery] 找到 {len(symbol_earliest)} 個 COINM 符號: {sorted(symbol_earliest.keys())}")
    except Exception as e:
        print(f"  [discovery] COINM income history: {e} (可能無歷史)")
    return symbol_earliest


def fetch_usdm_trades(client, symbol, start_ms=None):
    """USDM 期货交易（U本位）— 支援 startTime 抓歷史"""
    endpoint = "GET /fapi/v1/userTrades"
    try:
        kwargs = {"symbol": symbol, "limit": 1000}
        if start_ms:
            kwargs["startTime"] = start_ms
        trades = client.futures_account_trades(**kwargs)
        print(f"  {symbol:20} {endpoint:30} → {len(trades):4} 条")
        return trades
    except BinanceAPIException as e:
        print(f"  {symbol:20} {endpoint:30} → ✗ [{e.code}] {e.message}")
        return []
    except Exception as e:
        print(f"  {symbol:20} {endpoint:30} → ✗ {type(e).__name__}: {e}")
        return []


def fetch_coinm_trades(client, symbol, start_ms=None):
    """COINM 期货交易（币本位）— 支援 startTime 抓歷史"""
    endpoint = "GET /dapi/v1/userTrades"
    try:
        kwargs = {"symbol": symbol, "limit": 1000}
        if start_ms:
            kwargs["startTime"] = start_ms
        trades = client.futures_coin_account_trades(**kwargs)
        print(f"  {symbol:20} {endpoint:30} → {len(trades):4} 条")
        return trades
    except BinanceAPIException as e:
        print(f"  {symbol:20} {endpoint:30} → ✗ [{e.code}] {e.message}")
        return []
    except Exception as e:
        print(f"  {symbol:20} {endpoint:30} → ✗ {type(e).__name__}: {e}")
        return []


def main():
    print("="*70)
    print("Binance 数据导入 - 诊断版本")
    print("="*70)

    # 读取环境变量
    env = load_env()
    api_key = env.get('BINANCE_API_KEY')
    api_secret = env.get('BINANCE_SECRET')
    db_url = env.get('DATABASE_URL')

    if not api_key or not api_secret or not db_url:
        print(f"[✗] 环境变量不完整：")
        print(f"  BINANCE_API_KEY: {'✓' if api_key else '✗'}")
        print(f"  BINANCE_SECRET: {'✓' if api_secret else '✗'}")
        print(f"  DATABASE_URL: {'✓' if db_url else '✗'}")
        sys.exit(1)

    # 初始化 Binance 客户端
    print("\n[初始化] Binance 客户端...")
    client = Client(api_key, api_secret)

    # 同步时钟
    try:
        server_time = client.get_server_time()['serverTime']
        client.timestamp_offset = server_time - int(time.time() * 1000)
        print(f"  ✓ 时钟同步 (offset={client.timestamp_offset}ms)")
    except Exception as e:
        print(f"  ✗ 时钟同步失败: {e}")
        return

    # 初始化数据库
    processor = DataProcessor(db_url)
    try:
        processor.connect()
        processor.clear_trades()
    except Exception as e:
        print(f"[✗] 初始化失败: {e}")
        return

    # Step A3: 自動發現符號 + 每個符號用自己的 income 時間作為 startTime
    import datetime
    DISCOVERY_START = int(datetime.datetime(2024, 1, 1).timestamp() * 1000)

    # Spot：保留硬編碼（spot 無 income discovery）
    spot_symbols = ['BTCUSDT', 'ADAUSDT', 'ETHUSDT', 'BNBUSDT', 'ETHBTC']

    # USDM：{symbol: earliest_income_ms}
    usdm_symbol_times = discover_usdm_symbols(client, DISCOVERY_START)
    # 補充保底（這些符號在 income 中可能沒出現但值得查）
    for sym in ['BTCUSDT', 'ETHUSDT', 'ADAUSDT']:
        if sym not in usdm_symbol_times:
            usdm_symbol_times[sym] = DISCOVERY_START

    # COINM：{symbol: earliest_income_ms}
    coinm_symbol_times = discover_coinm_symbols(client, DISCOVERY_START)
    # COINM fallback：income history 為空時用近 60 天（讓 Binance 7 天窗口能抓到最近交易）
    COINM_RECENT_START = int((time.time() - 60 * 86400) * 1000)
    for sym in ['BTCUSD_PERP', 'ADAUSD_PERP', 'ETHUSD_PERP']:
        if sym not in coinm_symbol_times:
            coinm_symbol_times[sym] = COINM_RECENT_START

    print(f"\nUSDM 將抓 {len(usdm_symbol_times)} 個符號（每個用自己的 earliest income 時間）")
    print(f"COINM 將抓 {len(coinm_symbol_times)} 個符號")

    # 导入 Spot
    print("\n[导入] Spot (GET /api/v3/myTrades):")
    for symbol in spot_symbols:
        trades = fetch_spot_trades(client, symbol)
        for trade in trades:
            processor.insert_trade({
                'symbol': symbol,
                'id': trade['id'],
                'orderId': trade['orderId'],
                'side': 'BUY' if trade['isBuyer'] else 'SELL',
                'price': trade['price'],
                'qty': trade['qty'],
                'quoteQty': trade['quoteQty'],
                'realizedPnl': 0,
                'marginAsset': None,
                'positionSide': None,
                'commission': trade['commission'],
                'commissionAsset': trade['commissionAsset'],
                'isMaker': trade['isMaker'],
                'time': trade['time'],
            }, 'spot')
        processor.flush('spot', symbol)
        time.sleep(0.1)

    # 导入 USDM（每個符號用自己的 income 最早時間前推 1 天）
    print("\n[导入] USDM (GET /fapi/v1/userTrades):")
    for symbol, earliest_income_ms in sorted(usdm_symbol_times.items()):
        sym_start = max(DISCOVERY_START, earliest_income_ms - 86400000)
        trades = fetch_usdm_trades(client, symbol, start_ms=sym_start)
        for trade in trades:
            processor.insert_trade({
                'symbol': symbol,
                'id': trade['id'],
                'orderId': trade.get('orderId'),
                'side': trade['side'],
                'price': trade['price'],
                'qty': trade['qty'],
                'quoteQty': float(trade['price']) * float(trade['qty']),
                'realizedPnl': float(trade.get('realizedPnl', 0)),
                'marginAsset': 'USDT',
                'positionSide': trade.get('positionSide'),
                'commission': trade.get('commission', 0),
                'commissionAsset': trade.get('commissionAsset'),
                'maker': trade.get('maker', False),
                'time': trade['time'],
            }, 'usdm')
        processor.flush('usdm', symbol)
        time.sleep(0.2)

    # 导入 COINM（每個符號用自己的 income 最早時間前推 1 天）
    print("\n[导入] COINM (GET /dapi/v1/userTrades):")
    for symbol, earliest_income_ms in sorted(coinm_symbol_times.items()):
        sym_start = max(DISCOVERY_START, earliest_income_ms - 86400000)
        trades = fetch_coinm_trades(client, symbol, start_ms=sym_start)
        for trade in trades:
            processor.insert_trade({
                'symbol': symbol,
                'id': trade['id'],
                'orderId': trade.get('orderId'),
                'side': trade['side'],
                'price': trade['price'],
                # COIN-M（反向合约）: baseQty=币量、qty=合约张数。
                #   qty_base  ← baseQty（币量，realized_pnl 同单位）
                #   quote_qty ← qty（合约张数）：净部位配对的「部位量」是张数，不是币量
                #     （见 api.py::_qty_unit / normalize_coinm）。若留 None，insert_trade 会
                #     回退成 price×币量=USD notional，张数遗失 → 聚合失效。
                'qty': trade.get('baseQty', trade.get('qty', 0)),
                'quoteQty': trade.get('qty'),
                'realizedPnl': float(trade.get('realizedPnl', 0)),
                'marginAsset': trade.get('marginAsset'),
                'positionSide': trade.get('positionSide'),
                'commission': trade.get('commission', 0),
                'commissionAsset': trade.get('commissionAsset'),
                'maker': trade.get('maker', False),
                'time': trade['time'],
            }, 'coinm')
        processor.flush('coinm', symbol)
        time.sleep(0.2)

    processor.print_stats()
    processor.close()
    print("\n[✓] 导入完成")


if __name__ == '__main__':
    main()
