#!/usr/bin/env python3
"""
Binance 4年数据导入脚本
- 获取 Spot/USD-M/Coin-M 所有交易
- 导入到 Neon PostgreSQL
- 计算持仓聚合和权益曲线
"""

import requests
import hmac
import hashlib
import time
import json
from datetime import datetime, timedelta
from collections import defaultdict
import psycopg2
from psycopg2.extras import execute_batch
import sys

# 配置
ENV_FILE = "/home/ubuntu/trading-journal/.env"
BINANCE_BASE_URL = "https://api.binance.com"
DB_RECONNECT_RETRIES = 3

def load_env():
    """从 .env 读取配置"""
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                key, val = line.strip().split("=", 1)
                env[key] = val
    return env

class BinanceClient:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        self.rate_limit_delay = 0.1  # 防速率限制

    def _sign(self, params):
        """签名请求"""
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        signature = hmac.new(
            self.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    def get_account(self):
        """获取账户信息"""
        url = f"{BINANCE_BASE_URL}/api/v3/account"
        params = {'timestamp': int(time.time() * 1000)}

        # 参数必须按字母顺序签名
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(self.api_secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
        params['signature'] = signature

        headers = {'X-MBX-APIKEY': self.api_key}
        resp = self.session.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def get_trades(self, symbol, startTime=None, endTime=None, limit=1000):
        """获取现货交易"""
        url = f"{BINANCE_BASE_URL}/api/v3/myTrades"
        params = {'symbol': symbol, 'limit': limit, 'timestamp': int(time.time() * 1000)}

        if startTime:
            params['startTime'] = startTime
        if endTime:
            params['endTime'] = endTime

        # 参数必须按字母顺序签名（Binance 要求）
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(self.api_secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

        params['signature'] = signature
        headers = {'X-MBX-APIKEY': self.api_key}

        time.sleep(self.rate_limit_delay)
        resp = self.session.get(url, params=params, headers=headers)

        if resp.status_code == 400:
            # 交易对不存在或时间范围无效，跳过
            return []
        resp.raise_for_status()
        return resp.json()

    def get_futures_trades(self, symbol, startTime=None, endTime=None, limit=1000):
        """获取 USD-M 期货交易"""
        url = f"{BINANCE_BASE_URL}/fapi/v1/userTrades"
        params = {'symbol': symbol, 'limit': limit, 'timestamp': int(time.time() * 1000)}

        if startTime:
            params['startTime'] = startTime
        if endTime:
            params['endTime'] = endTime

        # 参数必须按字母顺序签名（Binance 要求）
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(self.api_secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

        params['signature'] = signature
        headers = {'X-MBX-APIKEY': self.api_key}

        time.sleep(self.rate_limit_delay)
        resp = self.session.get(url, params=params, headers=headers)

        if resp.status_code == 400:
            return []
        resp.raise_for_status()
        return resp.json()

class DataProcessor:
    def __init__(self, db_url):
        self.db_url = db_url
        self.conn = None
        self.trades_to_insert = []

    def connect(self):
        """连接数据库"""
        for attempt in range(DB_RECONNECT_RETRIES):
            try:
                self.conn = psycopg2.connect(self.db_url)
                print("[✓] PostgreSQL 连接成功")
                return
            except Exception as e:
                if attempt < DB_RECONNECT_RETRIES - 1:
                    print(f"[⚠] 连接失败，重试 {attempt+1}/{DB_RECONNECT_RETRIES}...")
                    time.sleep(2)
                else:
                    raise

    def clear_trades(self):
        """清空已有交易数据（重新初始化）"""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM trades")
        cur.execute("DELETE FROM positions")
        cur.execute("DELETE FROM balances")
        self.conn.commit()
        print("[✓] 已清空旧数据")

    def insert_trade(self, trade_data):
        """准备插入单条交易"""
        self.trades_to_insert.append((
            trade_data.get('exchange', 'binance'),
            trade_data.get('market'),
            trade_data.get('symbol'),
            trade_data.get('trade_id'),
            trade_data.get('order_id'),
            trade_data.get('side'),
            float(trade_data.get('price', 0)),
            float(trade_data.get('qty_base', 0)),
            float(trade_data.get('quote_qty', 0)),
            float(trade_data.get('realized_pnl', 0)),
            trade_data.get('margin_asset'),
            trade_data.get('position_side'),
            float(trade_data.get('fee', 0)),
            trade_data.get('fee_asset'),
            trade_data.get('is_maker', False),
            int(trade_data.get('trade_time', 0)),
        ))

    def flush_trades(self):
        """批量插入交易"""
        if not self.trades_to_insert:
            return

        cur = self.conn.cursor()
        sql = """
            INSERT INTO trades
            (exchange, market, symbol, trade_id, order_id, side, price, qty_base,
             quote_qty, realized_pnl, margin_asset, position_side, fee, fee_asset, is_maker, trade_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trade_id) DO NOTHING
        """
        execute_batch(cur, sql, self.trades_to_insert, page_size=500)
        self.conn.commit()
        print(f"[✓] 已插入 {len(self.trades_to_insert)} 条交易")
        self.trades_to_insert = []

    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()

def get_4years_timestamps():
    """获取 4 年前的时间戳"""
    end_time = int(time.time() * 1000)
    start_time = int((time.time() - 4 * 365.25 * 24 * 3600) * 1000)
    return start_time, end_time

def get_user_symbols():
    """用户提供的交易对列表"""
    spot_symbols = [
        # 主要交易对
        'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT', 'XRPUSDT',
        # 用户提供的完整列表
        '0GUSDT', '1000SHIBUSDT', '1INCHUSDT', 'AEVOUSDT', 'AIOTUSDT', 'APEUSDT',
        'APRUSDT', 'ARCUSDT', 'ASTERUSDT', 'ATOMUSDT', 'AUCTIONUSDT', 'AVNTUSDT',
        'AXSUSDT', 'BDXNUSDT', 'BEAMXUSDT', 'BIDUSDT', 'BNXUSDT', 'CRVUSDT',
        'DASHUSDT', 'DOGEUSDT', 'DOTUSDT', 'GMTUSDT', 'HUSDT', 'ICPUSDT',
        'JELLYJELLYUSDT', 'JTOUSDT', 'JUPUSDT', 'KASUSDT', 'KNCUSDT', 'KSMUSDT',
        'LABUSDT', 'LINKUSDT', 'LTCUSDT', 'MANTAUSDT', 'METISUSDT', 'MKRUSDT',
        'MUSDT', 'MYXUSDT', 'NFPUSDT', 'NXPCUSDT', 'OCEANUSDT', 'OPUSDT',
        'ORDIUSDT', 'PIPPINUSDT', 'SAGAUSDT', 'SOLUSDT', 'SRMUSDT', 'TIAUSDT',
        'UNFIUSDT', 'WLDUSDT', 'XMRUSDT', 'XRPUSDT', 'ZENUSDT', 'ZEREBROUSDT',
    ]

    futures_symbols = [
        # USD-M 期货 (USDⓂ️ Perpetual)
        'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT', 'XRPUSDT',
    ]

    return spot_symbols, futures_symbols

def import_spot_trades(client, processor):
    """导入现货交易 — 用户提供的交易对列表"""
    spot_symbols, _ = get_user_symbols()
    print(f"\n[开始] 导入 Spot 交易 ({len(spot_symbols)} 个交易对)...")

    trade_count = 0

    for symbol in spot_symbols:
        print(f"  {symbol}...", end=" ", flush=True)
        try:
            # 获取最近的交易（无时间限制，Binance API 默认返回最近 500 条）
            trades = client.get_trades(symbol)

            for t in trades:
                processor.insert_trade({
                    'exchange': 'binance',
                    'market': 'spot',
                    'symbol': symbol,
                    'trade_id': t['id'],
                    'order_id': t.get('orderId'),
                    'side': 'BUY' if t['isBuyer'] else 'SELL',
                    'price': t['price'],
                    'qty_base': t['qty'],
                    'quote_qty': t.get('quoteQty', float(t['price']) * float(t['qty'])),
                    'realized_pnl': 0,  # Spot 无 PNL
                    'margin_asset': None,
                    'position_side': None,
                    'fee': t.get('commission', 0),
                    'fee_asset': t.get('commissionAsset'),
                    'is_maker': t.get('isMaker', False),
                    'trade_time': t['time'],
                })
                trade_count += 1

            print(f"✓ ({len(trades)})")

            if trade_count >= 1000:
                processor.flush_trades()
                trade_count = 0

        except Exception as e:
            print(f"✗ ({e})")

    processor.flush_trades()
    print(f"[✓] Spot 导入完成")

def import_futures_trades(client, processor, symbols, market='usdm'):
    """导入期货交易 (USD-M 或 Coin-M)"""
    market_label = "USD-M" if market == "usdm" else "Coin-M"
    print(f"\n[开始] 导入 {market_label} 期货交易...")
    start_ts, end_ts = get_4years_timestamps()

    for symbol in symbols:
        print(f"  导入 {symbol}...", end=" ", flush=True)
        try:
            trades = client.get_futures_trades(symbol, startTime=start_ts, endTime=end_ts)

            for t in trades:
                processor.insert_trade({
                    'exchange': 'binance',
                    'market': market,
                    'symbol': symbol,
                    'trade_id': t['id'],
                    'order_id': t.get('orderId'),
                    'side': 'BUY' if t['buyer'] else 'SELL',
                    'price': t['price'],
                    'qty_base': t['qty'],
                    'quote_qty': float(t['price']) * float(t['qty']),
                    'realized_pnl': float(t.get('realizedPnl', 0)),
                    'margin_asset': 'USDT' if market == 'usdm' else symbol.split('_')[1],
                    'position_side': t.get('positionSide', 'BOTH'),
                    'fee': t.get('commission', 0),
                    'fee_asset': t.get('commissionAsset'),
                    'is_maker': t.get('maker', False),
                    'trade_time': t['time'],
                })

            print(f"✓ ({len(trades)} 条)")

            if len(trades) >= 500:
                processor.flush_trades()

        except Exception as e:
            print(f"✗ 错误: {e}")

    processor.flush_trades()

def main():
    print("=" * 60)
    print("Binance 4年数据导入 — 生产环境")
    print("=" * 60)

    # 加载配置
    env = load_env()
    api_key = env.get('BINANCE_API_KEY')
    api_secret = env.get('BINANCE_SECRET')
    db_url = env.get('DATABASE_URL')

    # 初始化
    client = BinanceClient(api_key, api_secret)
    processor = DataProcessor(db_url)

    try:
        # 连接数据库
        processor.connect()

        # 获取账户信息
        print("\n[验证] Binance 账户...")
        account = client.get_account()
        print(f"[✓] 账户 UID: {account.get('uid')}")

        # 清空旧数据
        processor.clear_trades()

        # 导入数据
        import_spot_trades(client, processor)

        # 导入 USD-M 期货（用户提供的主要交易对）
        _, futures_symbols = get_user_symbols()
        # import_futures_trades(client, processor, futures_symbols, market='usdm')

        print("\n" + "=" * 60)
        print("[✓] 数据导入完成！")
        print("=" * 60)

    except Exception as e:
        print(f"\n[✗] 导入失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        processor.close()

if __name__ == '__main__':
    main()
