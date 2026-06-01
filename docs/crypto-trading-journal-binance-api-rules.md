---
type: reference
title: "Binance API 交易记录抓取规则手册"
domain: trading
updated: 2026-05-29
status: active
tags:
  - reference
  - trading
  - crypto
  - binance
  - api
  - python-binance
---

# Binance API 交易记录抓取规则手册

> 目标：抓取**现货 SPOT** + **USD-M 合约** + **COIN-M 币本位合约**三类历史成交记录。
> 痛点：`python-binance` SDK 方法名、底层 Open API 端点、**时间窗口限制**三者对不上号，本手册一次讲清。

---

## 0. 三市场速查表（最重要）

| 市场 | python-binance 方法 | 底层 Open API 端点 | Base URL | 时间窗口上限 | weight |
|------|--------------------|-------------------|----------|------------|--------|
| 现货 SPOT | `client.get_my_trades()` | `GET /api/v3/myTrades` | `api.binance.com` | **startTime↔endTime ≤ 24h** | 20 |
| USD-M 合约 | `client.futures_account_trades()` | `GET /fapi/v1/userTrades` | `fapi.binance.com` | **≤ 7 天** | 5 |
| COIN-M 币本位 | `client.futures_coin_account_trades()` | `GET /dapi/v1/userTrades` | `dapi.binance.com` | **≤ 7 天** | 20 |

**三个端点的共同硬规则：**
1. 全部是 **SIGNED（签名私有）** 接口，必须带 API Key + Secret + timestamp 签名。
2. **必须指定 `symbol`**（COIN-M 可用 `pair`）——没有"一次拉全部交易对"的接口。
3. `limit` 默认 500，**最大 1000**；返回顺序固定 `[最旧 → 最新]`。
4. 不传 `startTime/endTime` 时，只返回**最近一段**记录（现货返回最近的、合约返回最近 7 天），**绝不会给你全部历史**——这是新手最大误区。

---

## 1. 时间窗口限制 — 痛点根源

每个端点对 `startTime` 和 `endTime` 的**跨度**有硬限制，超出直接报错（`-1127` 或 `-1128` 类参数错误）。

| 市场 | 窗口跨度 | 抓 1500 天需要的窗口数 |
|------|---------|----------------------|
| SPOT | ≤ 24 小时 | 最坏 1500 个窗口 / 交易对 |
| USD-M | ≤ 7 天 | 最坏 ~215 个窗口 / 交易对 |
| COIN-M | ≤ 7 天 | 最坏 ~215 个窗口 / 交易对 |

> ⚠️ 现货 24h 窗口是最折磨的：抓 1500 天 × 3 个交易对 = 最多 4500 次请求。务必做好限速与续抓。

### 滑动窗口分页逻辑（统一写法）

```
current_end = now_ms
while current_end > start_ms:
    window_start = max(start_ms, current_end - WINDOW_MS)
    trades = fetch(symbol, startTime=window_start, endTime=current_end, limit=1000)
    if not trades:
        current_end = window_start - 1          # 本窗口无成交，整窗后移
        continue
    save(trades)
    current_end = trades[0]['time'] - 1         # 用本批"最旧"成交时间 -1ms 继续往前
    if len(trades) < 1000:
        # 本窗口已抓完，但可能还有更早的窗口 → 继续 while 而非 break
        current_end = window_start - 1
```

**关键点：**
- 返回是 `[最旧→最新]`，所以往**过去**翻页要用 `trades[0]['time'] - 1`（最旧那条）。
- `len(trades) == 1000` 说明**单个窗口内**还没抓完（一个 24h/7d 窗口内成交超 1000 笔），此时**不要后移窗口**，而是把 `endTime` 收到 `trades[0]['time']-1` 在同窗口内继续。
- `-1ms` 是为了避免同一条成交被重复抓到边界。
- 收尾一定要 **dedupe**（按 `id` 去重），边界重叠不可避免。

---

## 2. Rate Limit（限速）

| 市场 | 限速池 | 上限 | 触发后 |
|------|-------|------|--------|
| 现货 | IP weight | **6000 / 1min** | `-1003 TOO_MANY_REQUESTS`，等 5s 重试 |
| USD-M | IP weight | **2400 / 1min** | 同上，注意比现货更紧 |
| COIN-M | IP weight | **2400 / 1min** | 同上 |

- myTrades weight=20 → 现货单 IP 每分钟最多 ~300 次安全调用。
- 合约 weight 低（5/20）但池子小（2400）。
- **响应头**带剩余配额，监控这两个头比盲猜更稳：
  - 现货：`X-MBX-USED-WEIGHT-1M`
  - 合约：`X-MBX-USED-WEIGHT-1M`（同名，但 fapi/dapi 独立计数）
- 命中 `-1003` 偶尔会返回 `429`，**连续无视会升级成 `418`（封 IP 2 分钟～3 天）**，务必尊重 `Retry-After` 头。
- 实用节流：每 10 次调用 `sleep(0.3)`，命中 `-1003` 时 `sleep(5)` 重试（见本项目 `sync_all_trades.py:safe_request`）。

---

## 3. 三市场返回字段差异（踩坑重灾区）

### SPOT `get_my_trades`
```json
{
  "symbol": "BTCUSDT", "id": 28457, "orderId": 100234,
  "price": "4.00", "qty": "12.00", "quoteQty": "48.00",
  "commission": "10.10", "commissionAsset": "BNB",
  "time": 1499865549590, "isBuyer": true, "isMaker": false, "isBestMatch": true
}
```
- 方向看 `isBuyer`（布尔），**没有 `realizedPnl`**（现货无盈亏字段）。

### USD-M `futures_account_trades`
```json
{
  "symbol": "BTCUSDT", "id": 6789, "orderId": 569,
  "side": "BUY", "price": "4000", "qty": "1.0", "quoteQty": "4000",
  "realizedPnl": "0.5", "marginAsset": "USDT",
  "commission": "0.04", "commissionAsset": "USDT",
  "positionSide": "LONG", "buyer": true, "maker": false, "time": 1569514978020
}
```
- 方向用 `side`（"BUY"/"SELL"）**或** `buyer`（布尔，注意不是 `isBuyer`）。
- `qty` = 标的数量（币）；有 `realizedPnl`、`positionSide`（LONG/SHORT/BOTH）。

### COIN-M `futures_coin_account_trades`
```json
{
  "symbol": "BTCUSD_PERP", "id": 6789, "orderId": 569, "pair": "BTCUSD",
  "side": "BUY", "price": "9000", "qty": "16",
  "baseQty": "0.0017777", "realizedPnl": "0.00000000",
  "marginAsset": "BTC", "commission": "0.00000034", "commissionAsset": "BTC",
  "positionSide": "BOTH", "buyer": true, "maker": false, "time": 1591155555555
}
```
- **致命坑：`qty` 是合约张数（contracts），不是币数！** 真实币数量看 `baseQty`。
- 保证金/手续费以**币本位**计价（`marginAsset` / `commissionAsset` = BTC、ETH…）。
- 多了 `pair` 字段，可用 `pair` 代替 `symbol` 一次查该 pair 下所有合约（永续+交割）。

> 字段命名跨市场不一致：现货 `isBuyer` / `isMaker`，合约 `buyer` / `maker`。入库时务必做字段归一化。

---

## 4. 如何发现"我交易过哪些交易对"

trades 端点必须传 symbol，但你可能不记得全部交易对。用 **income/资金流水**端点反查（这俩**不需要 symbol**，可跨全交易对）：

| 市场 | python-binance 方法 | 端点 | 用途 |
|------|--------------------|------|------|
| USD-M | `client.futures_income_history()` | `/fapi/v1/income` | 列出 REALIZED_PNL / COMMISSION / FUNDING_FEE，从中提取出现过的 `symbol` |
| COIN-M | `client.futures_coin_income_history()` | `/dapi/v1/income` | 同上，币本位 |

- income 同样受 7 天窗口 + 1000 条限制，滑窗逻辑一致。
- 现货没有等价的"全交易对流水"接口 → 只能用已知交易对列表迭代，或借助 `client.get_account()` 看当前持仓资产倒推。

---

## 5. 常见错误码与修复

| 错误码 | 含义 | 修复 |
|--------|------|------|
| `-1003` | 请求过多（限速） | sleep 5s 重试，尊重 `Retry-After`，否则升级到 `418` 封 IP |
| `-1021` | timestamp 超出 recvWindow（**本机时钟漂移**） | 同步服务器时间（见下），或加大 `recvWindow` |
| `-2015` | API Key 无效 / 权限不足 / IP 不在白名单 | 开启 **Enable Reading**；合约需单独勾选合约权限；检查 IP 白名单 |
| `-2014` | API Key 格式错误 | 检查 Key/Secret 是否复制完整、无空格 |
| `-1102` | 必填参数缺失 | trades 端点必须带 `symbol` |
| `-1127` | 时间窗口超限 | 缩小 `startTime/endTime` 跨度到限制内（24h / 7d） |

### `-1021` 时钟漂移修复（合约抓取高频出现）
```python
import time
server_time = client.get_server_time()['serverTime']
client.timestamp_offset = server_time - int(time.time() * 1000)  # python-binance 内置该属性
# 或每次调用传 recvWindow=60000 放宽容忍度
```

### 权限要点
- **现货与合约共用同一对 Key/Secret**，但权限是分开的开关：
  - 现货读取：Enable Reading
  - 合约读取：需在 API 管理页**单独启用合约**（否则合约端点全部 `-2015`）。
- 抓历史**只需读权限**，**切勿勾选提现/交易**权限，降低密钥泄露风险。

---

## 6. 可直接套用的代码骨架

```python
import os, time, json
from datetime import datetime, timedelta
from binance.client import Client
from binance.exceptions import BinanceAPIException

client = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_API_SECRET'))
# 防 -1021：同步时钟
client.timestamp_offset = client.get_server_time()['serverTime'] - int(time.time()*1000)

LIMIT = 1000
SPOT_WIN  = 24 * 3600 * 1000          # 24 小时
FUT_WIN   = 7 * 24 * 3600 * 1000      # 7 天

def safe(fn, **kw):
    while True:
        try:
            return fn(**kw)
        except BinanceAPIException as e:
            if e.code == -1003:
                time.sleep(5); continue
            raise

def fetch_window(fn, symbol, start_ms, end_ms, win_ms, label):
    out, cur = [], end_ms
    while cur > start_ms:
        ws = max(start_ms, cur - win_ms)
        trades = safe(fn, symbol=symbol, startTime=ws, endTime=cur, limit=LIMIT)
        if not trades:
            cur = ws - 1; continue
        out.extend(trades)
        if len(trades) == LIMIT:
            cur = trades[0]['time'] - 1     # 同窗口内还没抓完，收紧 endTime
        else:
            cur = ws - 1                    # 本窗口抓完，整窗后移
        print(f'{label} +{len(trades)} total={len(out)}')
    return out

def dedupe(trades):
    seen, uniq = set(), []
    for t in sorted(trades, key=lambda x: x['time']):
        if t['id'] not in seen:
            seen.add(t['id']); uniq.append(t)
    return uniq

now  = int(time.time()*1000)
past = int((datetime.now() - timedelta(days=1500)).timestamp()*1000)

spot  = dedupe(fetch_window(client.get_my_trades,               'BTCUSDT',     past, now, SPOT_WIN, 'SPOT'))
usdm  = dedupe(fetch_window(client.futures_account_trades,      'BTCUSDT',     past, now, FUT_WIN,  'USDM'))
coinm = dedupe(fetch_window(client.futures_coin_account_trades, 'BTCUSD_PERP', past, now, FUT_WIN,  'COINM'))
```

> 实战说明：本项目最终**没用时间窗口分页**，改用 `fromId` 正向分页（直接绕开 24h/7d 限制）。落地见 [[trading/crypto-trading-journal-trades-sync|交易记录同步系统]]（`common.py` / `backfill_binance.py` / `sync_binance.py`）。上面的时间窗口骨架仍是理解窗口限制的参考。

---

## 7. 检查清单（抓取前过一遍）

- [ ] API Key 已启用现货读取 **且** 合约读取（否则合约 `-2015`）。
- [ ] 已同步 `timestamp_offset`，避免 `-1021`。
- [ ] 窗口跨度：现货 ≤ 24h、合约 ≤ 7d，**未写死成统一值**。
- [ ] 分页用 `trades[0]['time']-1`（最旧条），不是最新条。
- [ ] `len==1000` 时同窗口续抓，不要直接后移窗口漏单。
- [ ] COIN-M 用 `baseQty` 算币量，别用 `qty`（那是张数）。
- [ ] 字段归一化：`isBuyer`↔`buyer`、`isMaker`↔`maker`。
- [ ] 最终 `dedupe(id)`。
- [ ] 节流：每 ~10 次 sleep 0.3s；`-1003` sleep 5s。

---

## 相关文件

| 文件 | 用途 |
|------|------|
| [[trading/crypto-trading-journal-2026-05-28|CMM 阶段总结]] | 系统架构与窗口限制原始记录 |
| [[trading/crypto-trading-journal-trades-sync|交易记录同步系统]] | 回填 + 每日增量落地方案 |
| [[trading/scripts/binance_fetch_1500d|binance_fetch_1500d.py]] | K线公开接口抓取（无需签名） |
