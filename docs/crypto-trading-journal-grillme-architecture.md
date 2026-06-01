# Crypto Trading Journal — 需求訪談與架構形成

> 本記錄包含 grill-me 問答過程、CMM 逆向工程分析，以及最終技術架構的決策依據。

## 1. 使用者需求訪談（grill-me）

### Q: 你希望研究哪些交易所的 API？

**A:** Binance 現貨 + Binance COIN-M 幣本位合約（BTCUSD/ETHUSD/ADAUSD PERP）+ Flipster USDT 合約

### Q: 交易頻率？

**A:** 低頻交易者，每週不到一筆。歷史交易共 929 筆，橫跨 1506 天。

### Q: 你想從 API 抓哪些資料？

**A:**
- 現貨：myTrades（歷史成交）
- COIN-M：myTrades（幣本位合約成交）
- Flipster：Prex Trading API 的成交記錄

### Q: 你希望呈現什麼分析？

**A:** 與 CoinMarketMan.com (CMM) 相同的功能：
- **Summary** — 交易總覽、總筆數、總盈虧
- **Performance** — 勝率、盈虧比、持倉時間分佈
- **Analytics** — 各幣種交易量/頻率分析
- **Calendar** — 每月交易日曆PnL

### Q: 部署在哪裡？

**A:** 
- 目標：Oracle Cloud Free Tier VM（24/7 開機）
- OS：Ubuntu 24.04 Minimal
- 不能間斷運行（同步腳本需要定時執行）

### Q: 資料庫用什麼？

**A:** 
- 偏好 PostgreSQL（結構化查詢優勢）
- 考慮過 SQLite（但交易記錄多、查詢複雜，PostgreSQL 更適合）
- 最後選擇 **Neon PostgreSQL**（免費版，0.5 GB 完全足夠）

### Q: 前端呈現方式？

**A:**
- HTTP Basic Auth 保護
- 不需要 Verification Page
- 不需要 Reporting 頁面（只要 Summary/Performance/Analytics/Calendar 四頁）
- 深色主題，綠漲紅跌

### Q: 通知機制？

**A:**
- Telegram 只發**純文字摘要**（不發截圖）
- 觸發方式：VM 每週自動 Cron + 前端手動按鈕觸發同步

### Q: 資料如何備份？

**A:**
- VM 上的 `.env` 存放所有 Keys 和連線字串
- Mac vault 每日自動備份到 GitHub

---

## 2. CMM 逆向工程分析

### 透過截圖解構 CoinMarketMan.com 架構

CMM 是一個專門給加密貨幣交易者的圖形化 Journal 工具。透過截圖分析還原其架構：

### 2.1 技術棧

| 層面   | 技術                                                                          |
| ---- | --------------------------------------------------------------------------- |
| 前端框架 | Next.js（App Router，頁面路由 `/summary` `/performance` `/analytics` `/calendar`） |
| 部署平台 | Vercel（邊緣部署，響應速度快）                                                          |
| 資料庫  | 自建 API Server + PostgreSQL                                                  |
| 認證   | 自家 Session/Cookie 系統                                                        |
| 變現方式 | 交易所返傭（CMM Partner Links）                                                    |

### 2.2 四個核心頁面功能拆解

#### Summary（總覽頁）
- 卡片式佈局：總交易筆數、總盈虧、勝率
- 最近交易明細 table（Symbol / Side / Price / Qty / Time）
- 佈局：上方 metrics cards，下方交易表格

#### Performance（績效頁）
- 圓餅圖：買入 vs 賣出比例
- 文字區塊：最大單筆盈虧、獲利/虧損交易筆數
- 表格：各幣種進場次數統計

#### Analytics（分析頁）
- 柱狀圖：各幣種交易量（Quote Qty 總和）
- 文字：平均持倉時間、交易頻率

#### Calendar（日曆頁）
- 月曆 grid，每個交易日標註當日 PnL
- 點擊日期展開當日成交明細

### 2.3 關鍵觀察

- CMM 使用**自建 API Server**（非直接從瀏覽器呼叫交易所 API）
- 所有交易資料存於 CMM 自有的 PostgreSQL
- 用戶需手動觸發同步（通過手動按鈕）
- CMM 只提供圖形化呈現，底層數據模型與本專案相同

---

## 3. 最終技術架構

### 3.1 系統架構圖

```
┌─────────────────────────────────────────────────────────┐
│  三個交易所                                              │
│  ┌──────────┐  ┌────────────────┐  ┌───────────────┐   │
│  │ Binance  │  │ Binance COIN-M │  │ Flipster Prex │   │
│  │  現貨     │  │  幣本位合約     │  │  USDT合約     │   │
│  │ /api/v3/ │  │ /dapi/v1/      │  │ /v1/myTrades  │   │
│  └────┬─────┘  └───────┬────────┘  └───────┬───────┘   │
│       │                 │                   │           │
│       └────────────────┴───────────────────┘           │
│                         │                              │
│              ┌──────────▼──────────┐                  │
│              │   Oracle VM          │                  │
│              │   Ubuntu 24.04       │                  │
│              │                      │                  │
│              │  ┌────────────────┐  │                  │
│              │  │  Python Sync   │  │                  │
│              │  │  Scripts       │  │                  │
│              │  └───────┬────────┘  │                  │
│              │          │           │                  │
│              │  ┌───────▼────────┐  │                  │
│              │  │  Neon PostgreSQL│  │                  │
│              │  │  (Free Tier)   │  │                  │
│              │  └───────┬────────┘  │                  │
│              │          │           │                  │
│              │  ┌───────▼────────┐  │                  │
│              │  │  FastAPI       │  │                  │
│              │  │  Dashboard     │  │                  │
│              │  │  (port 8000)   │  │                  │
│              │  └────────────────┘  │                  │
│              └──────────────────────┘                  │
└─────────────────────────────────────────────────────────┘
                          │
              ┌───────────▼───────────┐
              │   HTTP Basic Auth      │
              │   帳號: admin         │
              │   密碼: cm2025        │
              └───────────┬───────────┘
                          │
         ┌────────────────▼────────────────┐
         │   四頁 Dashboard                │
         │   Summary / Performance /        │
         │   Analytics / Calendar          │
         └─────────────────────────────────┘
```

### 3.2 元件說明

| 元件 | 技術 | 用途 |
|------|------|------|
| 同步腳本 | Python 3 + requests + psycopg2 | 從交易所 API 抓交易記錄入庫 |
| 資料庫 | Neon PostgreSQL（Free Tier，0.5 GB）| 儲存三個交易所的交易資料 |
| Web 框架 | FastAPI + uvicorn | 提供 REST API + Dashboard 路由 |
| 認證 | HTTP Basic Auth（FastAPI 內建）| 保護 Dashboard 存取 |
| 觸發方式 | VM 每週 Cron Job + 前端 `/sync` 按鈕 | 定時/手動同步 |
| 通知 | Telegram Bot（純文字摘要）| 同步完成後通知 |

### 3.3 資料庫 Schema

```sql
-- Binance 現貨
CREATE TABLE binance_spot_trades (
    id SERIAL PRIMARY KEY,
    trade_id BIGINT UNIQUE NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,        -- 'BUY' or 'SELL'
    price DECIMAL(20,10) NOT NULL,
    qty DECIMAL(20,10) NOT NULL,
    quote_qty DECIMAL(20,10) NOT NULL,
    commission DECIMAL(20,10) DEFAULT 0,
    commission_asset VARCHAR(20),
    trade_time BIGINT NOT NULL,        -- Unix timestamp ms
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Binance COIN-M 幣本位合約
CREATE TABLE binance_coinm_trades (
    id SERIAL PRIMARY KEY,
    trade_id BIGINT UNIQUE NOT NULL,
    symbol VARCHAR(30) NOT NULL,       -- e.g. 'BTCUSD_PERP'
    side VARCHAR(10) NOT NULL,
    price DECIMAL(20,10) NOT NULL,
    qty DECIMAL(20,10) NOT NULL,
    quote_qty DECIMAL(20,10) NOT NULL,
    commission DECIMAL(20,10) DEFAULT 0,
    commission_asset VARCHAR(20),
    trade_time BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Flipster Prex USDT 合約
CREATE TABLE flipster_trades (
    id SERIAL PRIMARY KEY,
    trade_id VARCHAR(50) UNIQUE NOT NULL,
    symbol VARCHAR(30) NOT NULL,       -- e.g. 'BTCUSDT.PERP'
    side VARCHAR(10) NOT NULL,
    price DECIMAL(20,10) NOT NULL,
    qty DECIMAL(20,10) NOT NULL,
    quote_qty DECIMAL(20,10) NOT NULL,
    commission DECIMAL(20,10) DEFAULT 0,
    commission_asset VARCHAR(20),
    trade_time BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 同步狀態追蹤（斷點續傳）
CREATE TABLE sync_status (
    id SERIAL PRIMARY KEY,
    source VARCHAR(30) NOT NULL UNIQUE,
    last_sync_id TEXT,
    last_sync_time BIGINT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.4 三個交易所 API 接入要點

#### Binance 現貨 `/api/v3/myTrades`
- Base URL: `https://api.binance.com`
- HMAC SHA256 簽名（字母順序拼接 params）
- Symbol 格式：`BTCUSDT`（無底線）

#### Binance COIN-M `/dapi/v1/myTrades`
- Base URL: `https://dapi.binance.com`
- HMAC SHA256 簽名
- Symbol 格式：`BTCUSD_PERP`（底線）

#### Flipster Prex Trading API
- Base URL: `https://trading-api.flipster.io`
- HMAC-SHA256 簽名（`method + path + body`）
- Symbol 格式：`BTCUSDT.PERP`

### 3.5 實作 Phase 規劃

| Phase | 內容 | 優先級 |
|-------|------|--------|
| Phase 1 | 資料層：Neon PostgreSQL Schema + 三個交易所同步腳本 | **最高** |
| Phase 2 | Dashboard：FastAPI + HTTP Basic Auth + 四頁 | 中 |
| Phase 3 | Cron Job + Telegram Bot + Mac vault 備份 | 低 |

---

## 4. 待完成事項（API Key 申請）

- [ ] 申請 Binance API Key（VM IP `158.179.178.105` 需加入白名單）
- [ ] 申請 Flipster API Key（用戶已有 Key：`3|...`，尚未設定）
- [ ] VM 端 `.env` 填入 API Key 後執行同步腳本
- [ ] 驗證資料入庫正確性
