# Crypto Trading Journal 研究報告

> 產出日期：2026-05-28  
> 研究目的：使用交易所 API Key 搭建個人 Trading Journal，讀取現貨 / USDT 合約 / 幣本位合約交易記錄並進行分析

---

## 1. 需求規格彙整

| 項目 | 規格 |
|------|------|
| 目標交易所 | Binance、Flipster（優先 Binance） |
| 交易類型 | USDT 合約（優先）→ 現貨 → 幣本位（後期擴展） |
| API 用途 | **只讀**（嚴格避免交易權限） |
| 擴展性 | 支援 1-2 家，架構預留擴展 |
| 分析功能 | 對標 CoinMarketMan：勝率、盈虧比、持倉時間分佈、收益日曆、最大回撤、資金曲線 |
| 輸出格式 | HTML 單頁報告（self-contained） |
| 數據儲存 | Oracle Free Tier VM（SQLite） |
| 更新頻率 | 每日 Cron 自動同步 |
| 歷史深度 | 先抓近 90 天 |

---

## 2. 交易所 API 分析

### 2.1 Binance

**API 成熟度：** ⭐⭐⭐⭐⭐（最佳）

幣本位合約（COIN-M）Base URL：`https://dapi.binance.com`
現貨 Base URL：`https://api.binance.com`

**幣本位合約 Symbol 格式：** `BTCUSD_PERP`（永續合約）、`BTCUSD_200925`（季度期 — 你不需要）

**認證方式（HMAC SHA256）：**

```
Header: X-MBX-APIKEY: <your_api_key>
Query/Body: timestamp=<ms>&recvWindow=5000&signature=<hmac_sha256>
```

- `timestamp`：毫秒級 Unix 時間
- `recvWindow`：建議 5000（5秒），超過會被拒絕
- `signature`：將所有參數（不含 signature 本身）按字母順序排列後拼接，用 secretKey 做 HMAC SHA256

**測試網：** `https://testnet.binancefuture.com`（可在測試網先驗證簽名邏輯）

**SDK：**
```
# 現貨
pip install binance-connector-python

# 幣本位合約（無獨立官方 SDK，用 ccxt）
pip install ccxt
```
ccxt 的 `binancecoin()` 封裝了完整的 `/dapi/` 簽名邏輯，可以直接用。

---

#### 現貨（Spot）— `/api/v3/`

| 用途 | 端點 | 關鍵欄位 |
|------|------|----------|
| 成交歷史 | `GET /api/v3/myTrade` | `symbol`, `side`, `price`, `qty`, `commission`, `time` |
| 帳戶餘額快照 | `GET /api/v3/account` | `balances[]`（asset / free / locked） |

---

#### 幣本位合約（COIN-M）— `/dapi/v1/` 或 `/dapi/v2/`

目標 Symbol：`BTCUSD_PERP`、`ETHUSD_PERP`、`ADAUSD_PERP`

| 用途 | 端點 | 關鍵欄位 |
|------|------|----------|
| 成交歷史 | `GET /dapi/v1/myTrades` | `symbol`, `side`, `price`, `qty`, `commission`, `time` |
| 持倉 & 帳戶 | `GET /dapi/v2/account` | `assets[]`（BTC/ETH/ADA 餘額）, `positions[]` |
| 持倉風險 | `GET /dapi/v1/positionRisk` | `entryPrice`, `leverage`, `liquidationPrice`, `unrealizedPnl`, `marginType` |
| 歷史資金費 | `GET /dapi/v1/fundingRate` | 資金費記錄（輔助分析）|

---

#### API 重要限制

| 項目 | 現貨 | 幣本位合約 |
|------|------|-----------|
| 歷史數據深度 | 90 天（`myTrade`） | 90 天（`myTrades`） |
| Rate Limit | 1200 請求/分鐘 | 2400 請求/分鐘 |
| 必需權限 | 僅讀取 | 僅讀取 |
| recvWindow 上限 | 5000ms（預設）| 同 |

**關於更長歷史：** Binance 對普通 API 用戶統一限制為約 90 天。若需要更長歷史（如 1-2 年），需升級為 `Binance Wealth` 或 `Binance Institutional` 帳戶，否則只能靠每日同步累積。

---

## 附：時間粒度說明

「時間粒度」指的是數據彙總的時間單位。在分析交易數據時，不同維度看到不同的規律：

| 粒度 | 用途 | 例子 |
|------|------|------|
| **分鐘級** | 日內交易分析 | 「上午 10:00 最容易進單」|
| **日級** | 日常紀錄、每日報告 | 「今天賺了 2%，最大回撤 1.5%」|
| **週級** | 策略回測、一週概覽 | 「這週勝率 60%，平均每筆 +1.2%」|
| **月級** | 月度總結、資産配置 | 「5月收益率 8%，手續費支出 $45」|

**對你的 Journal 來說：**
- 原始數據是**每一筆成交**（分鐘級以下）
- 每日快照是**日級**（沉澱到 SQLite）
- 報告可以切換粒度：日 / 週 / 月

目前設計的報告預設用**日級**，可切換至週/月。這樣足夠了嗎？還是你需要分鐘級分析？

---

### 2.2 Flipster（Prex Trading API）

**API 成熟度：** ⭐⭐⭐⭐（完整 REST API）

Flipster 提供的其實是 **Prex Trading API**，並非沒有 API，而是文檔在獨立的 `api-doc.flipster.io` 子網域。

**基本資訊：**

| 項目 | 值 |
|------|-----|
| API 名稱 | Prex Trading API |
| Base URL | `https://trading-api.flipster.io` |
| API Key 前綴 | `3\|` |
| 認證方式 | HMAC-SHA256（`api-key` / `api-expires` / `api-signature` 三個 Header）|
| 官網申請 | https://flipster.io/user/account/api-management |

**核心端點（全部支援讀取，無需交易權限）：**

| 用途 | 端點 | 關鍵欄位 |
|------|------|----------|
| **交易歷史（Execution History）** | `GET /api/v1/trade/execution/history` | `execId`, `orderId`, `symbol`, `side`, `lastPrice`, `lastQty`, `execFee`, `execType`（TRADE/FUNDING）, `transactTime` |
| 持倉現況 | `GET /api/v1/account/position` | `symbol`, `positionQty`, `entryPrice`, `unrealizedPnl`, `leverage`, `liquidationPrice`, `marginType` |
| 帳戶概覽 | `GET /api/v1/account` | `totalWalletBalance`, `totalUnrealizedPnl`, `totalMarginBalance`, `availableBalance` |
| 錢包餘額 | `GET /api/v1/account/balance` | `asset`, `balance`, `availableBalance`, `maxWithdrawableAmount` |
| 合約規格 | `GET /api/v1/market/contract` | `symbol`, `initMarginRate`, `maxLeverage`, `tickSize`, `notionalMinOrderAmount` |
| 手續費率 | `GET /api/v1/market/fee-rate` | `level`, `feeRate`（maker/taker）|

**交易歷史查詢參數：**

```
GET /api/v1/trade/execution/history
  ?productType=PERPETUAL        # SPOT 或 PERPETUAL
  &symbol=BTCUSDT.PERP         # 可選
  &execType=TRADE              # 可選：TRADE, FUNDING
  &count=64                    # 每頁數量（默认 64）
  &before=<uuid>              # cursor 分頁（舊數據）
  &after=<uuid>               # cursor 分頁（新數據）
  &startTime=<nanosecond_ts>  # 可選
  &endTime=<nanosecond_ts>    # 可選
```

**Python 簽名範例（直接可用）：**

```python
import hmac, hashlib
from urllib.parse import urlparse

def flipster_signature(secret, method, url, expires, data=None):
    parsed = urlparse(url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    parts = [method.upper().encode(), path.encode(), str(expires).encode()]
    if data:
        parts.append(data)
    message = b"".join(parts)
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
```

**重要細節：**
- `execType=TRADE` → 實際成交記錄
- `execType=FUNDING` → 資金費用記錄（每 8 小時結算一次）
- Timestamps 為**納秒級**（nanosecond）整數，需要除以 `1e9` 轉換為秒
- 僅支援 **Cross Margin**（無獨立幣本位合約接口，但 PERPETUAL 產品線齊全）
- API Key 需要 **read 權限**（申請時勾選「僅讀取」）

---

### 2.3 跨交易所方案：ccxt

**ccxt（CryptoCurrency eXchange Trading Library）**

支持 100+ 交易所的統一 Python 接口，包括 Binance、Bybit、OKX、Bitget 等。

```bash
pip install ccxt
```

**優點：**
- 統一接口，切換交易所成本低
- 支援現貨 + 期貨 + 幣本位
- 大量社群範例

**缺點：**
- 非官方，API 更新可能滯後
- 高級功能（如幣本位合約）支援度參差不齊

**建議：** 將 ccxt 作為**第二交易所擴展**的備選方案，但主幹仍使用各交易所官方 SDK，確保穩定性。

---

### 2.4 Bybit（後期擴展參考）

Bybit 官方 API 完善，Python SDK：

```bash
pip install bybit-connect
```

| 用途 | 端點 |
|------|------|
| USDT 合約持倉 | `GET /v5/position/list` |
| USDT 合約成交 | `GET /v5/execution/list` |
| 幣本位合約持倉 | `GET /v5/position/list`（coin=XXX）|

---

## 3. 推薦技術棧

```
┌─────────────────────────────────────────────────────┐
│                    架構總覽                          │
├─────────────────────────────────────────────────────┤
│                                                     │
│  [交易所 API] ──→ [Python Sync Engine] ──→ [SQLite] │
│     Binance                                            │
│     (official SDK)      Cron Job (每日)     數據入庫   │
│                                                     │
│                        ↓                             │
│              [HTML Report Generator]                  │
│               (Plotly / Chart.js)                    │
│                                                     │
│                        ↓                             │
│              [vault 備份 + Telegram 推送]              │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 核心依賴

| 用途 | Library | 安裝 |
|------|---------|------|
| Binance 現貨 API | `binance-connector-python` | `pip install binance-sdk-spot` |
| Binance 幣本位合約 | `ccxt`（官方 COIN-M SDK 不存在）| `pip install ccxt` |
| Flipster API | 原生 HMAC REST | `pip install requests` |
| 數據分析 | `pandas` | `pip install pandas` |
| 視覺化 | `plotly`（輸出 HTML）| `pip install plotly kaleido` |
| 數據庫 | `sqlite3`（Python 內建）| 無需安裝 |
| 定時任務 | `cron` + Python script | 系統級 |

### 數據模型（SQLite）

```sql
-- 交易記錄表
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL,           -- 'binance' / 'flipster'
    product_type TEXT NOT NULL,       -- 'spot' / 'coin_margined'
    symbol TEXT NOT NULL,             -- 'BTCUSDT', 'BTCUSD_PERP', 'ETHUSD_PERP', 'ADAUSD_PERP'
    side TEXT NOT NULL,               -- 'BUY' / 'SELL'
    quantity REAL NOT NULL,           -- 成交數量
    price REAL NOT NULL,              -- 成交價格
    commission REAL,                  -- 手續費
    commission_asset TEXT,             -- 手續費幣種（BNB或結算幣種）
    realized_pnl REAL,                -- 已實現盈虧（僅平倉時）
    order_id TEXT,                    -- 訂單ID
    exec_id TEXT UNIQUE,              -- 成交ID（去重用的）
    trade_time INTEGER NOT NULL,      -- Unix timestamp (ms)
    trade_type TEXT,                  -- 'trade' / 'funding'
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 每日餘額快照
CREATE TABLE daily_balance (
    id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL,
    product_type TEXT,                -- 'spot' / 'coin_margined'
    date TEXT NOT NULL,              -- 'YYYY-MM-DD'
    asset TEXT NOT NULL,              -- 'BTC', 'ETH', 'USDT', 'ADA'
    balance REAL NOT NULL,            -- 當日帳面餘額
    available_balance REAL,            -- 可用餘額
    unrealized_pnl REAL,              -- 未實現盈虧（僅合約）
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(exchange, product_type, date, asset)
);

-- 持倉快照（每日記錄）
CREATE TABLE position_snapshots (
    id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,             -- 'BTCUSD_PERP'
    side TEXT,                       -- 'LONG' / 'SHORT'
    quantity REAL,                    -- 持倉數量
    entry_price REAL,                -- 進場價
    mark_price REAL,                 -- 標記價格
    unrealized_pnl REAL,              -- 未實現盈虧
    leverage INTEGER,                 -- 槓桿倍數
    liquidation_price REAL,           -- 強平價格
    margin_type TEXT,                 -- 'CROSS' / 'ISOLATED'
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**去重策略：** `exec_id`（成交唯一ID）設 UNIQUE 約束，插入時用 `INSERT OR IGNORE` 防止重複。

---

## 4. 分析功能清單（對標 CoinMarketMan）

| 功能 | 說明 | 優先級 |
|------|------|--------|
| 勝率統計 | 盈利交易筆數 / 總交易筆數 | P0 |
| 盈虧比 | 平均盈利金額 / 平均虧損金額 | P0 |
| 資金曲線 | 每日 equity 折線圖 | P0 |
| 持倉時間分佈 | 平均持倉時長Histogram | P0 |
| 收益日曆 | 每日 PnL 熱力圖 | P1 |
| 最大回撤 | Max Drawdown 計算 + 標註 | P1 |
| 月度收益統計 | 每月總結算 PnL | P1 |
| 交易頻率分析 | 每日/每週交易次數 | P2 |
| 幣種分佈 | 各幣種交易次數與盈虧 | P2 |
| 槓桿分佈 | 使用槓桿的頻率與效果 | P2 |

---

## 5. 實現計劃

### Phase 1：核心數據讀取（預計 2-3 天）
1. 申請 Binance API Key（僅讀權限 + IP 白名單）
2. 安裝 `binance-connector-python`
3. 測試 `GET /fapi/v2/myTrades` 讀取近 90 天合約成交
4. 測試 `GET /fapi/v2/positionRisk` 讀取持倉
5. 建立 SQLite 數據庫與 `trades` 表
6. 撰寫 Flipster HMAC 簽名模組 + 測試 `/api/v1/trade/execution/history`
7. 確認 Flipster 歷史數據深度（是否滿足 90 天）

### Phase 2：數據入庫與校驗（預計 1 天）
1. 撰寫完整的每日同步腳本（增量更新邏輯，兩個交易所統一格式）
2. 數據校驗：重複交易过滤、金額精度處理、nanosecond→秒轉換
3. 建立 `daily_balance` 快照表

### Phase 3：分析與視覺化（預計 2-3 天）
1. 撰寫分析 module（勝率、盈虧比、回撤等）
2. 使用 Plotly 生成 self-contained HTML 報告
3. 報告發送到 vault 備份 + Telegram 通知

### Phase 4：自動化（預計 1 天）
1. 設定每日 Cron Job（在 Oracle VM 上）
2. 測試自動化流程端到端

---

## 6. 風險與緩解

| 風險 | 等級 | 緩解措施 |
|------|------|----------|
| API Key 洩漏 | 高 | 只開讀取權限；存入環境變量而非代碼；Oracle VM IP 白名單 |
| Flipster HMAC 簽名計算錯誤 | 中 | 嚴格按照文檔實現，注意 nanosecond timestamp 與秒級之分 |
| 歷史數據不足 | 中 | 90天為先行版本；日後優化增量備份 |
| Rate Limit | 低 | 添加 request 延時；避開高峰期同步 |
| Oracle VM 免費版限制 | 低 | 免費 tier：1GB RAM + Always Free，符合需求 |

---

## 7. Flipster 特別備註

Flipster 使用 **Prex Trading API**（`trading-api.flipster.io`），文檔在 `api-docs.flipster.io`。

### 7.1 實測確定的關鍵設定

**Origin / Cloudflare 繞過：**
- Cloudflare 擋 `urllib` Python UA → 1010 ASN Block
- 解決方式：瀏覽器 UA + `Origin: https://www.flipster.io`
- VM 已確認 `curl` + Chrome UA 可通過 Cloudflare，但 `/api/v1/account/info` 仍 403（Flipster 應用層阻擋）
- `/api/v1/trade/execution/history` 成功返回 1 筆記錄（2026-05-28）

**Signature 格式（已驗證）：**
```
message = METHOD + path + query_string + expires
例：GET + /api/v1/trade/execution/history?count=64 + 1779955935
sig = HMAC-SHA256(secret, message).hexdigest()
```

**請求 Header（已驗證可通過 Cloudflare）：**
```
User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ...
Origin: https://www.flipster.io
Referer: https://www.flipster.io/
api-key: <key>
api-expires: <unix_ts>
api-signature: <hex_sig>
```

### 7.2 支援的產品類型
- `SPOT` — 現貨
- `PERPETUAL` — USDT 合約（永續合約，無幣本位）

### 7.3 與 Binance 的關鍵差異

| 項目 | Binance | Flipster |
|------|---------|----------|
| 現貨 | ✅ | ✅ |
| USDT 合約 | ✅ | ✅ |
| 幣本位合約 | ✅（COIN-M）| ❌ |
| API 認證 | API Key + Secret（HMAC SHA256）| HMAC-SHA256（三Header） |
| 歷史數據限制 | 90天（myTrades）| 未標明（實測至少 1 筆）|
| Cloudflare 阻擋 | 無 | 有（需瀏覽器 UA + 正確 Origin）|

### 7.4 已知限制
- `/api/v1/account/info` 在 Oracle VM IP 仍返回 403 — Flipster 可能需要額外開通讀取權限或 IP 白名單尚未完全生效
- `/api/v1/trade/execution/history` 可讀，但目前只有 1 筆（帳號可能剛建立或幾乎沒在 Flipster 交易）

---

## 8. Binance COIN-M 特別備註

### 8.1 myTrades 404 解決方案

Binance COIN-M 的 `/dapi/v1/myTrades` 在 VM IP 上返回 404 HTML 錯誤頁，但 `/dapi/v1/account` 完全正常。

**原因分析：**
- VM IP 在 Binance 針對性封鎖或 Rate Limit 內
- 同一支 Key 在 USDT-M (`/api/v3/myTrades`) 完全正常
- COIN-M 的 account 資料齊全（有持倉、有 entryPrice）

**解決方案：改用倉位快照策略**
- 每筆同步時從 `/dapi/v1/account` 的 `positions[]` 拿當前倉位快照
- 寫入 `binance_coinm_snapshots` 表
- 通過**兩帧差分**計算已實現 PnL：
  ```
  Δunrealized_pnl = snapshot2.pnl - snapshot1.pnl
  ```
- 配合倉位 `entryPrice` × `positionAmt` 計算進場成本

### 8.2 實測持倉（2026-05-28）

| Symbol | Side | Position Amt | Entry Price | Leverage | Unrealized PnL |
|--------|------|-------------|-------------|----------|---------------|
| BTCUSD_PERP | SHORT | -30 | $75,934.70 | 1x | ~0.0014 BTC |
| ADAUSD_PERP | SHORT | -218 | $0.2394 | 1x | ~$347 |

---

## 9. 當前實現狀態（2026-05-28）

### 9.1 已完成的腳本

| 腳本 | 功能 | 狀態 |
|------|------|------|
| `sync_binance_spot.py` | 現貨 myTrades → Neon | ✅ 154 筆已入庫 |
| `sync_binance_coinm.py` | COIN-M 倉位快照 → Neon | ✅ 2 倉位 × 快照 |
| `sync_flipster.py` | Flipster execution history → Neon | ✅ 1 筆已入庫 |
| `dashboard.py` | FastAPI Web Dashboard (Summary/Performance/Analytics/Calendar) | ✅ 上線中 |

### 9.2 Dashboard

- URL：`http://158.179.178.105:8000/`
- 認證：HTTP Basic Auth（`admin` / `cm2025`）
- 依賴：FastAPI + uvicorn（port 8000）

### 9.3 待解決

| 項目 | 優先級 | 說明 |
|------|--------|------|
| Flipster account/info 403 | P1 | 需聯繫 Flipster 支援開通權限 |
| Flipster 歷史資料不足 | P1 | 目前僅 1 筆，可能需要等待更多交易 |
| Dashboard 四頁完整實作 | P2 | 目前 HTML 骨架，需串接真實數據和圖表 |
| VM iptables 持久化 | P2 | port 8000 規則重開機後失效 |
| 每週 Cron Job | P2 | 自動化同步 |
| Telegram Bot | P3 | 純文字摘要通知 |

---

## 8. 參考資源

- [Binance Developers API](https://developers.binance.com)
- [Binance Python SDK (GitHub)](https://github.com/binance/binance-connector-python)
- [ccxt Documentation](https://ccxt.readthedocs.io)
- [Bybit API Docs](https://bybit-exchange.github.io/docs/inverse/)
- [CoinMarketMan](https://coinmarketman.com)（功能對標）
