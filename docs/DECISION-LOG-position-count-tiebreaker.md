# Decision Log — Position 計數不確定性（tie-breaker 修法）

日期：2026-06-04
文件：`trading_journal/api.py` → `build_positions_from_fills` / `/api/summary` / `/api/positions` / `/api/analytics`

## 問題

同一份資料，三個端點對 COIN-M 的 position 數給出不同答案（usdm 穩定 16，coinm 飄）：

| 端點 | 修法前 coinm | 修法前 usdm |
|------|-------------|------------|
| `/api/summary`（Dashboard，total_positions=usdm+coinm） | 8 | 16 |
| `/api/analytics?market=coinm`（kpi.total_trades） | 9 | 16 |
| `/api/positions?market=coinm`（total） | 15 | 16 |

## 根因

`build_positions_from_fills()` 是狀態機：按 `trade_time` 排序後逐筆累加 signed qty，
`cum_qty` 回到 ~0 或穿越 0 時截斷成一個 position。**逐筆順序決定 cycle 邊界**。

- COIN-M 有大量同毫秒 fill（`ADAUSD_PERP` 單一 `trade_time=1777605755912` 就有 20 筆；
  全 coinm 多個同毫秒 cluster）。
- 三個端點 SQL 都只 `ORDER BY trade_time ASC`，**沒有 tie-breaker**。不同查詢（不同
  `WHERE` 條件、不同計畫）回傳同毫秒 fill 的物理順序不同。
- `build_positions_from_fills` 第 152 行用 `sorted(..., key=trade_time)`，Python 穩定
  排序**保留輸入的物理順序** → 不同端點餵進不同順序 → 狀態機得到不同 cycle 邊界。
- 實證：同一份 coinm fills 用 12 種隨機輸入順序餵進去，position 數 = {9, 15}，不唯一。

## 修法（第一層：穩定 tie-breaker）

用 `trades` 表的單調主鍵 `id` 作為 tie-breaker，讓計數與輸入順序無關、跨端點一致。

1. 狀態機內排序改複合鍵 `key=lambda x: (x.get("trade_time",0), x.get("id",0))`
   （`build_positions_from_fills` 第 152 行；以及 `/api/positions/{trade_id}` 詳情路徑
   內重複的第二個狀態機，保持一致）。**這層是真正修好不確定性的關鍵**——即使 SQL
   回傳順序不同，狀態機自己重排後結果唯一。
2. 三個端點（+ position 詳情查詢）SQL 一律加 `ORDER BY trade_time ASC, id ASC`，
   雙重保險，並讓其他依賴查詢順序的消費者也確定。

### 為何用 `id` 而非 `trade_id`

`trades` 表主鍵是 serial `id`；另有 Binance 成交 id `trade_id`（bigint）。實測在每個
symbol group 內，`id` 升序與 `trade_id` 升序**完全一致**（入庫順序即按 trade_id），
故兩者等價。選 `id`：是 PK、每行必存在、最簡單。`trade_id` 跨 symbol 不全域單調，而
狀態機本就按 (market, symbol) 分組，組內兩鍵一致，無差別。

## 驗證

本地 `uvicorn ... --port 8000`，admin:trading123 curl，重複呼叫穩定：

| 端點 | 修法後 coinm | 修法後 usdm |
|------|-------------|------------|
| summary `total_positions` | — | — | （= 24 = 16+8，重複 3 次穩定） |
| positions `total`（all / coinm / usdm） | 8 | 16 |（all=24） |
| analytics `kpi.total_trades` | 8 | 16 |（重複 3 次穩定） |

不變量全部成立：
- `summary.total_positions (24) == positions(all).total (24)`
- `positions(coinm) (8) == analytics(coinm).total_trades (8)`
- `positions(usdm) (16) == analytics(usdm).total_trades (16)`
- `24 == 16 + 8`

修法後 coinm = **8**，即「按真實成交順序（id/trade_id 升序）」算出的唯一解。

## 關鍵決策

1. **只做第一層，不做第二層（同毫秒預聚合）。**
   原計畫評估第二層：把同 `(market, symbol, trade_time, side)` 的分批成交先聚合成一筆
   synthetic fill 再跑狀態機（理由：同毫秒 20 筆其實是一張單的分批成交，逐筆判 cycle
   邊界本就脆弱）。但第一層後三端點已一致、coinm=8 數字合理且等於真實成交順序的唯一解，
   第二層改變聚合語義、風險較高，**留作後續迭代**。若日後仍出現邊界誤判（例如同毫秒內
   先平後開被誤切），再實作第二層。

2. **tie-breaker 用單調 PK 而非時間戳精度。** 同毫秒 fill 無更細時間可分，唯一可靠的
   穩定次序是入庫/成交單調 id，故用 `id`。
