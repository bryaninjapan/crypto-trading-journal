#!/usr/bin/env python3
"""
sync_binance.py — 每日增量同步
从各 (market, symbol) 的水位线（已入库最大 trade_id）之后抓新成交，upsert 进 Neon。
跑完推 Telegram 摘要。设计为 cron 每天 UTC 跑一次。

机制：watermark + fromId 续抓。
  - 漏跑自愈：某天没跑成，下次仍从老水位线接着抓，不丢单。
  - 不依赖精确时钟/时间窗口，DB 唯一键兜底去重。

用法（cron）：
  0 1 * * *  cd /home/ubuntu/trading-journal && /usr/bin/python3 sync_binance.py >> sync.log 2>&1
"""
from dotenv import load_dotenv
load_dotenv("/home/ubuntu/trading-journal/.env")

import traceback
import common as c
import positions as pos

# 合约 symbol 发现回看窗口：覆盖最近活动 + 给漏跑留余量
FUTURES_DISCOVERY_LOOKBACK_MS = 30 * 24 * 3600 * 1000
# 资金费增量回看窗口（给漏跑留余量；income 去重靠 tranId）
FUNDING_LOOKBACK_MS = 30 * 24 * 3600 * 1000


def known_symbols(conn, market):
    """DB 中该 market 已出现过的 symbol —— 保证已知 symbol 持续被同步。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT symbol FROM trades WHERE exchange=%s AND market=%s",
            (c.EXCHANGE, market),
        )
        return {r[0] for r in cur.fetchall()}


def sync_market(conn, market, trade_fn, normalize, symbols):
    new_count = 0
    for sym in symbols:
        from_id = c.get_watermark(conn, market, sym) + 1
        raw = c.fetch_forward(trade_fn, sym, from_id)
        # fromId 包含式：首条可能是已入库的水位线本身，DB 去重兜底
        rows = [normalize(t) for t in raw]
        new_count += c.upsert_trades(conn, rows)
    return new_count


def main():
    client = c.make_client()
    conn = c.get_conn()

    # 现货：余额倒推 + 种子 ∪ DB 已知
    spot_syms = sorted(set(c.discover_spot_symbols(client)) | known_symbols(conn, "spot"))
    # 合约：近 30 天 income 发现 ∪ DB 已知（确保已知 symbol 不会停更）
    usdm_syms = sorted(
        set(c.discover_futures_symbols(client.futures_income_history,
                                       FUTURES_DISCOVERY_LOOKBACK_MS))
        | known_symbols(conn, "usdm")
    )
    coinm_syms = sorted(
        set(c.discover_futures_symbols(client.futures_coin_income_history,
                                       FUTURES_DISCOVERY_LOOKBACK_MS))
        | known_symbols(conn, "coinm")
    )

    spot = sync_market(conn, "spot", client.get_my_trades, c.normalize_spot, spot_syms)
    usdm = sync_market(conn, "usdm", client.futures_account_trades, c.normalize_usdm, usdm_syms)
    coinm = sync_market(conn, "coinm", client.futures_coin_account_trades, c.normalize_coinm, coinm_syms)

    # 资金费增量（usdm + coinm）
    fund_rows = (
        [c.normalize_funding("usdm", r)
         for r in c.fetch_funding(client.futures_income_history, FUNDING_LOOKBACK_MS)]
        + [c.normalize_funding("coinm", r)
           for r in c.fetch_funding(client.futures_coin_income_history, FUNDING_LOOKBACK_MS)]
    )
    funding = c.upsert_funding(conn, fund_rows)

    # 账户余额快照
    bal = c.snapshot_balances(client, conn)

    # 重建持仓聚合（含新成交 + funding 窗口求和）
    written, total = pos.rebuild(conn)

    conn.close()
    msg = (f"<b>Binance 每日同步完成</b>\n"
           f"新增成交 — 现货 {spot} | USD-M {usdm} | COIN-M {coinm}\n"
           f"资金费 +{funding} | 余额快照 {bal} 项 | 持仓 {total}（更新 {written}）")
    print(msg)
    c.telegram_send(msg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = f"<b>⚠️ Binance 每日同步失败</b>\n<code>{type(e).__name__}: {e}</code>"
        print(err)
        traceback.print_exc()
        c.telegram_send(err)
        raise SystemExit(1)
