#!/usr/bin/env python3
"""
Binance 数据导入 - Spot + USD-M + Coin-M 期货
使用 python-binance SDK，支持 fromId 分页
"""

import os
import time
import psycopg2
from psycopg2.extras import execute_batch
from binance.client import Client
from binance.exceptions import BinanceAPIException

ENV_FILE = "/home/ubuntu/trading-journal/.env"

def load_env():
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
        cur.execute("DELETE FROM positions")
        self.conn.commit()
        print("[✓] 已清空旧数据")

    def insert_trade(self, trade_dict):
        # COINM quote_qty 可能是 None，计算为 price × qty_base
        quote_qty = trade_dict.get('quote_qty')
        if quote_qty is None:
            quote_qty = float(trade_dict.get('price', 0)) * float(trade_dict.get('qty_base', 0))
        else:
            quote_qty = float(quote_qty)

        self.trades_buffer.append((
            'binance',  # exchange
            trade_dict.get('market'),
            trade_dict.get('symbol'),
            trade_dict.get('id'),  # trade_id
            trade_dict.get('orderId'),
            trade_dict.get('side'),
            float(trade_dict.get('price', 0)),
            float(trade_dict.get('qty_base', 0)),
            quote_qty,
            float(trade_dict.get('realized_pnl', 0)),
            trade_dict.get('margin_asset'),
            trade_dict.get('position_side'),
            float(trade_dict.get('fee', 0)),
            trade_dict.get('fee_asset'),
            trade_dict.get('is_maker', False),
            int(trade_dict.get('time', 0)),
        ))

    def flush(self):
        if not self.trades_buffer:
            return

        cur = self.conn.cursor()
        sql = """
            INSERT INTO trades
            (exchange, market, symbol, trade_id, order_id, side, price, qty_base,
             quote_qty, realized_pnl, margin_asset, position_side, fee, fee_asset, is_maker, trade_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trade_id) DO NOTHING
        """
        execute_batch(cur, sql, self.trades_buffer, page_size=500)
        self.conn.commit()
        print(f"  [✓] 已插入 {len(self.trades_buffer)} 条交易到数据库")
        self.trades_buffer = []

    def close(self):
        if self.conn:
            self.conn.close()

def fetch_all_spot_trades(client, symbol):
    """
    使用 python-binance SDK 获取现货全历史交易
    SDK 自动处理 fromId 分页和签名
    """
    print(f"  {symbol}...", end=" ", flush=True)

    all_trades = []
    from_id = None
    batch_count = 0

    try:
        while True:
            batch_count += 1

            # python-binance 的 get_my_trades 支持 fromId
            if from_id:
                trades = client.get_my_trades(symbol=symbol, fromId=from_id, limit=1000)
            else:
                trades = client.get_my_trades(symbol=symbol, limit=1000)

            if not trades:
                break

            all_trades.extend(trades)

            if len(trades) == 1000:
                # 本批满 1000，说明还有更多数据
                from_id = trades[-1]['id'] + 1
            else:
                # 本批不满 1000，说明已到末尾
                break

            # 每 5 批打印一次进度
            if batch_count % 5 == 0:
                print(f"({len(all_trades)})", end=" ", flush=True)

            time.sleep(0.1)  # 限速

        print(f"✓ ({len(all_trades)} 条)")
        return all_trades

    except BinanceAPIException as e:
        if e.code == -1003:
            print(f"✗ (限速，跳过)")
            return []
        elif e.code == -2015:
            print(f"✗ (权限不足，跳过)")
            return []
        else:
            print(f"✗ (错误 {e.code}: {e.message})")
            return []


def fetch_all_usdm_trades(client, symbol):
    """获取 USD-M 期货全历史交易"""
    print(f"  {symbol}...", end=" ", flush=True)

    all_trades = []
    from_id = None
    batch_count = 0

    try:
        while True:
            batch_count += 1

            if from_id:
                trades = client.futures_account_trades(symbol=symbol, fromId=from_id, limit=1000)
            else:
                trades = client.futures_account_trades(symbol=symbol, limit=1000)

            if not trades:
                break

            all_trades.extend(trades)

            if len(trades) == 1000:
                from_id = trades[-1]['id'] + 1
            else:
                break

            if batch_count % 5 == 0:
                print(f"({len(all_trades)})", end=" ", flush=True)

            time.sleep(0.1)

        print(f"✓ ({len(all_trades)} 条)")
        return all_trades

    except BinanceAPIException as e:
        if e.code == -1003:
            print(f"✗ (限速，跳过)")
            return []
        else:
            print(f"✗ (错误 {e.code}，跳过)")
            return []
    except Exception:
        print(f"✗ (交易对不存在或无权限，跳过)")
        return []


def fetch_all_coinm_trades(client, symbol):
    """获取 Coin-M 期货全历史交易"""
    print(f"  {symbol}...", end=" ", flush=True)

    all_trades = []
    from_id = None
    batch_count = 0

    try:
        while True:
            batch_count += 1

            if from_id:
                trades = client.futures_coin_account_trades(symbol=symbol, fromId=from_id, limit=1000)
            else:
                trades = client.futures_coin_account_trades(symbol=symbol, limit=1000)

            if not trades:
                break

            all_trades.extend(trades)

            if len(trades) == 1000:
                from_id = trades[-1]['id'] + 1
            else:
                break

            if batch_count % 5 == 0:
                print(f"({len(all_trades)})", end=" ", flush=True)

            time.sleep(0.1)

        print(f"✓ ({len(all_trades)} 条)")
        return all_trades

    except BinanceAPIException as e:
        if e.code == -1003:
            print(f"✗ (限速，跳过)")
            return []
        else:
            print(f"✗ (错误 {e.code}，跳过)")
            return []
    except Exception:
        print(f"✗ (交易对不存在或无权限，跳过)")
        return []

def main():
    print("=" * 70)
    print("Binance 数据导入 — python-binance SDK")
    print("=" * 70)

    env = load_env()
    api_key = env.get('BINANCE_API_KEY')
    api_secret = env.get('BINANCE_SECRET')
    db_url = env.get('DATABASE_URL')

    # 初始化 Binance 客户端
    client = Client(api_key, api_secret)

    # 同步时钟
    try:
        print("\n[初始化] 同步 Binance 时钟...", end=" ", flush=True)
        server_time = client.get_server_time()['serverTime']
        client.timestamp_offset = server_time - int(time.time() * 1000)
        print(f"✓ (offset={client.timestamp_offset}ms)")
    except Exception as e:
        print(f"✗ {e}")
        return

    # 初始化数据库
    processor = DataProcessor(db_url)
    try:
        processor.connect()
        processor.clear_trades()
    except Exception as e:
        print(f"[✗] 初始化失败: {e}")
        return

    # Spot 交易对（>0.1 USD）
    spot_symbols = ['BTCUSDT', 'ADAUSDT']

    # USD-M 期货交易对（USDT 计价）
    usdm_symbols = [
        'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT',
    ]

    # Coin-M 期货交易对（_PERP 后缀，币本位）
    coinm_symbols = [
        'BTCUSD_PERP', 'ETHUSD_PERP', 'BNBUSD_PERP',
        'ADAUSD_PERP', 'SANDUSD_PERP',
    ]

    total_trades = 0

    # 导入 Spot 交易
    print("\n[导入] Spot 交易：")
    for i, symbol in enumerate(spot_symbols, 1):
        trades = fetch_all_spot_trades(client, symbol)

        for trade in trades:
            processor.insert_trade({
                'market': 'spot',
                'symbol': symbol,
                'id': trade['id'],
                'orderId': trade['orderId'],
                'side': 'BUY' if trade['isBuyer'] else 'SELL',
                'price': trade['price'],
                'qty_base': trade['qty'],
                'quote_qty': trade['quoteQty'],
                'realized_pnl': 0,
                'margin_asset': None,
                'position_side': None,
                'fee': trade['commission'],
                'fee_asset': trade['commissionAsset'],
                'is_maker': trade['isMaker'],
                'time': trade['time'],
            })

        total_trades += len(trades)

        if i % 10 == 0 or i == len(spot_symbols):
            processor.flush()

        time.sleep(0.2)

    processor.flush()

    # 导入 USD-M 期货交易
    print("\n[导入] USD-M 期货交易：")
    for i, symbol in enumerate(usdm_symbols, 1):
        trades = fetch_all_usdm_trades(client, symbol)

        for trade in trades:
            processor.insert_trade({
                'market': 'usdm',
                'symbol': symbol,
                'id': trade['id'],
                'orderId': trade.get('orderId'),
                'side': trade['side'],
                'price': trade['price'],
                'qty_base': trade['qty'],
                'quote_qty': float(trade['price']) * float(trade['qty']),
                'realized_pnl': float(trade.get('realizedPnl', 0)),
                'margin_asset': 'USDT',
                'position_side': trade.get('positionSide'),
                'fee': float(trade.get('commission', 0)),
                'fee_asset': trade.get('commissionAsset'),
                'is_maker': trade.get('maker', False),
                'time': trade['time'],
            })

        total_trades += len(trades)

        if i % 5 == 0 or i == len(usdm_symbols):
            processor.flush()

        time.sleep(0.2)

    processor.flush()

    # 导入 Coin-M 期货交易
    print("\n[导入] Coin-M 期货交易：")
    for i, symbol in enumerate(coinm_symbols, 1):
        trades = fetch_all_coinm_trades(client, symbol)

        for trade in trades:
            processor.insert_trade({
                'market': 'coinm',
                'symbol': symbol,
                'id': trade['id'],
                'orderId': trade.get('orderId'),
                'side': trade['side'],
                'price': trade['price'],
                'qty_base': float(trade.get('baseQty', trade.get('qty', 0))),
                'quote_qty': None,
                'realized_pnl': float(trade.get('realizedPnl', 0)),
                'margin_asset': trade.get('marginAsset'),
                'position_side': trade.get('positionSide'),
                'fee': float(trade.get('commission', 0)),
                'fee_asset': trade.get('commissionAsset'),
                'is_maker': trade.get('maker', False),
                'time': trade['time'],
            })

        total_trades += len(trades)

        if i % 5 == 0 or i == len(coinm_symbols):
            processor.flush()

        time.sleep(0.2)

    processor.flush()

    print("\n" + "=" * 70)
    print(f"[✓] 导入完成！共导入 {total_trades} 条交易（Spot + USDM + COINM）")
    print("=" * 70)

    processor.close()

if __name__ == '__main__':
    main()
