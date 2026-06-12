# Trading Journal SPA 部署記錄 — 2026-06-02

**目標**: 部署完整的 React SPA 前端 + FastAPI 後端到 Oracle Cloud VM

**部署地點**: Oracle VM (Ubuntu 24.04, IP: 158.179.178.105:8000)

---

## 部署流程

### 1. 前端構建

**問題**: 前端構建失敗，TypeScript 類型不符

**根本原因**: 
- `types.ts` 中的 `Analytics` 接口與後端 API 響應結構不符
- `Analytics.tsx` 訪問不存在的字段: `data.kpi`, `data.longs`, `data.shorts`
- `Dashboard.tsx` 訪問 `statistics.avg_hold_ms`（實際上在 `kpi` 中）

**修復**:
```typescript
// types.ts 更新
export interface Analytics {
  market: Market;
  kpi: {
    total_trades: number;
    avg_hold_ms: number;
    win_rate: number;
    longs: number;
    shorts: number;
    long_pct: number;
  };
  statistics: {
    total_gain_loss: number;
    trade_expectancy: number;
    avg_daily_gain: number;
    avg_daily_volume: number;
    largest_gain: number;
    total_trades_volume: number;
    avg_trades_per_day: number;
    avg_trade_win: number;
    avg_trade_loss: number;
    max_consecutive_win: number;
    max_consecutive_loss: number;
    largest_losses: number;
  };
  longs: {
    count: number;
    win_ratio: number;
    wins: number;
    losses: number;
    avg_duration_ms: number;
    total_realized_pnl: number;
    avg_win: number;
    avg_loss: number;
  };
  shorts: { /* similar */ };
}
```

**構建結果**:
```
✓ 55 modules transformed.
✓ built in 7.93s

../trading_journal/static/index.html                     0.89 kB │ gzip:     0.46 kB
../trading_journal/static/assets/index-D5WCsdUZ.css     21.56 kB │ gzip:     5.63 kB
../trading_journal/static/assets/index-DUZofcdf.js   7,392.20 kB │ gzip: 1,686.28 kB
```

### 2. 代碼提交

```
Commit: de44ebd
Message: fix: Type definitions for Analytics API response structure and rebuild frontend SPA
Branch: feature/frontend-redesign-v2
```

### 3. 同步到 VM

**方法**: TAR + SCP 傳輸

```bash
tar czf /tmp/static.tar.gz static/
scp -i /Users/iruka/Downloads/CMM.key /tmp/static.tar.gz ubuntu@158.179.178.105:~/trading-journal/
ssh -i /Users/iruka/Downloads/CMM.key ubuntu@158.179.178.105 "tar xzf static.tar.gz && rm static.tar.gz"
```

**確認**:
```
-rw-rw-r-- 1 ubuntu ubuntu 887 Jun  2 08:52 trading_journal/static/index.html
```

### 4. 服務重啟

```bash
pkill -f "uvicorn trading_journal.api"
cd ~/trading-journal && nohup python3 -m uvicorn trading_journal.api:app --host 0.0.0.0 --port 8000 > /tmp/dashboard.log 2>&1 &
```

### 5. 驗證部署

| 檢查項 | 結果 |
|--------|------|
| API 響應 | ✅ HTTP 200 |
| 前端頁面 | ✅ 加載成功 |
| HTML 結構 | ✅ 正確 |
| 資源加載 | ✅ CSS/JS 引用正確 |

---

## 訪問信息

| 項目 | 值 |
|------|-----|
| **URL** | http://158.179.178.105:8000 |
| **帳號** | admin |
| **密碼** | trading123 |
| **API 端點** | /api/* (需 HTTP Basic Auth) |

---

## 前端架構

### 新部署的 SPA 結構

```
├── index.html (887 B)
├── assets/
│   ├── index-D5WCsdUZ.css (21.56 kB, gzip 5.63 kB)
│   └── index-DUZofcdf.js  (7.3 MB, gzip 1.6 MB)
└── 由 FastAPI 在 / 路由提供
```

### 頁面清單（已實現）

| 頁面 | 路由 | 功能 |
|------|------|------|
| Dashboard | / | KPI 卡片 + 餘額甜甜圈 + 權益曲線 |
| Journal | /journal | 持倉列表（分頁 + 篩選） |
| Analytics | /analytics | 市場分析（USD-M/COIN-M/Spot） |
| PositionDetail | /positions/:id | 持倉詳情 + 交易記錄 + 指標 |

### 後端 API 被依賴的端點

| 端點 | 用途 |
|------|------|
| GET /api/summary | 儀表板 KPI |
| GET /api/analytics?market=usdm | 分析數據 |
| GET /api/positions | 持倉列表 |
| GET /api/positions/{id} | 持倉詳情 |
| GET /api/positions/{id}/klines | K 線圖 |

---

## 已知限制

1. **分析頁面** — 部分字段使用 mock 數據（`largest_gain`, `avg_daily_volume` 等）
2. **實時數據** — 依賴後端完整實現，當前使用 mock 數據
3. **K 線圖** — 依賴 `/api/positions/{id}/klines` 端點
4. **持倉聚合** — 已在後端實現（`positions_aggregation.py`）

---

## 故障排查

### 如果頁面無法加載

**檢查 1: 確認 uvicorn 運行中**
```bash
ssh -i /Users/iruka/Downloads/CMM.key ubuntu@158.179.178.105 \
  "ps aux | grep uvicorn | grep -v grep"
```

**檢查 2: 查看日誌**
```bash
ssh -i /Users/iruka/Downloads/CMM.key ubuntu@158.179.178.105 \
  "tail -50 /tmp/dashboard.log"
```

**檢查 3: 測試 API**
```bash
curl -u admin:trading123 http://158.179.178.105:8000/api/summary
```

### 如果 CSS/JS 無法加載

確認 static 文件夾存在並有正確的內容：
```bash
ssh -i /Users/iruka/Downloads/CMM.key ubuntu@158.179.178.105 \
  "ls -la ~/trading-journal/trading_journal/static/assets/"
```

---

## 下一步

- [ ] 在瀏覽器上實際測試所有頁面
- [ ] 驗證 Analytics 數據顯示
- [ ] 測試持倉詳情鑽取
- [ ] 確認實時 API 調用成功
- [ ] 考慮配置 nginx 反向代理 + HTTPS
- [ ] 設定定期備份和監控

---

**部署日期**: 2026-06-02  
**部署人員**: Claude Opus (assisted by Haiku Pilot diagnosis)  
**狀態**: ✅ 完成

