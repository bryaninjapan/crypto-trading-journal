---
type: project
title: "CMM 交易日志系统 — 2026-05-28 阶段总结"
domain: trading
updated: 2026-05-28
status: active
tags:
  - project
  - trading
  - crypto
  - binance
  - infrastructure
---

# CMM 交易日志系统 — 2026-05-28 阶段总结

## 系统架构

```
Binance API  ←  Python 爬虫 (Vault 内)
                    ↓
            Neon PostgreSQL (Cloud)
                    ↓
            FastAPI (Oracle VM)
                    ↓
            Next.js 前端 (CMM Dashboard)
```

- **数据库**: Neon PostgreSQL (`Tradingjournal` 项目，pooler endpoint)
- **后端**: FastAPI (Python) 在 Oracle VM Ubuntu 24.04 (158.179.178.105:8000)
- **前端**: Next.js，HTTP Basic Auth (admin/cm2025)
- **爬虫**: Vault 内 Python 脚本，直接走 Binance 公开 API

## 已完成

### 1. 1500 天 K 线数据
- **脚本**: `wiki/trading/scripts/binance_fetch_1500d.py`
- **数据**: `wiki/data/btcusdt_1d_20260528.json` (1500 根日线)
- **时间范围**: 2020-12-05 → 2026-05-27
- **抓取方式**: asyncio + aiohttp，Binance 每批最大 1000 根，分 2 批抓完

### 2. 数据库表 `binance_klines`
```sql
CREATE TABLE binance_klines (
  id SERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  interval TEXT NOT NULL,
  open_time BIGINT NOT NULL,
  open DOUBLE PRECISION,
  high DOUBLE PRECISION,
  low DOUBLE PRECISION,
  close DOUBLE PRECISION,
  volume DOUBLE PRECISION,
  close_time BIGINT,
  quote_volume DOUBLE PRECISION,
  num_trades INTEGER,
  taker_buy_base DOUBLE PRECISION,
  taker_buy_quote DOUBLE PRECISION,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (symbol, interval, open_time)
);
```
- **修复**: 原 `DECIMAL(20,12)` 导致 `numeric field overflow`，改用 `DOUBLE PRECISION`

### 3. `/klines` API Endpoint
- **路径**: `GET http://158.179.178.105:8000/klines`
- **认证**: HTTP Basic Auth (admin/cm2025)
- **参数**:
  - `symbol` — 交易对，如 `BTCUSDT`
  - `interval` — 周期，如 `1d`、`1h`、`4h`
  - `limit` — 返回条数，默认 500，最大 1500
  - `start_time` / `end_time` — 可选，毫秒时间戳
- **示例**:
  ```bash
  curl -u admin:cm2025 "http://158.179.178.105:8000/klines?symbol=BTCUSDT&interval=1d&limit=5"
  ```

### 4. 现有 CMM Dashboard 端点
| 端点 | 用途 |
|------|------|
| `/summary` | 账户总览 |
| `/performance` | 业绩表现 |
| `/analytics` | 交易分析 |
| `/calendar` | 交易日历 |

## 技术细节

### 连接字符串 (Neon PostgreSQL)
```
postgresql://Bryan:[REDACTED]@c-2.ap-southeast-1.aws.neon.tech/Tradingjournal?sslmode=require&channel_binding=require&options=endpoint%3Dc2-xxxxx-0-7f2ecfa8-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx
```
- **必须用 pooler endpoint** (`c-2.ap-southeast-1.aws.neon.tech`)，不能用 `.eo2` 非 pooler 域名
- `options=endpoint%3D<id>` 给旧版 libpq 兼容

### Binance API 窗口限制（重要）

| 市场 | API 端点 | 最大时间窗口 | 分页方式 |
|------|----------|------------|---------|
| 现货 SPOT | `GET /api/v3/myTrades` | **≤ 24 小时** | `startTime`/`endTime` 滑动 |
| USD-M 合约 | `GET /fapi/v1/userTrades` | **≤ 7 天** | `startTime`/`endTime` 滑动 |
| COIN-M 币本位 | `GET /dapi/v1/userTrades` | **≤ 7 天** | `startTime`/`endTime` 滑动 |

- **Rate Limit**: 6000 request weight / 1 分钟，触发 `-1003` 需等 5 秒重试
- **返回顺序**: 每批返回 `[最旧 → 最新]`，用 `trades[0]['time'] - 1` 作为下一窗口的 `endTime`
- **分页逻辑**: 有交易时用最旧交易时间往前滑；无交易时直接跳到窗口起点

### Binance API — K线公开接口（已实现）
- **无需认证** (公开市场数据)
- **端点**: `https://api.binance.com/api/v3/klines`
- **参数**: `symbol`, `interval`, `limit`, `endTime` (毫秒)
- **返回**: 每批 1000 根，最旧→最新顺序

## 待接入

- [ ] **Binance 全量交易记录抓取** — 1500天，现货+USD-M+COIN-M（脚本已就绪，见 `sync_all_trades.py`）
- [ ] **Binance USD-M 合约** — API Key 同现货，等待实际交易后补充数据
- [ ] **Flipster USDT 合约** — API Key 前缀 `3|`，需正确 Origin header
- [ ] **Telegram 通知** — 同步完成后推送摘要
- [ ] **Vault 异地备份** — GitHub / iCloud 方案待定

## 相关文件

| 文件 | 用途 |
|------|------|
| `wiki/trading/scripts/binance_fetch_1500d.py` | Binance K线抓取脚本 |
| `wiki/data/btcusdt_1d_20260528.json` | 1500 根日线原始数据 |
| `wiki/trading/crypto-trading-journal-grillme-architecture.md` | 需求 Grill 记录 |
| `wiki/trading/crypto-trading-journal-neon-oracle-setup.md` | 基础设施搭建记录 |

## 下一步

1. 前端 `/klines` 图表 — 用 Lightweight Charts 渲染 K 线
2. 添加技术指标层 (MA、RSI、MACD)
3. 对齐 CMM 交易记录与 K 线，进场/出场可视化
