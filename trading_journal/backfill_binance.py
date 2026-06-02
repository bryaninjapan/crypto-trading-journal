#!/usr/bin/env python3
"""
backfill_binance.py — 一次性历史回填
抓取 Binance 现货 + USD-M + COIN-M 全部历史成交，upsert 进 Neon trades 表。

用法（在 Oracle VM 上跑一次即可）：
  python3 backfill_binance.py

机制：用 fromId=0 正向分页，绕开 24h/7d 时间窗口限制，一路抓到最新。
DB 唯一键去重，可安全重复运行（断点续跑时已入库的会被 ON CONFLICT 跳过）。
"""
from dotenv import load_dotenv
load_dotenv("/home/ubuntu/trading-journal/.env")

import common as c
import positions as pos


def backfill_market(client, conn, market, trade_fn, normalize, symbols, label):
    total = 0
    print(f"\n=== 回填 {label} ({len(symbols)} symbols) ===")
    for sym in symbols:
        # 回填从已入库水位线之后继续，支持断点续跑；首次为 0 = 全量
        from_id = c.get_watermark(conn, market, sym) + 1
        try:
            raw = c.fetch_forward(trade_fn, sym, from_id)
        except Exception as e:
            print(f"  跳过 {sym}: {e}")
            continue
        rows = [normalize(t) for t in raw]
        n = c.upsert_trades(conn, rows)
        total += n
        print(f"  {label} {sym}: 抓取 {len(raw)} 笔, 新增 {n} (from_id={from_id})")
    print(f"{label} 完成：累计新增 {total} 笔")
    return total


def main():
    client = c.make_client()

    # symbol 发现
    conn = c.get_conn()
    spot_syms = c.discover_spot_symbols(client)
    usdm_syms = c.discover_futures_symbols(client.futures_income_history)
    coinm_syms = c.discover_futures_symbols(client.futures_coin_income_history)
    conn.close()
    print(f"现货: {spot_syms}")
    print(f"USD-M: {usdm_syms}")
    print(f"COIN-M: {coinm_syms}")

    conn = c.get_conn()
    spot = backfill_market(client, conn, "spot", client.get_my_trades,
                           c.normalize_spot, spot_syms, "SPOT")
    conn.close()

    conn = c.get_conn()
    usdm = backfill_market(client, conn, "usdm", client.futures_account_trades,
                           c.normalize_usdm, usdm_syms, "USD-M")
    conn.close()

    conn = c.get_conn()
    coinm = backfill_market(client, conn, "coinm", client.futures_coin_account_trades,
                            c.normalize_coinm, coinm_syms, "COIN-M")
    conn.close()

    # 资金费全量回填（usdm + coinm）
    conn = c.get_conn()
    print("\n=== 回填资金费 (FUNDING_FEE) ===")
    fund_rows = (
        [c.normalize_funding("usdm", r)
         for r in c.fetch_funding(client.futures_income_history)]
        + [c.normalize_funding("coinm", r)
           for r in c.fetch_funding(client.futures_coin_income_history)]
    )
    funding = c.upsert_funding(conn, fund_rows)
    print(f"资金费 完成：新增 {funding} 笔")

    # 首次余额快照
    bal = c.snapshot_balances(client, conn)
    print(f"余额快照：{bal} 项")

    # 建持仓聚合
    print("\n=== 建持仓聚合 ===")
    written, total = pos.rebuild(conn)
    print(f"持仓 完成：聚合 {total} 个，写入 {written}")
    conn.close()

    msg = (f"<b>Binance 历史回填完成</b>\n"
           f"现货 {spot} | USD-M {usdm} | COIN-M {coinm} 笔\n"
           f"资金费 {funding} | 余额 {bal} 项 | 持仓 {total}")
    print("\n" + msg)
    c.telegram_send(msg)


if __name__ == "__main__":
    main()
