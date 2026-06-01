# Trading Journal — Binance 交易记录同步与仪表板

自动化爬取 Binance 现货 / USD-M / COIN-M 合约交易记录，存入 Neon PostgreSQL，提供实时仪表板与 JSON API。

## 特性

- **三市场统一数据库** — 现货、USD-M 合约、COIN-M 合约的交易记录归一化存储
- **历史回填 + 每日增量** — `fromId` 正向分页绕开时间窗口限制，watermark 自愈漏跑
- **真实已实现盈亏** — 合约用交易所 `realized_pnl`，现货 FIFO 配对估算
- **FastAPI 仪表板** — Summary / Performance / Analytics / Calendar 四个页面 + `/trades` JSON API
- **按 symbol 单位** — COIN-M 按币本位（BTC / ADA），USD-M 与现货用 USDT，不跨币种相加

## 架构

```
Binance API ──fromId 正向分页──> 归一化 ──ON CONFLICT(exchange,market,symbol,trade_id) 去重──> Neon trades 表
   ├─ common.py            共享：client + 限速调用 + fetch_forward + 字段归一化 + DB upsert/watermark + symbol发现 + Telegram
   ├─ backfill_binance.py  一次性历史回填（watermark=0 → 全量 from_id=1）
   └─ sync_binance.py      每日 cron 增量（watermark+1 → 最新）
                           ↓
                      dashboard.py
                      ├─ /summary          总览 + 三市场盈亏卡
                      ├─ /performance      USD-M/现货/COIN-M 分区盈亏曲线/胜率/回撤
                      ├─ /analytics        交易对明细表 + 分布直方图
                      ├─ /calendar         每日热力图 + 月度汇总
                      ├─ /trades           JSON 分页查询 API
                      └─ /trades/stats     聚合统计（按 market 拆分）
```

## 快速开始

### 1. 环境设置

```bash
git clone <repo-url> ~/Projects/trading-journal
cd ~/Projects/trading-journal
pip install -r requirements.txt
```

### 2. 配置 `.env`

在 `/home/ubuntu/trading-journal/.env`（或项目根目录）：

```bash
BINANCE_API_KEY=<key>
BINANCE_API_SECRET=<secret>
DATABASE_URL=postgresql://user:pwd@host/dbname?sslmode=require
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>
DASHBOARD_USER=<http_basic_user>
DASHBOARD_PASS=<http_basic_password>
```

### 3. 数据库初始化

在 Neon 执行（详见 `docs/crypto-trading-journal-trades-sync.md`）：

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
  qty_base      DOUBLE PRECISION,
  quote_qty     DOUBLE PRECISION,
  realized_pnl  DOUBLE PRECISION,
  margin_asset  TEXT,
  position_side TEXT,
  fee           DOUBLE PRECISION,
  fee_asset     TEXT,
  is_maker      BOOLEAN,
  trade_time    BIGINT NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (exchange, market, symbol, trade_id)
);
CREATE INDEX idx_trades_lookup ON trades (exchange, market, symbol, trade_id);
CREATE INDEX idx_trades_time ON trades (trade_time);
```

### 4. 回填历史数据

```bash
cd trading_journal
python3 backfill_binance.py
```

### 5. 启动仪表板

```bash
python3 -m uvicorn dashboard:app --host 0.0.0.0 --port 8000
```

访问 `http://localhost:8000/summary`（HTTP Basic 认证）

### 6. 装 cron 定时增量同步

```bash
crontab -e
# 加入（UTC 01:00 每天）：
0 1 * * * cd /home/ubuntu/trading-journal && /usr/bin/python3 -m trading_journal.sync_binance >> sync.log 2>&1
```

## 文件说明

| 文件 | 用途 |
|------|------|
| `trading_journal/common.py` | 共享模块：Binance client、限速调用、fromId 分页、字段归一化、DB upsert、symbol 发现、Telegram |
| `trading_journal/backfill_binance.py` | 一次性回填全历史（支持断点续跑） |
| `trading_journal/sync_binance.py` | 每日增量同步（含 Telegram 推送和错误告警） |
| `trading_journal/dashboard.py` | FastAPI 仪表板 + 四个 HTML 页面 + 两个 JSON API |
| `docs/*.md` | 详细文档：API 规则、部署指南、架构决策、研究报告、踩坑记录 |

## 关键设计决策

1. **`fromId` 正向分页** — 绕开现货 24h / 合约 7d 的时间窗口限制
2. **Watermark 增量** — `SELECT MAX(trade_id)` 作为水位线，漏跑自动恢复
3. **复合唯一键** `(exchange, market, symbol, trade_id)` — trade_id 仅 symbol 内唯一
4. **字段归一化** — 三市场列名统一；COIN-M 用 `baseQty`；现货无 `realized_pnl`
5. **三市场原生单位** — USD-M/现货用 USDT；COIN-M 按 symbol 币本位
6. **真实已实现盈亏** — 合约用交易所 `realized_pnl`；现货 FIFO 配对估算

## 已知限制与改进空间

- [ ] 若 Binance 合约 `userTrades` 因防火墙实际受 7 天限制，需改为时间窗口滑动回退
- [ ] COIN-M 不同币种无法合并成统一盈亏曲线（除非换成等价 USD 价格，需 klines 回填）
- [ ] 现货盈亏为 FIFO 估算，与交易所实际成本口径可能有差
- [ ] Position 级盈亏聚合（多笔平仓→一个持仓）未实现

## 扩展计划

- [ ] Flipster USDT 合约接入（复用同一 trades 表，`exchange='flipster'`）
- [ ] 若要恢复「成交 × K 线对齐」，需重新回填 klines 表
- [ ] Position 级盈亏分析与 P&L 按单位时间的 ROI 计算

## 技术栈

- **爬虫** — python-binance SDK + psycopg2
- **数据库** — Neon PostgreSQL (cloud)
- **后端** — FastAPI + Uvicorn
- **前端** — Plotly (HTML5)
- **告警** — Telegram Bot API
- **部署** — Oracle Cloud VM (Ubuntu 22.04)

## 许可证

MIT

## 作者

Bryan （@iruka）

## 参考

- [Binance API 规则手册](docs/crypto-trading-journal-binance-api-rules.md)
- [架构与设计决策](docs/crypto-trading-journal-grillme-architecture.md)
- [部署全记录](docs/crypto-trading-journal-neon-oracle-setup.md)
- [系统总结](docs/crypto-trading-journal-2026-05-28.md)
