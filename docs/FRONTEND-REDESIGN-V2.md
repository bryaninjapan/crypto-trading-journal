# Trading Journal — 前端改版 V2 計劃

> 對標參考：CoinMarketManager (CMM)
> 決策鎖定：2026-06-02
> 範圍：4 個頁面，純前端改版，不動後端 API
> 執行順序：Dashboard → Journal → Analytics → PositionDetail

---

## 背景與決策

| 決策點 | 選擇 | 理由 |
|--------|------|------|
| 對標對象 | CMM Summary / Performance / Analytics | 用戶指定 |
| Dashboard ← | CMM Summary（不含 Latest 5 Future Trades） | 用戶確認 |
| Journal ← | CMM Performance Trade History 表格 | 用戶確認 |
| Analytics ← | CMM Performance 圖區 + CMM Analytics 全部 | 用戶確認 |
| PositionDetail ← | CMM inline expand 的獨立頁面版 | 用戶確認 |
| Open Positions / Unrealised PNL | **不做**（需要 live Binance API，後端未支持） | 用戶選 A |
| Deposits / Withdrawals | **不做**（後端無 transaction 數據） | 後端限制 |
| Journal drill-down | **跳頁**到 PositionDetail（保留現有路由） | 用戶選 A |
| 後端修改 | **零後端改動** | 用戶選 A |

---

## 已確認的數據可用性

| 功能 | API 端點 | 狀態 |
|------|---------|------|
| 帳戶餘額、Futures PNL、KPI | `/api/summary` | ✅ 已有 |
| 歷史 KPI（win rate donut、L/S、stats、avg hold） | `/api/analytics` | ✅ 已有（Dashboard 新增調用） |
| 持倉列表（分頁 + 篩選） | `/api/positions` | ✅ 已有 |
| 持倉詳情 + fills | `/api/positions/{id}` | ✅ 已有 |
| K 線圖 | `/api/positions/{id}/klines` | ✅ 已有 |
| 報表（按日/時/時長/規模） | `/api/reports` | ✅ 已有，**前端完全未建** |
| Symbol 聚合統計 | `/api/symbols` | ✅ 已有 |

---

## Phase 0 — 基礎修復（先做，所有頁面受益）

### 0-A：修復 PlotlyChart overflow-hidden 截線 bug

**問題根因**：Dashboard 的 `GlassCard` 有隱式 `overflow-hidden`（通過 `rounded-md` + 瀏覽器裁切），Plotly SVG 溢出被截斷。

**修復**：在 `PlotlyChart.tsx` 的包裝 div 加 `overflow: visible`，並在 `PlotlyChart` 的父容器（Dashboard GlassCard）傳入 `className="overflow-visible"`。

```tsx
// PlotlyChart.tsx
return <div ref={ref} style={{ width: "100%", height, overflow: "visible" }} />;
```

### 0-B：PlotlyChart 加 hover tooltip + ResizeObserver

**問題**：圖表沒有 hover crosshair / tooltip；視窗 resize 不重繪。

```tsx
// PlotlyChart.tsx 改動：
// 1. config 加 displayModeBar: false 已有，但需加 hovermode
const DARK_LAYOUT = {
  ...existing,
  hovermode: "x unified",          // 統一垂直 crosshair + tooltip
  hoverlabel: {
    bgcolor: "#1c1f29",
    bordercolor: "#464554",
    font: { color: "#e0e2ef", family: "JetBrains Mono, monospace", size: 11 },
  },
};

// 2. 加 ResizeObserver 讓圖表隨容器寬度自適應
useEffect(() => {
  if (!ref.current) return;
  const ro = new ResizeObserver(() => Plotly.relayout(ref.current!, { autosize: true }));
  ro.observe(ref.current);
  return () => ro.disconnect();
}, []);
```

---

## Phase 1 — Dashboard 改版

### 目標外觀（對標 CMM Summary）

```
┌─────────────────────────────────────────────────────────┐
│  [甜甜圈 Total Balance]  [Futures PNL]  [Overall Win%]  │
│                          [Total Trades] [Avg Hold]       │
├─────────────────────────────────────────────────────────┤
│  Equity Curve（全寬，正確 hover tooltip，無截線）         │
├─────────────────────────────────────────────────────────┤
│  Balances  [All][Spot][USD-M][COIN-M]                   │
│  Symbol | Amount | Allocation ████░░░ xx% | Value       │
└─────────────────────────────────────────────────────────┘
```

### 1-1：新增 `BalanceDonut` 組件

**文件**：`src/components/charts/BalanceDonut.tsx`

- 用 Plotly `type: "pie"` + `hole: 0.65`
- 中心顯示 Total Balance USDT 值
- 每個 asset 一個扇形，色彩用 `primary / secondary / tertiary` 循環
- legend 顯示前 4 大 asset（其餘歸入 "Others"）

```tsx
// 數據計算（純前端）
const total = balances.reduce((s, b) => s + (b.balance ?? 0) * price(b.asset), 0);
// price() 先用 1:1 USDT，BTC 用 balances 中推算（若有 USDT 等值）
// 初版簡化：僅展示各 asset 的 balance 數量，不換算成 USD
```

### 1-2：Dashboard 主體重構

**文件**：`src/pages/Dashboard.tsx`

**數據來源**：並行調用 `/api/summary` + `/api/analytics?market=usdm`

```tsx
const summary = useApi(() => api.summary(), []);
const analytics = useApi(() => api.analytics("usdm"), []);
```

**佈局**（四大區塊）：

**區塊 A：頂部 stat 卡片行**
```
[BalanceDonut + Total Balance] | [Futures PNL] | [Win Rate] | [Total Trades] | [Avg Hold]
```
- 左側甜甜圈（跨 2 行高）+ 右側 2×2 stat 卡片格
- `Win Rate` 用現有 `kpi.win_rate`（來自 summary）
- `Total Trades` 用 `kpi.total_positions`
- `Avg Hold` 用 `analytics.statistics.avg_hold_ms`（新）
- `Futures PNL` 用 `kpi.usdm_realized_pnl`

**區塊 B：Equity Curve 全寬圖**
- 移出 GlassCard overflow-hidden
- 綠色 area chart（正值）/ 紅色 area chart（負值）動態切換
- 加 `hovertemplate: "%{x|%d %b %Y}<br>PNL: $%{y:.2f}"`
- PNL/Drawdown 切換 tab（從 Analytics 移過來）

**區塊 C：Balances 表格（tab 切換）**
```
[All] [Spot] [USD-M] [COIN-M]

Symbol | Amount | Allocation ████░░░ % | Value (USDT)
BTC      0.042    ██████████████ 99.2%   $2,987
USDT    23.38    █ 0.8%                  $23.38
```
- Tab 狀態：`useState<"" | "spot" | "usdm" | "coinm">("") `
- Allocation bar：`(balance / totalBalance) * 100`
- 最多顯示 8 行

### 1-3：移除現有結構

- 移除：大 GlassCard（CONSOLIDATED PNL 文字 + 截線圖）
- 移除：3 欄餘額 cards（改為 Balances 表格）
- 移除：底部 3 個 KpiCard（整合進頂部 stat 行）

---

## Phase 2 — Journal 改版

### 目標外觀（對標 CMM Performance Trade History）

```
Journal                    [All Symbols ▼] [All][USD-M][COIN-M][Spot]

 #  │ Symbol/Size   │ Open              │ Hold     │ Close             │ Realised PNL
────┼───────────────┼───────────────────┼──────────┼───────────────────┼──────────────
375 │ ADAUSD_PERP   │ $0.2394@          │ 3d 4h 20m│ $0.2349@          │ +$40.96
    │ -218 (Short) ▌│ 27/May/26 05:39  │          │ 30/May/26 10:00   │ +174.35 ADA

Display [10][20][50]        ← 1 2 3 ... 30 →
```

### 2-1：新增分頁狀態

```tsx
const [page, setPage] = useState(1);
const [pageSize, setPageSize] = useState(20);
const [symbolFilter, setSymbolFilter] = useState("");
const offset = (page - 1) * pageSize;
```

API 調用：
```tsx
const positions = useApi(
  () => api.positions({ market: market || undefined, status: "closed", limit: pageSize, offset }),
  [market, pageSize, page, symbolFilter],
);
```

### 2-2：新建 `TradeRow` 組件

**文件**：`src/components/TradeRow.tsx`

```tsx
// 一行 = 一個 position
// 左側 4px 色條：Long = bullish / Short = bearish
// 欄位：# | Symbol + Size | Open price@datetime | Hold | Close price@datetime | PNL
// 點擊 row → navigate(`/positions/${p.id}`)
```

顯示格式對標 CMM：
- `Symbol/Size`：`ADAUSD_PERP` + 第二行 `-218`（紅色 = short，綠色 = long）
- `Open`：`$0.2394@\n27/May/26 05:39`
- `Realised PNL`：`+$40.96\n+174.35910977 ADA`（若 asset 不是 USDT 才顯示第二行）

### 2-3：移除 Symbol cards 區塊

- 移除 Journal 的 "Traded Symbols" 卡片格（此數據移到 Analytics 作表格）
- 移除 "Recent Positions" 簡單列表
- 完整替換為 TradeTable

### 2-4：新增分頁控制器

**文件**：`src/components/Pagination.tsx`

```tsx
// Display: [10][20][50] 切換 pageSize
// Page nav: ← 1 2 3 ... 30 →（最多顯示 5 個頁碼）
// 從 positions.data.total 計算總頁數
```

---

## Phase 3 — Analytics 改版

### 目標外觀（對標 CMM Performance 圖區 + CMM Analytics）

```
Analytics                              [USD-M ▼]

[Total Trades] [Avg Hold] [Win Rate] [Expectancy]

[PNL/Drawdown 大圖（全寬）          ] [PNL ▌Drawdown]
                                       （切換 tab）

[Win Rate donut]  [Long/Short bar]

[Statistics 雙欄表]

[Day of Week 柱狀圖]  [Time of Day 柱狀圖]

[Trade Duration 水平條]  [Trade Size 水平條]

[Traded Symbols 表格]
```

### 3-1：新增 `/api/reports` 調用

**文件**：`src/api/client.ts`（已有 `reports()` 函數 if not, add it）

```tsx
const reports = useApi(() => api.reports("usdm"), []);
```

`/api/reports` 回傳格式（已確認）：
```json
{
  "by_day_of_week": [{"dow": 0, "pnl": -10.5, "n": 3}, ...],
  "by_hour": [{"hour": 9, "pnl": 45.2, "n": 7}, ...],
  "duration_buckets": [{"bucket": "<1h", "n": 12, "pnl": -5.3}, ...],
  "size_buckets": [{"bucket": "<100", "n": 8, "pnl": 2.1}, ...]
}
```

### 3-2：新建報表圖組件

**文件**：`src/components/charts/ReportBarChart.tsx`

- 通用 Plotly bar chart 封裝
- 水平（orientation="h"）或垂直（orientation="v"）
- 綠色 bar（正 PNL）/ 紅色 bar（負 PNL）—— 用 marker.color 按值著色
- 顯示 `n（次數）+ pnl（金額）`

用途：
1. **Day of Week**：x = Mon/Tue/.../Sun, y = pnl（垂直 bar）
2. **Time of Day**：x = 00:00~23:00, y = pnl（垂直 bar）
3. **Trade Duration**：y = bucket label, x = n（水平 bar，Win/Loss 雙色）
4. **Trade Size**：y = bucket label, x = n（水平 bar，Win/Loss 雙色）

### 3-3：新建 Traded Symbols 表格

從 Journal 移過來，改為表格格式：

```
Symbol | Market | Trades | Win% | L vs S ████░░ | Avg Hold | Total PNL
```

### 3-4：Statistics 雙欄表擴充

現有：6 個指標
目標：補齊以下（全部可從現有 analytics.statistics 取得）：

```
左欄                    右欄
Total Gain/Loss        Avg # Trades/day
Trade Expectancy       Avg Trade Win
Avg Daily Gain         Avg Trade Loss
Avg Win                Max Consecutive Win  ← 後端暫無，先跳過
Avg Loss               Max Consecutive Loss ← 後端暫無，先跳過
Largest Gain           Largest Loss         ← 後端暫無，先跳過
```

**注意**：Max Consecutive Win/Loss、Largest Gain/Loss 後端 statistics 字段目前不存在，先跳過（顯示 "—"），不改後端。

---

## Phase 4 — PositionDetail 改版

### 目標外觀（對標 CMM Chart Summary expand panel）

```
← Back     ADAUSD_PERP  [Long] [USD-M]              +$40.96

┌──────────────────────────┬───────────────────────────┐
│  K 線圖（較大）           │  Total Funding: N/A       │
│  [15m timeframe label]   │  Fees: -$1.09             │
│                          │  ─────────────────────    │
│  MAE $0.24 label         │  MAE    Entry    MFE      │
│  MFE $0.23 label         │  -1.5%  ↑      +5.01%    │
│                          │  ████████░░░░░░░░         │
│  [Open] [Close] markers  │  ─────────────────────    │
│                          │  [Entry Quality: 77% Good]│
│                          │  [Opp. Capture: 38% Poor] │
└──────────────────────────┴───────────────────────────┘

Position Details
Open: $0.2394 @ 27/May/26 05:39  │ Hold: 3d 4h 20m
Close: $0.2349 @ 30/May/26 10:00 │ Qty: 218
Fills: 2                          │ Fees: 4.55 ADA

Fills
Date              │ Side  │ Type   │ Size │ Avg Price │ Value      │ Commission
27/May/26 05:39   │ SELL  │ Market │ 218  │ $0.2394   │ 9,106.09   │ 4.55 ADA
30/May/26 10:00   │ BUY   │ Market │ 218  │ $0.2349   │ 9,280.54   │ -4.64 ADA
```

### 4-1：改為兩欄佈局

```tsx
// 左欄：CandleChart（佔 60% 寬度）
// 右欄：metrics panel（佔 40% 寬度）
<div className="grid grid-cols-1 gap-md md:grid-cols-[3fr_2fr]">
  <GlassCard><CandleChart ... /></GlassCard>
  <div className="space-y-md">
    <MetricsPanel p={p} />
  </div>
</div>
```

### 4-2：新建 MAE/MFE 改版 bar

對標 CMM：加入 Entry 位置三角標記（▼）+ Avg Exit 標記

```tsx
// MAE/MFE bar 從左到右：
// [MAE 紅區] [Entry ▼] [MFE 綠區]
// 三角標記位置 = MAE / (MAE + MFE) * 100%
```

### 4-3：新建 `FillsTable` 組件

**文件**：`src/components/FillsTable.tsx`

```tsx
// 欄位：Date | Side (BUY/SELL 著色) | Type (Market/Limit) | Size | Avg Fill Price | Value | Commission
// Date = fmtTime(fill.trade_time)
// Side = fill.side → green BUY / red SELL
// Type = 暫時全部顯示 "Market"（fill 無此欄位，默認 Market）
// Size = fmtNum(fill.qty_base, 0)
// Avg Fill Price = fmtNum(fill.price, 4)
// Value = fmtNum(fill.price * fill.qty_base, 2) （本地計算）
// Commission = fill.fee ? `${fmtNum(fill.fee, 6)} ${fill.fee_asset}` : "—"
```

### 4-4：Position Details 改為橫向卡片

移到 Fills 表格上方，簡潔兩行：
```
Open: $x.xxxx @ DD/Mon/YY HH:mm   │   Hold: Xd Xh Xm
Close: $x.xxxx @ DD/Mon/YY HH:mm  │   Qty: xxx
Realised PNL: +$xx.xx              │   Fills: N
```

---

## 組件清單（新建 / 改動）

| 組件 | 動作 | 影響頁面 |
|------|------|---------|
| `PlotlyChart.tsx` | 改動：加 ResizeObserver + hover config + overflow visible | 全部 |
| `GlassCard.tsx` | 改動：加 `noOverflow` prop | 全部 |
| `BalanceDonut.tsx` | **新建**：甜甜圈圖 | Dashboard |
| `Pagination.tsx` | **新建**：分頁控制器 | Journal |
| `TradeRow.tsx` | **新建**：交易記錄行 | Journal |
| `ReportBarChart.tsx` | **新建**：報表柱狀圖 | Analytics |
| `FillsTable.tsx` | **新建**：成交明細表 | PositionDetail |
| `Dashboard.tsx` | 重寫 | Dashboard |
| `Journal.tsx` | 重寫 | Journal |
| `Analytics.tsx` | 大改 | Analytics |
| `PositionDetail.tsx` | 中改 | PositionDetail |
| `client.ts` | 確認 `reports()` 函數存在 | Analytics |
| `types.ts` | 確認 Reports 類型定義 | Analytics |

---

## API client 補充（若缺少）

```ts
// src/api/client.ts 確認以下函數存在，若無則添加：
reports: (market: Market) => get<Reports>(`/api/reports?market=${market}`),
```

```ts
// src/api/types.ts 添加：
export interface ReportRow { pnl: number; n: number; }
export interface Reports {
  market: Market;
  long_short: { direction: string; n: number; wins: number; pnl: number; avg_hold: number }[];
  by_day_of_week: (ReportRow & { dow: number })[];
  by_hour: (ReportRow & { hour: number })[];
  duration_buckets: (ReportRow & { bucket: string })[];
  size_buckets: (ReportRow & { bucket: string })[];
  pnl_asset: string;
}
```

---

## 執行順序與預估工時

| Phase | 內容 | 預估 |
|-------|------|------|
| **Phase 0** | 修 overflow bug + hover tooltip + ResizeObserver | 30 分鐘 |
| **Phase 1** | Dashboard 完整改版 | 3-4 小時 |
| **Phase 2** | Journal 改版（表格 + 分頁） | 2-3 小時 |
| **Phase 3** | Analytics 改版（加 4 張報表 + 重排） | 3-4 小時 |
| **Phase 4** | PositionDetail 改版（FillsTable + 兩欄佈局） | 1-2 小時 |
| **總計** | | **約 10-14 小時** |

---

## 後端延後事項（本次不做）

以下功能等 4 個頁面視覺完成後再評估：

- [ ] Open Positions + Unrealised PNL（需 live Binance API）
- [ ] Total Deposits / Withdrawals（需 transaction history 表）
- [ ] Overall PNL 跨市場合計（需 `summary` 加 spot+coinm 求和）
- [ ] Time Underwater 指標（需 klines 分析 + DB 回寫）
- [ ] Max Consecutive Win/Loss、Largest Gain/Loss（需 analytics SQL 擴充）
- [ ] Longs/Shorts 各自 Win Ratio donut（需 analytics 按 direction 分組）

---

## Git 規劃

```
feature/frontend-redesign-v2
  ├─ fix: plotly overflow + hover tooltip (Phase 0)
  ├─ feat: dashboard v2 redesign (Phase 1)
  ├─ feat: journal trade history table (Phase 2)
  ├─ feat: analytics reports charts (Phase 3)
  └─ feat: position detail fills table (Phase 4)
```

每個 Phase 獨立 commit，PR 合併到 main 前全部通過 `tsc + vite build`。
