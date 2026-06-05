# DECISION LOG — COIN-M 部位聚合改用合約張數 + 淨部位配對（對標 process_trades_v7）

日期：2026-06-05
分支：feature/frontend-redesign-v2
檔案：trading_journal/api.py（`build_positions_from_fills`）

## 問題

舊 `build_positions_from_fills`（FIFO 逐 lot×平倉配對）對 COIN-M 產生 **2x 顆粒度**
（聚合 1,475 筆 vs 真實 664 筆）並造出**幽靈未平倉殘量**（docstring 寫死的「ADAUSD 殘 ~9721」）。

## 根因（實測）

1. **數量單位用錯**：FIFO 以 `qty_base`（=幣量）配對。COIN-M 是**反向合約**，部位量應是
   **合約張數**（DB 存於 `quote_qty`）。幣量在開/平價不同時不守恆：
   - ADA 開空 284 張 @0.825 = 3,442 幣；平回 284 張 @0.235 = 14,650 幣。
   - 同張數、幣量天差地遠 → 淨幣量永遠不歸零 → 配對碎裂 + 假未平倉。
   - 反向合約的幣量淨差 ≈ 已實現 PnL，所以舊算法的假「ADA 殘 5454」恰好 = ΣPnL（破綻）。
2. **顆粒度模型**：FIFO 每個「開倉 lot × 平倉」出一段（事後僅合併 time+price 完全相同者），
   一個平倉吃掉多個不同價開倉時 → N 段不合併 = 過度碎片。

## 決定

改用使用者已人工驗證的 **process_trades_v7「淨部位配對」模型**，移植進 `build_positions_from_fills`：

1. **事件化**：同 `(trade_time, side, is_close)` 的 raw fill 併成一個事件（加權均價 / Σqty / Σpnl / Σfee）。
   `is_close = realized_pnl != 0`。
2. **數量單位市場化**（`_qty_unit`）：COIN-M → `quote_qty`（張數）、USD-M → `qty_base`（線性，張數=幣量）。
3. **淨部位配對**：每個平倉事件依時間序消耗最早的反向開倉事件；**一個平倉事件 = 一筆交易**
   （消耗的多個開倉合併成加權均價進場）。一張大開倉被多次平倉 → 拆成多筆（每次平倉一筆）。
4. 無對應反向開倉的平倉 = orphan close（仍記一筆，保 PnL）；視窗結束殘餘開倉 = 未平倉（不出 closed trade）。

輸出 dict 形狀、合成 id（= 平倉事件首筆 fill row id × 1000）與 detail 端點反查邏輯**完全不變**。

## 驗收（golden = 使用者驗證過的 6 個 COIN-M xlsx：/Users/iruka/Documents/csv/exports/*_perp_trades.xlsx）

- **逐筆對拍 0 mismatch**：663 筆交易的 開倉/平倉時間、進場/出場價、qty(張數)、方向，全部與 golden 一致
  （時間僅差固定 +9h，因 xlsx 以 UTC+9 匯出；epoch 同一瞬間）。
- 筆數：COIN-M 664（=golden，含 BTCUSD_PERP 1 筆 orphan close），舊版 1,475 → 消除 2x。
- 守恆：全資料集（coinm+usdm）Σpnl 輸出−輸入 = +0.00000206。
- 契約：position dict keys 不變；合成 id 唯一、`id//1000` 解析回真實平倉 fill；detail 端點 round-trip ✅。

## 已知影響 / 待辦

- **USD-M 筆數 686 → 446**：同樣套用「每平倉事件一筆」顆粒度（USD-M 單位本就正確，僅顆粒度模型變）。
  USD-M 無 golden，僅以守恆 + 契約把關。如需逐筆背書要另備 USD-M 的 v7 輸出。
## 匯入路徑隱患（2026-06-05 一併修復）

aggregator 依賴「COIN-M `quote_qty` = 合約張數」這個約定。盤點三個匯入器發現不一致：

| 匯入器 | 狀態 | COIN-M `quote_qty` 原本 |
|--------|------|------------------------|
| `import_csv_trades_batch.py` | 載入現行 DB | 合約張數 ✅（aggregator 所依賴）|
| `common.py::normalize_coinm` | **無 caller**（backfill/sync 腳本不存在）| `None` ❌ |
| `import_binance_sdk.py` | **實際 live 匯入器** | `None` → insert 回退成 price×幣量=USD notional ❌，且把 `baseQty` 塞進 `qty` 鍵、**遺失真實張數** |

**已修**：
1. `import_binance_sdk.py` COIN-M 區塊：`quoteQty` 改傳 `trade['qty']`（合約張數），`qty_base` 維持 `baseQty`（幣量）。
2. `common.py::normalize_coinm`：`quote_qty` 由 `None` 改 `_f(t.get("qty"))`（canonical 正確，雖目前無 caller）。
3. `api.py::_notional`（成交量 USD）：原假設「COIN-M 無 quote_qty」對 CSV 資料已錯（quote_qty 是張數），
   改為市場感知——COIN-M 一律 `price×qty_base`，不可用 quote_qty。

現行 DB（CSV 匯入）無需 backfill；上述修復確保日後 SDK 同步入庫與 aggregator 一致。

**注意**：`import_binance_sdk.py` 另有兩個與現行 neondb schema 對不上的 bug（`ON CONFLICT (trade_id)`
去重鍵不存在、`clear_trades()` 刪不存在的 `positions` 表），**本次未修**（defer），跑該匯入器前必補。
詳見 obsidian wiki `2026-06-05-import-binance-sdk-schema-drift`。

## 其他待辦

- **USD-M 筆數 686 → 446**：同套用「每平倉事件一筆」顆粒度（USD-M 單位本就正確）。USD-M 無 golden，僅守恆+契約背書。
