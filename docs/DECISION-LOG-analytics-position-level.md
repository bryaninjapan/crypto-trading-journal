# Decision Log — Analytics Long/Short 統計改為 position 級

日期：2026-06-04
文件：`trading_journal/api.py` → `/api/analytics`

## 問題

Analytics 頁的 Long/Short 區塊統計邏輯錯亂，次數、勝負、方向各用不同口徑：

| 市場/方向     | 舊輸出（運行中 API 實證）           | 矛盾                         |
|--------------|----------------------------------|------------------------------|
| usd-m LONG   | count=10（position）但 1W8L、avg_win=0 | W/L 是 fill 級、且方向錯桶   |
| usd-m SHORT  | count=6 但 11W4L（15 筆 fill）     | wins+losses 遠超 count        |
| coinm LONG   | count=7 但 0W0L、PNL=0            | 多頭回合的 fill 全跑去 SHORT 桶 |
| coinm SHORT  | count=1 但 14W55L（69 筆 fill）    | 1 個回合不可能 14W55L         |

### 根因

1. **單位混用**：`longs.count` / `shorts.count` 來自聚合後 `agg_positions`（回合級），
   但 `wins / losses / avg_win / avg_loss / win_ratio / total_realized_pnl` 來自**原始
   fills**。一個 position 由多筆分批平倉 fill 組成，每筆 fill 各有 `realized_pnl`，
   故一個回合貢獻多個 W/L → 次數恆對不上。

2. **方向來源不一致**：
   - count 的方向 = `build_positions_from_fills` 依首筆 fill 的 side 推斷（BUY→Long / SELL→Short）。
   - W/L 的方向 = DB 的 `position_side` 欄位。
   - COIN-M 的 `position_side` 在 DB 裡幾乎全是 SHORT（實測 LONG 0 筆、SHORT 69 筆），
     所以 7 個多頭回合的 fill 全落進 SHORT 桶 → LONG 桶 0W0L、SHORT 桶被塞爆。

## 修法

把 Long/Short 區塊與所有勝負統計**統一改成從 `agg_positions` 計算**，丟棄原始 fill 的
`position_side` 路徑：

- **方向**一律用 position 的 `direction`（"Long"/"Short"，由 `_build_one_position`
  依 cycle 首筆 fill side 推斷，與 count 同源）。
- **勝負**一律看 position 的**淨 `realized_pnl`** 正負（>0 勝、<0 負、==0 不計）。
  一個 position 只貢獻一個 W 或一個 L，故恆有 `wins + losses ≤ count`。
- `win_ratio = wins / (wins + losses)`、`avg_win = mean(淨PnL of wins)`、
  `avg_loss = mean(淨PnL of losses)`、`total_realized_pnl = sum(淨PnL)`。
- **移除舊的 `or 0.01 / or -0.01` 除零補丁**——改用 `if nw > 0` / `if nl > 0` 守衛即可。

### COIN-M PnL → USD 換算

`build_positions_from_fills` 產出的 position `realized_pnl` 是各 fill `realized_pnl` 的
**原始計價單位直接相加**：USD-M 為 USDT，COIN-M 為標的币（ADA/BTC 顆數）。沿用
`DECISION-LOG-coinm-pnl-usd` 的換算邏輯，新增 `net_pnl(p)`：

- usdm → 原樣返回（已是 USDT）。
- coinm → 取標的币（`pnl_asset`，fallback 解析 symbol 去 `USD_PERP`/`USD`），
  乘現價 `prices[f"{coin}USDT"]`；缺現價返回 None → 該 position 排除統計
  （`count` 仍計入，故 `wins+losses ≤ count` 仍成立）。
- 現價僅在 `market == "coinm"` 時才呼叫 Binance（usdm 不需要，省一次 API）。

勝負分類只看正負號，換算乘正數現價不改變符號，故方向桶歸屬不受換算影響；但
`avg_win` / `avg_loss` / `total_realized_pnl` 的**金額**必須換算才有物理意義。

## 一併統一為 position 級的頂層欄位（原為 fill 級）

依需求一併改為 position 級，避免同頁殘留 fill 級口徑：

- `kpi.win_rate`：position 淨 PnL 正負計勝率。
- `statistics.avg_trade_win` / `avg_trade_loss`：所有 decided position 的平均盈／虧（USD）。
- `statistics.largest_gain` / `largest_losses`：position 淨 PnL（USD）的極值
  （原為單筆 fill raw `realized_pnl`，COIN-M 為混合單位，無意義）。
- `statistics.max_consecutive_win` / `loss`：按 position **完成時間**（`close_time`，
  fallback `open_time`）排序後遍歷——`agg_positions` 是按 (market,symbol) 分組產出、
  非全域時間序，必須先排序才能算正確連勝/連敗。
- `statistics.total_gain_loss` / `trade_expectancy` / `avg_daily_gain`：用 position 級
  USD 合計 `total_pnl`（原為 fill raw sum，COIN-M 混合單位）。

成交量（`total_trades_volume` / `avg_daily_volume`）維持 fill 級 notional 不變
（成交量本就該逐筆計，與回合勝負無關）。

## 驗證（2026-06-04，本地 uvicorn :8011，admin:trading123）

| 市場/方向     | 舊（錯）           | 新（position 級）                         |
|--------------|-------------------|------------------------------------------|
| usd-m LONG   | 1W8L、avg_win=0   | count=10、5W5L、avg_win=4.91、avg_loss=-1.87、pnl=15.18 |
| usd-m SHORT  | 11W4L             | count=6、2W4L、pnl=-9.26                  |
| coinm LONG   | 0W0L、PNL=0       | count=7、2W5L、avg_win=43.49、avg_loss=-33.07、pnl=-78.36（USD） |
| coinm SHORT  | 14W55L、pnl=-438  | count=1、0W1L、avg_loss=-84.5（USD）       |

- 各方向 `wins + losses ≤ count` 全部成立。
- coinm 總 PnL ≈ -162.87 USD，與 `/api/summary` 的 USD 量級一致
  （微小差異為兩次調用間現價波動 + per-market vs 全市場口徑）。
