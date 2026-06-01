# 部署指南

本文档说明如何在 Oracle Cloud VM 或其他 Linux 服务器上部署 Trading Journal。

## 前置条件

- Python 3.8+
- Neon PostgreSQL 实例（已建好 `trades` 表）
- Binance API Key（现货读权限 + 合约读权限）
- Telegram Bot Token（可选，用于告警）
- Ubuntu 22.04 LTS 或类似 Linux 发行版

## 部署步骤（VM 上）

### 1. 系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git curl
```

### 2. 项目克隆与虚拟环境

```bash
git clone <repo-url> /home/ubuntu/trading-journal
cd /home/ubuntu/trading-journal
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

### 3. 环境变量配置

编辑 `.env`：

```bash
cat > .env << 'EOF'
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require&channel_binding=require
TELEGRAM_BOT_TOKEN=123456:ABCDEFGHijklmn
TELEGRAM_CHAT_ID=-1001234567890
DASHBOARD_USER=admin
DASHBOARD_PASS=securepass123
EOF
chmod 600 .env
```

### 4. 数据库初始化

在 Neon 控制台或 `psql` 执行（参见 `crypto-trading-journal-trades-sync.md`）：

```sql
CREATE TABLE IF NOT EXISTS trades (
  id            SERIAL PRIMARY KEY,
  exchange      TEXT NOT NULL,
  market        TEXT NOT NULL,
  symbol        TEXT NOT NULL,
  trade_id      BIGINT NOT NULL,
  order_id      BIGINT,
  side          TEXT NOT NULL,
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

### 5. 测试脚本

```bash
cd /home/ubuntu/trading-journal
source venv/bin/activate
python3 -c "from trading_journal import common; c = common.make_client(); print(c.get_server_time())"
```

若无报错，说明 Binance API 连接正常。

### 6. 一次性回填

```bash
python3 -m trading_journal.backfill_binance
```

预期输出：

```
现货: ['BTCUSDT', 'ADAUSDT', 'ETHUSDT']
USD-M: [... 14 symbols]
COIN-M: ['ADAUSD_PERP', 'BTCUSD_PERP']

=== 回填 SPOT (3 symbols) ===
  SPOT BTCUSDT: 抓取 ... 笔, 新增 ... (from_id=...)
...
SPOT 完成：累计新增 ... 笔

=== 回填 USD-M (14 symbols) ===
...

=== 回填 COIN-M (2 symbols) ===
...

<b>Binance 历史回填完成</b>
现货 ... | USD-M ... | COIN-M ... 笔
```

数据库检查：

```bash
psql $DATABASE_URL -c "SELECT market, count(*) FROM trades GROUP BY market;"
```

### 7. 验证数据库

```bash
psql $DATABASE_URL -c "
SELECT market, count(*) AS total, min(trade_time) AS earliest, max(trade_time) AS latest
FROM trades GROUP BY market;
"
```

### 8. 手动测试增量同步

```bash
python3 -m trading_journal.sync_binance
```

应收到 Telegram 消息：

```
<b>Binance 每日同步完成</b>
新增成交 — 现货 X | USD-M Y | COIN-M Z
```

### 9. 启动仪表板服务

#### 方式 A：直接运行（调试用）

```bash
python3 -m uvicorn trading_journal.dashboard:app --host 0.0.0.0 --port 8000 --reload
```

访问 `http://server-ip:8000/summary`（HTTP Basic 认证）

#### 方式 B：SystemD 服务（生产）

创建 `/etc/systemd/system/trading-journal-dashboard.service`：

```ini
[Unit]
Description=Trading Journal Dashboard
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading-journal
Environment="PATH=/home/ubuntu/trading-journal/venv/bin"
ExecStart=/home/ubuntu/trading-journal/venv/bin/python3 -m uvicorn trading_journal.dashboard:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

然后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-journal-dashboard
sudo systemctl start trading-journal-dashboard
sudo systemctl status trading-journal-dashboard
```

### 10. 装每日 cron（增量同步）

```bash
crontab -e
```

加入：

```cron
# UTC 01:00 每天运行一次增量同步
0 1 * * * cd /home/ubuntu/trading-journal && /home/ubuntu/trading-journal/venv/bin/python3 -m trading_journal.sync_binance >> sync.log 2>&1
```

验证：

```bash
crontab -l
```

次日检查日志：

```bash
tail -f /home/ubuntu/trading-journal/sync.log
```

## 常见问题排查

### Q: -1021 timestamp 漂移错误

**A:** `common.py` 已内置时钟同步（`client.timestamp_offset`）。若仍然报错，检查：
- VM 系统时间：`date -u`
- Binance 服务器时间：`curl -s https://api.binance.com/api/v3/time | python3 -m json.tool`
- 若差距 > 1 秒，同步 NTP：`sudo ntpdate -s time.nist.gov`

### Q: -1003 rate limit 频繁触发

**A:** `safe_request` 已包含节流（每 10 次请求 sleep 0.3s）和 -1003 重试（5s）。若仍触发：
- 减少并发数（单线程默认已最优）
- 检查是否有其他进程也在访问 Binance API
- 查看 API 剩余配额：Binance 账户设置 → API 管理 → 权重用量

### Q: 数据库连接超时

**A:** Neon 连接串检查：
- `sslmode=require` — SSL 必须
- `channel_binding=require` — 部分 psycopg2 版本需要改为 `disable`（试试两个）
- 防火墙：确认 VM 能访问 Neon 的 IP（Neon 在 AWS us-east-1，通常无需配置）

### Q: 找不到 `.env`

**A:** 确保 `.env` 在项目根目录（与 `setup.py` 同级）：

```bash
ls -la /home/ubuntu/trading-journal/.env
```

若权限过宽，改为：

```bash
chmod 600 /home/ubuntu/trading-journal/.env
```

### Q: `realized_pnl` 为 NULL（现货）

**A:** 正常。现货交易所不返回 `realized_pnl`，仪表板会自动用 FIFO 估算。检查 `realized_pnl` 对现货是否都是 NULL：

```sql
SELECT COUNT(*) FROM trades WHERE market='spot' AND realized_pnl IS NOT NULL;
```

应返回 0。

### Q: COIN-M 盈亏单位混乱

**A:** 检查 `margin_asset` 字段是否正确：

```sql
SELECT symbol, DISTINCT margin_asset FROM trades WHERE market='coinm';
```

预期输出：
- `ADAUSD_PERP` → `ADA`
- `BTCUSD_PERP` → `BTC`

若为 NULL，说明 Binance API 响应格式有变，需更新 `common.py` 的 `normalize_coinm` 逻辑。

## 升级与维护

### 代码更新

```bash
cd /home/ubuntu/trading-journal
git pull origin main
pip install -r requirements.txt --upgrade
# 若改了 dashboard.py，重启服务
sudo systemctl restart trading-journal-dashboard
```

### 数据备份

```bash
# 每周备份到本地
pg_dump $DATABASE_URL | gzip > trades_$(date +%Y%m%d).sql.gz
```

### 日志轮转

在 `/etc/logrotate.d/trading-journal` 加：

```
/home/ubuntu/trading-journal/sync.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
}
```

## 监控与告警

### Telegram 告警

已内置：
- `backfill_binance.py` 完成 → 推送摘要
- `sync_binance.py` 完成 → 推送新增成交数
- `sync_binance.py` 失败 → 推送错误栈 + 退出码 1

### Cron 失败检测

若 cron 不跑，检查：

```bash
sudo journalctl -u cron -f  # systemd 日志
# 或
sudo tail -f /var/log/syslog | grep CRON
```

### 数据新鲜度检测

```bash
# 检查最新成交时间
psql $DATABASE_URL -c "
SELECT market, max(trade_time) as latest_ms,
       (EXTRACT(EPOCH FROM NOW()) * 1000 - max(trade_time)) / 1000 / 3600 as hours_ago
FROM trades GROUP BY market;
"
```

若 `hours_ago > 25`，说明 cron 可能漏跑，检查 `sync.log`。

## 生产检查清单

- [ ] `.env` 权限 600，不在 git 跟踪
- [ ] API Key 仅开放「现货读取 + 合约读取」权限（关闭交易、提现等危险权限）
- [ ] Telegram Token 用过一次后立即 `/revoke` 重置（防止泄露）
- [ ] 数据库周期备份（至少每周一次）
- [ ] Cron 日志定期检查（周一早上确认前一天有无漏跑）
- [ ] 仪表板后面加反向代理（nginx）+ HTTPS
- [ ] 网络隔离（仅本地 / VPN 访问仪表板，不暴露公网）
