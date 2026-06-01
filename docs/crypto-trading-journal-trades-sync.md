---
type: project
title: "Binance 交易记录同步系统 — 回填 + 每日增量"
domain: trading
updated: 2026-05-29
status: active
tags:
  - project
  - trading
  - crypto
  - binance
  - infrastructure
---

# Binance 交易记录同步系统 — 回填 + 每日增量

把现货 / USD-M / COIN-M 三类成交记录灌进 Neon `trades` 表。
取代旧的 `sync_all_trades.py`（已删除）。设计经 grill-me 逐项确认，详见
[[trading/crypto-trading-journal-binance-api-rules|Binance API 抓取规则手册]]。

## 架构

```
Binance API ──fromId 正向分页──> 归一化 ──ON CONFLICT 去重──> Neon trades 表
   ├─ backfill_binance.py  一次性，from_id=0 灌全历史
   └─ sync_binance.py      每日 cron，from_id=水位线+1 增量
```

**三个脚本：**

| 文件 | 角色 |
|------|------|
| [[trading/scripts/common|common.py]] | 共享：client(含时钟同步)、限速调用、`fetch_forward`、字段归一化、DB upsert/水位线、symbol 发现、Telegram |
| [[trading/scripts/backfill_binance|backfill_binance.py]] | 一次性回填全历史，可断点续跑 |
| [[trading/scripts/sync_binance|sync_binance.py]] | 每日增量，跑完推 Telegram，失败推错误并退出码 1 |

## 关键设计决策（grill-me 结论）

1. **用 `fromId` 正向分页，不用时间窗口** — 直接按 trade id 翻页，**绕开现货 24h / 合约 7d 的窗口限制**，也不会有"单窗口 >1000 漏单"问题。
2. **watermark 增量** — 水位线 = `SELECT MAX(trade_id)`，不单独存状态表。漏跑自愈：某天没跑成，下次从老水位线接着抓，不丢单。
3. **单表 + 复合唯一键** `UNIQUE(exchange, market, symbol, trade_id)` — ⚠️ trade id 仅 **symbol 内唯一**，同一 market 跨 symbol 会重号（COIN-M 各 symbol 都从小 id 起算，必然撞；现货大 id 区间重叠也会偶发撞），所以唯一键和 watermark 都必须带 `symbol`。早期版本漏了 `symbol` 导致跨 symbol 静默丢单，已修复。
4. **字段归一化** — 现货 `isBuyer/isMaker` → `side/is_maker`；COIN-M 用 `baseQty`（币量）不是 `qty`（张数）；现货无 `realized_pnl` 留 NULL。
5. **symbol 发现** — 合约用 income 流水自动发现（不需 symbol）；现货 = 余额倒推(USD≥$0.1) × USDT ∪ 种子清单 `BTCUSDT/ADAUSDT/ETHUSDT`。
6. **每日 sync 额外并入 DB 已知 symbol** — 保证已知交易对持续更新，不因近期无活动而停更。

## 建表 SQL（在 Neon 执行一次）

```sql
CREATE TABLE IF NOT EXISTS trades (
  id            SERIAL PRIMARY KEY,
  exchange      TEXT NOT NULL,
  market        TEXT NOT NULL,            -- spot | usdm | coinm
  symbol        TEXT NOT NULL,
  trade_id      BIGINT NOT NULL,
  order_id      BIGINT,
  side          TEXT NOT NULL,            -- BUY | SELL
  price         DOUBLE PRECISION,
  qty_base      DOUBLE PRECISION,         -- 币量（COIN-M = baseQty）
  quote_qty     DOUBLE PRECISION,         -- COIN-M 为 NULL
  realized_pnl  DOUBLE PRECISION,         -- 现货为 NULL
  margin_asset  TEXT,
  position_side TEXT,
  fee           DOUBLE PRECISION,
  fee_asset     TEXT,
  is_maker      BOOLEAN,
  trade_time    BIGINT NOT NULL,          -- UTC 毫秒
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (exchange, market, symbol, trade_id)   -- ⚠️ 必须含 symbol：trade_id 仅 symbol 内唯一
);
CREATE INDEX IF NOT EXISTS idx_trades_lookup ON trades (exchange, market, symbol, trade_id);
CREATE INDEX IF NOT EXISTS idx_trades_time ON trades (trade_time);
```

### ⚠️ 已用旧唯一键建过表的迁移修复

旧键 `(exchange, market, trade_id)` 会跨 symbol 撞键丢单（COIN-M 已实测大面积丢）。
**不能只改约束后重跑 backfill**——watermark 取的是 `MAX(trade_id)`，已丢的低 id 成交夹在中间，
重跑只会从 `MAX+1` 往上抓，补不回来。正确做法是清表重灌：

```sql
DROP TABLE IF EXISTS trades;          -- 数据量小且已知被污染，直接重建最干净
-- 然后重新执行上面修正后的 CREATE TABLE + 两个索引
```

重建后再跑 `python3 backfill_binance.py`（watermark 归零 → 每个 symbol from_id=1 全量重拉，新键不再跨 symbol 撞键）。

## `.env`（在 Oracle VM `/home/ubuntu/trading-journal/.env`）

```bash
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
DATABASE_URL=postgresql://Bryan:<pwd>@c-2.ap-southeast-1.aws.neon.tech/Tradingjournal?sslmode=require&channel_binding=require
TELEGRAM_BOT_TOKEN=<从 BotFather 获取，勿提交 git>
TELEGRAM_CHAT_ID=<目标 chat id>
```

> ⚠️ **安全**：token/secret 只放 `.env`，确保 `.env` 在 `.gitignore`。本项目讨论中 Telegram token 曾明文出现，建议用 BotFather `/revoke` 重置一次。

## 部署步骤（在 Oracle VM 上）

```bash
# 1. 依赖
pip3 install python-binance psycopg2-binary python-dotenv requests

# 2. 上传三个脚本到 /home/ubuntu/trading-journal/
#    common.py  backfill_binance.py  sync_binance.py

# 3. 在 Neon 执行上面的建表 SQL（psql 或 Neon 控制台）

# 4. API Key 权限确认：启用现货读取 + 合约读取（否则合约 -2015）

# 5. 一次性回填全历史
cd /home/ubuntu/trading-journal && python3 backfill_binance.py

# 6. 验证落库
#    SELECT market, count(*) FROM trades GROUP BY market;

# 7. 装每日 cron（UTC 01:00）
crontab -e
# 加入：
0 1 * * * cd /home/ubuntu/trading-journal && /usr/bin/python3 sync_binance.py >> sync.log 2>&1
```

## 验证清单

- [x] `trades` 表建好，唯一键 `(exchange, market, symbol, trade_id)` 生效
- [x] API Key 现货 + 合约读取均已启用
- [x] `backfill_binance.py` 跑完，三市场计数合理（spot 146 / usdm 67 / coinm 1724；coinm = ADAUSD_PERP 1034 + BTCUSD_PERP 690，两 symbol 各自完整）
- [x] 重复跑 backfill 不产生重复行（重跑仅「新增 34/90」= ON CONFLICT 生效）
- [x] `sync_binance.py` 手动跑一次，Telegram 收到摘要
- [ ] 故意改错 token 跑一次，确认失败时 Telegram 收到错误 + 退出码 1
- [ ] cron 装好，次日确认 sync.log 有输出

## 旧版 dashboard.py（已废弃，对照存档）

重做前的 `dashboard.py` 架在**多张分表**上，与现在统一 `trades` 表的设计差异如下，留作对照：

**数据源（旧）** — 三个独立 loader，各读各表、各用旧列名：

| loader | 旧表 | 旧列名 |
|--------|------|--------|
| `load_spot` | `binance_spot_trades` | `qty` / `quote_qty` / `commission` / `commission_asset` / `trade_time` |
| `load_coinm` | `binance_coinm_trades` | 同上 |
| `load_flipster` | `flipster_trades` | `exec_id` / `transact_time`（列名又不一样）|
| `/klines` | `binance_klines` | K 线 JSON 端点 |

**盈亏逻辑（旧）的局限：**

1. **只有现货算盈亏** — `estimate_spot_pnl` 对现货做 FIFO（最旧 BUY 配 SELL，USDT）；COIN-M / Flipster **只 load 出来数个数**，Performance / Analytics / Calendar 三个页面实质只用现货数据。
2. **完全没用 `realized_pnl`** — 合约的交易所真实已实现盈亏被忽略，等于把最准的数据丢了。
3. **资金曲线加了 `initial_capital=10000` 基线**（`cumsum + 10000`），回撤按百分比算（盈亏曲线穿零时百分比口径会失真）。
4. **每个交易所一张表 + 列名不统一** — 加一个交易所就要加一个 loader、对齐一套列名，不可扩展。

**为何替换**：分表 schema 不一致、合约真实盈亏没用上、不可扩展。改为单一 `trades` 表后，旧表（`binance_spot_trades` / `binance_coinm_trades` / `binance_klines` / `flipster_trades`）已全部 DROP。

## 仪表盘（dashboard.py，已重做）

旧表已全部 DROP，`dashboard.py` 彻底重写，全部架在统一 `trades` 表上：

- **三市场各自分区、原生单位**：USD-M 用真实 `realized_pnl`(USDT)；COIN-M 按 symbol 币本位（不同币不可相加）；现货无 `realized_pnl`，FIFO 配对估算 USDT。
- **手续费按 `fee_asset` 分别汇总**，不跨币种相加。
- 页面：Summary / Performance / Analytics / Calendar，均改读 `trades`。
- 新增 JSON API：`GET /trades`（分页 + market/symbol/side/时间过滤）、`GET /trades/stats`（按 market 聚合，合约用真实 realized_pnl 算胜率）。
- **移除 `/klines`**（klines 表已删，专注交易分析）。
- 模板头补了 `plotly-2.27.0.min.js` CDN（旧版 `include_plotlyjs=False` 但没引入 js）。

> ⚠️ 胜率口径：基于 `realized_pnl` 符号，按**平仓成交**计，非按持仓聚合。后续可加 position 级聚合。

## 待接入

- [ ] Flipster USDT 合约 — 新增 `common_flipster.py` + `backfill/sync_flipster.py`，复用同一 trades 表（`exchange='flipster'`）
- [ ] position 级盈亏聚合（把多笔平仓成交归并成一次持仓的胜负）
- [ ] 若要恢复「成交 × K 线对齐」，需重新回填 klines

## 相关文件

| 文件 | 用途 |
|------|------|
| [[trading/crypto-trading-journal-binance-api-rules|Binance API 抓取规则手册]] | 三端点/窗口/字段/错误码参考 |
| [[trading/crypto-trading-journal-2026-05-28|CMM 阶段总结]] | 系统总体架构 |
| [[trading/scripts/common|common.py]] | 共享模块 |
| [[trading/scripts/backfill_binance|backfill_binance.py]] | 一次性回填 |
| [[trading/scripts/sync_binance|sync_binance.py]] | 每日增量 |
| [[trading/scripts/dashboard|dashboard.py]] | FastAPI 仪表盘 + `/trades` JSON API（读统一 trades 表）|
