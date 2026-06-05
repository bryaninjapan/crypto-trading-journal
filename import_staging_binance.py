#!/usr/bin/env python3
"""
import_staging_binance.py — Load Binance income ledger + position records into staging tables

Staging tables (idempotent, additive):
  - binance_income: FUNDING_FEE / REALIZED_PNL / COMMISSION from income ledger CSVs
    Dedupe key: tran_id (Binance's unique transaction ID)
  - binance_positions_ref: Binance's position-closing records (direction, entry, exit, pnl, open/close times)
    Dedupe key: UNIQUE(symbol, opened_at, closed_at)

CSV sources:
  - Income: /Users/iruka/Documents/csv/Binance-合約交易記錄-*
  - Positions: /Users/iruka/Documents/csv/Binance-合約倉位記錄-*

Usage:
  source venv/bin/activate && python3 import_staging_binance.py
"""
import os
import sys
import glob
import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch
from datetime import datetime
from dotenv import dotenv_values

ENV_PATH = "/Users/iruka/Projects/trading-journal/.env"

def safe_float(v, default=None):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default

def safe_int(v, default=None):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default

def parse_time_china(s) -> int | None:
    """Parse Chinese datetime format 'YY-MM-DD HH:MM:SS' or 'YYYY-MM-DD HH:MM:SS.mmm' to Unix timestamp (ms)"""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    try:
        # Try short format first
        dt = datetime.strptime(s, "%y-%m-%d %H:%M:%S")
        return int(dt.timestamp() * 1000)
    except ValueError:
        try:
            # Try full year without milliseconds
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return int(dt.timestamp() * 1000)
        except ValueError:
            try:
                # Try with milliseconds
                dt = datetime.strptime(s.split('.')[0], "%Y-%m-%d %H:%M:%S")
                return int(dt.timestamp() * 1000)
            except ValueError:
                return None

def load_income_tables(conn):
    """Load binance_income from income ledger CSVs"""

    # Create staging table if not exists
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS binance_income (
            id              SERIAL PRIMARY KEY,
            symbol          VARCHAR(50),
            trans_type      VARCHAR(50),
            amount          NUMERIC(20,8),
            asset           VARCHAR(20),
            trade_time      BIGINT,
            tran_id         BIGINT UNIQUE NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()

    # Find all income CSV files
    income_files = sorted(glob.glob("/Users/iruka/Documents/csv/Binance-合約交易記錄-*.csv"))

    print(f"\n📊 Loading income ledger CSVs ({len(income_files)} files)")

    total_rows = 0
    total_inserted = 0

    for fpath in income_files:
        fname = os.path.basename(fpath)
        print(f"  📄 {fname}...", end=" ", flush=True)

        try:
            df = pd.read_csv(fpath, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        except UnicodeDecodeError:
            df = pd.read_csv(fpath, encoding="gbk", dtype=str, keep_default_na=False)
        except Exception as e:
            print(f"❌ Read failed: {e}")
            continue

        # Expect columns: 時間,類型,金額,資產,符號,交易ID
        df.columns = [c.strip().lstrip("﻿") for c in df.columns]

        if len(df) == 0:
            print("⏭️ empty")
            continue

        rows = []
        skipped = 0
        for idx, row in df.iterrows():
            symbol = str(row.get("符號", "")).strip()
            trans_type = str(row.get("類型", "")).strip()
            amount = safe_float(row.get("金額"))
            asset = str(row.get("資產", "")).strip()
            trade_time_str = str(row.get("時間", "")).strip()
            tran_id = safe_int(row.get("交易 ID"))

            if not symbol or not trans_type or tran_id is None or tran_id == 0:
                skipped += 1
                continue

            trade_time = parse_time_china(trade_time_str)
            if trade_time is None:
                skipped += 1
                continue

            rows.append((symbol, trans_type, amount, asset, trade_time, tran_id))
            total_rows += 1

        if not rows:
            print(f"⏭️ no valid rows (skipped {skipped})")
            continue

        # Upsert into staging table
        try:
            sql = """
                INSERT INTO binance_income (symbol, trans_type, amount, asset, trade_time, tran_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tran_id) DO NOTHING
            """
            execute_batch(cur, sql, rows, page_size=1000)
            inserted = cur.rowcount
            conn.commit()
            total_inserted += inserted
            print(f"✅ {len(rows)} rows ({inserted} inserted, {len(rows)-inserted} dedupe)")
        except psycopg2.Error as e:
            conn.rollback()
            print(f"❌ DB error: {e}")

    cur.close()
    print(f"\n  Total loaded: {total_rows} rows, {total_inserted} inserted (after dedupe)")
    return total_inserted

def load_position_tables(conn):
    """Load binance_positions_ref from position-closing CSVs"""

    # Create staging table if not exists
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS binance_positions_ref (
            id              SERIAL PRIMARY KEY,
            symbol          VARCHAR(50),
            direction       VARCHAR(20),
            entry_price     NUMERIC(20,8),
            exit_price      NUMERIC(20,8),
            qty             NUMERIC(20,8),
            realized_pnl    NUMERIC(20,8),
            opened_at       BIGINT,
            closed_at       BIGINT,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(symbol, opened_at, closed_at)
        )
    """)
    conn.commit()

    # Find all position CSV files
    position_files = sorted(glob.glob("/Users/iruka/Documents/csv/Binance-合約倉位記錄-*.csv"))

    print(f"\n📊 Loading position records CSVs ({len(position_files)} files)")

    total_rows = 0
    total_inserted = 0

    for fpath in position_files:
        fname = os.path.basename(fpath)
        print(f"  📄 {fname}...", end=" ", flush=True)

        try:
            df = pd.read_csv(fpath, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        except UnicodeDecodeError:
            df = pd.read_csv(fpath, encoding="gbk", dtype=str, keep_default_na=False)
        except Exception as e:
            print(f"❌ Read failed: {e}")
            continue

        df.columns = [c.strip().lstrip("﻿") for c in df.columns]

        if len(df) == 0:
            print("⏭️ empty")
            continue

        rows = []
        skipped = 0
        for idx, row in df.iterrows():
            symbol = str(row.get("符號", "")).strip()
            direction = str(row.get("倉位方向", "")).strip()

            # Skip empty positions (no entry/exit prices)
            entry_price = safe_float(row.get("進場價格"))
            exit_price = safe_float(row.get("平均收盤價"))

            qty = safe_float(row.get("最大未平倉量"))
            realized_pnl = safe_float(row.get("平倉盈虧"))

            opened_at_str = str(row.get("已開啟", "")).strip()
            closed_at_str = str(row.get("已關閉", "")).strip()

            if not symbol or not direction:
                skipped += 1
                continue

            opened_at = parse_time_china(opened_at_str)
            closed_at = parse_time_china(closed_at_str)

            if opened_at is None or closed_at is None:
                skipped += 1
                continue

            rows.append((symbol, direction, entry_price, exit_price, qty, realized_pnl, opened_at, closed_at))
            total_rows += 1

        if not rows:
            print(f"⏭️ no valid rows (skipped {skipped})")
            continue

        # Upsert into staging table
        try:
            sql = """
                INSERT INTO binance_positions_ref (symbol, direction, entry_price, exit_price, qty, realized_pnl, opened_at, closed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, opened_at, closed_at) DO NOTHING
            """
            execute_batch(cur, sql, rows, page_size=1000)
            inserted = cur.rowcount
            conn.commit()
            total_inserted += inserted
            print(f"✅ {len(rows)} rows ({inserted} inserted, {len(rows)-inserted} dedupe)")
        except psycopg2.Error as e:
            conn.rollback()
            print(f"❌ DB error: {e}")

    cur.close()
    print(f"\n  Total loaded: {total_rows} rows, {total_inserted} inserted (after dedupe)")
    return total_inserted

def main():
    cfg = dotenv_values(ENV_PATH)
    db_url = cfg.get("DATABASE_URL")
    if not db_url:
        print(f"❌ DATABASE_URL not found in {ENV_PATH}")
        sys.exit(1)

    try:
        conn = psycopg2.connect(db_url, connect_timeout=10)
    except psycopg2.Error as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    print("=" * 70)
    print("Loading Binance staging tables (income + position records)")
    print("=" * 70)

    income_count = load_income_tables(conn)
    position_count = load_position_tables(conn)

    # Show summary
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM binance_income")
    income_total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM binance_positions_ref")
    position_total = cur.fetchone()[0]

    print("\n" + "=" * 70)
    print("STAGING TABLES SUMMARY")
    print("=" * 70)
    print(f"  binance_income:        {income_total:6d} rows")
    print(f"  binance_positions_ref: {position_total:6d} rows")
    print("=" * 70)

    cur.close()
    conn.close()
    print("\n✅ Staging tables loaded successfully")

if __name__ == "__main__":
    main()
