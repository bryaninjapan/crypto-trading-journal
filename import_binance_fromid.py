#!/usr/bin/env python3
"""
Binance 数据导入脚本 - 使用 fromId 分页（绕过时间窗口限制）
基于 crypto-trading-journal 实战方案
参考：https://github.com/user/crypto-trading-journal/blob/main/sync_all_trades.py
"""

import requests
import hmac
import hashlib
import time
import json
import psycopg2
from psycopg2.extras import execute_batch

ENV_FILE = "/home/ubuntu/trading-journal/.env"
BINANCE_REST_URL = "https://api.binance.com"
RATE_LIMIT_SLEEP = 0.1
SAFE_SLEEP = 5  # -1003 限速时重试等待

def load_env():
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
        self.timestamp_offset = 0

    def sync_timestamp(self):
        """同步服务器时间，防止 -1021 错误"""
        resp = self.session.get(f"{BINANCE_REST_URL}/api/v3/time")
        server_time = resp.json()['serverTime']
        self.timestamp_offset = server_time - int(time.time() * 1000)
        print(f"[✓] 时钟同步完成 (offset={self.timestamp_offset}ms)")

    def _sign(self, params):
        """签名（参数必须按字母顺序）"""
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        return hmac.new(
            self.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256
        ).hexdigest()

    def get_trades_fromid(self, symbol, fromId=None, limit=1000):
        """
        获取现货交易 - 使用 fromId 分页（无时间限制）
        fromId=None 时返回最近的交易；fromId>0 时返回该 ID 之后的交易
        """
        url = f"{BINANCE_REST_URL}/api/v3/myTrades"
        timestamp = int(time.time() * 1000) + self.timestamp_offset

        params = {'symbol': symbol, 'limit': limit, 'timestamp': timestamp}
        if fromId:
            params['fromId'] = fromId

        params['signature'] = self._sign(params)
        headers = {'X-MBX-APIKEY': self.api_key}

        time.sleep(RATE_LIMIT_SLEEP)
        resp = self.session.get(url, params=params, headers=headers)

        # 检查限速
        if resp.status_code == 429:
            retry_after = int(resp.headers.get('Retry-After', SAFE_SLEEP))
            print(f"  [⚠] 限速 (429)，等待 {retry_after}s...")
            time.sleep(retry_after)
            return self.get_trades_fromid(symbol, fromId, limit)

        if resp.status_code == 400:
            data = resp.json()
            if data.get('code') == -1003:
                # IP 限速
                print(f"  [⚠] IP 限速 (-1003)，等待 {SAFE_SLEEP}s...")
                time.sleep(SAFE_SLEEP)
                return self.get_trades_fromid(symbol, fromId, limit)
            # 交易对不存在，返回空
            return []

        resp.raise_for_status()
        return resp.json()

    def fetch_all_trades(self, symbol):
        """
        获取某个交易对的所有历史交易（从最旧开始）
        使用 fromId 正向分页，无时间限制
        """
        all_trades = []
        fromId = None
        batch_num = 0

        while True:
            batch_num += 1
            trades = self.get_trades_fromid(symbol, fromId=fromId, limit=1000)

            if not trades:
                print(f"  {symbol}: ✓ 完成 ({len(all_trades)} 条交易)")
                break

            all_trades.extend(trades)
            print(f"  {symbol}: 批次 {batch_num} +{len(trades)} | 累计 {len(all_trades)}")

            # fromId 应该是本批最后一条的 ID + 1
            if len(trades) == 1000:
                fromId = trades[-1]['id'] + 1
            else:
                # 本批不满 1000，说明已到历史末尾
                break

        return all_trades

class DataProcessor:
    def __init__(self, db_url):
        self.db_url = db_url
        self.conn = None
        self.trades_to_insert = []

    def connect(self):
        for attempt in range(3):
            try:
                self.conn = psycopg2.connect(self.db_url)
                print("[✓] PostgreSQL 连接成功")
                return
            except Exception as e:
                if attempt < 2:
                    print(f"[⚠] 连接失败，重试 {attempt+1}/3...")
                    time.sleep(2)
                else:
                    raise

    def clear_trades(self):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM trades")
        cur.execute("DELETE FROM positions")
        self.conn.commit()
        print("[✓] 已清空旧数据")

    def insert_trade(self, trade_data):
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
        if self.conn:
            self.conn.close()

def get_user_symbols():
    """用户提供的交易对列表"""
    spot_symbols = [
        'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT', 'XRPUSDT',
        '0GUSDT', '1000SHIBUSDT', '1INCHUSDT', 'AEVOUSDT', 'AIOTUSDT', 'APEUSDT',
        'APRUSDT', 'ARCUSDT', 'ASTERUSDT', 'ATOMUSDT', 'AUCTIONUSDT', 'AVNTUSDT',
        'AXSUSDT', 'BDXNUSDT', 'BEAMXUSDT', 'BIDUSDT', 'BNXUSDT', 'CRVUSDT',
        'DASHUSDT', 'DOGEUSDT', 'DOTUSDT', 'GMTUSDT', 'HUSDT', 'ICPUSDT',
        'JELLYJELLYUSDT', 'JTOUSDT', 'JUPUSDT', 'KASUSDT', 'KNCUSDT', 'KSMUSDT',
        'LABUSDT', 'LINKUSDT', 'LTCUSDT', 'MANTAUSDT', 'METISUSDT', 'MKRUSDT',
        'MUSDT', 'MYXUSDT', 'NFPUSDT', 'NXPCUSDT', 'OCEANUSDT', 'OPUSDT',
        'ORDIUSDT', 'PIPPINUSDT', 'SAGAUSDT', 'SRMUSDT', 'TIAUSDT',
        'UNFIUSDT', 'WLDUSDT', 'XMRUSDT', 'ZENUSDT', 'ZEREBROUSDT',
    ]
    return spot_symbols

def main():
    print("=" * 70)
    print("Binance 数据导入 — fromId 正向分页（无时间限制）")
    print("=" * 70)

    env = load_env()
    api_key = env.get('BINANCE_API_KEY')
    api_secret = env.get('BINANCE_SECRET')
    db_url = env.get('DATABASE_URL')

    client = BinanceClient(api_key, api_secret)
    processor = DataProcessor(db_url)

    try:
        # 初始化
        client.sync_timestamp()
        processor.connect()

        # 获取账户信息验证
        print("\n[验证] Binance 账户...")
        # account = client.get_account()  # 需要实现
        # print(f"[✓] 账户 UID: {account.get('uid')}")

        processor.clear_trades()

        # 导入 Spot 交易
        print("\n[开始] 导入 Spot 交易（使用 fromId 分页）...")
        symbols = get_user_symbols()

        for i, symbol in enumerate(symbols, 1):
            print(f"\n[{i}/{len(symbols)}] {symbol}")
            try:
                trades = client.fetch_all_trades(symbol)

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
                        'realized_pnl': 0,
                        'margin_asset': None,
                        'position_side': None,
                        'fee': t.get('commission', 0),
                        'fee_asset': t.get('commissionAsset'),
                        'is_maker': t.get('isMaker', False),
                        'trade_time': t['time'],
                    })

                processor.flush_trades()

            except Exception as e:
                print(f"  [✗] 错误: {e}")

        print("\n" + "=" * 70)
        print("[✓] 数据导入完成！")
        print("=" * 70)

    except Exception as e:
        print(f"\n[✗] 导入失败: {e}")
        import traceback
        traceback.print_exc()

    finally:
        processor.close()

if __name__ == '__main__':
    main()
