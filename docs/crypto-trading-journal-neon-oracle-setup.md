# Crypto Trading Journal — Neon PostgreSQL 與 Oracle VM 部署全記錄

> 本記錄詳細記載 Neon PostgreSQL 專案建立、Oracle Cloud VM 設定、以及部署過程中遇到的難題與解法。

---

## 1. Neon PostgreSQL 設定

### 1.1 建立專案

在 [console.neon.tech](https://console.neon.tech) 建立新專案：

| 設定項目 | 內容 |
|----------|------|
| Project Name | `Tradingjournal` |
| Region | `AWS ap-southeast-1`（新加坡）|
| Postgres Version | 17 |
| Neon Auth | **OFF**（VM 直接用密碼連線，不需 Neon Auth）|

### 1.2 連線字串

```
postgresql://neondb_owner:npg_PQl1Rn2HewMk@ep-morning-sunset-ao0clhy4-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

**注意**：Neon 的 host 是 **pooler 位址**（`pooler` 在 subdomain 中），不是直接 `ep-morning-sunset-ao0clhy4`。

### 1.3 VM 端的 DATABASE_URL

```bash
DATABASE_URL=postgresql://neondb_owner:npg_PQl1Rn2HewMk@ep-morning-sunset-ao0clhy4-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

---

## 2. Oracle Cloud VM 設定

### 2.1 Instance 規格

| 設定項目 | 內容 |
|----------|------|
| Name | `Trading CMM` |
| OS | Canonical Ubuntu 24.04 Minimal |
| Image Build | 2026.04.30-1 |
| Shape | VM.Standard.E2.1.Micro（1 OCPU / 1 GB）|
| Boot Volume | 預設（50 GB）|
| VPU | 1 |
| Use in-transit encryption | Enabled |
| Public IPv4 | Yes |
| SSH Keys | 自生成金鑰對（`CMM.key`）|

### 2.2 VM 公網資訊

| 項目 | 內容 |
|------|------|
| 公網 IP | `158.179.178.105` |
| SSH 私鑰 | `/Users/iruka/Downloads/CMM.key` |
| SSH 用戶名 | `ubuntu` |
| OS | Ubuntu 24.04.4 LTS |

### 2.3 連線方式

```bash
chmod 400 /Users/iruka/Downloads/CMM.key
ssh -i /Users/iruka/Downloads/CMM.key ubuntu@158.179.178.105
```

---

## 3. VM 初始化步驟

### 3.1 更新系統 + 安裝 Python 環境

```bash
# 更新系統（背景已跑，確認 lock 釋放後執行）
sudo apt-get update -qq

# 安裝 pip（Ubuntu 24.04 Minimal 可能已有但無 pip）
sudo apt-get install -y python3-pip

# 安裝 Python 套件
python3 -m pip install --break-system-packages \
  fastapi uvicorn psycopg2-binary python-dotenv requests \
  python-dateutil pytz httpx
```

### 3.2 建立專案目錄

```bash
mkdir -p ~/trading-journal
```

### 3.3 建立 `.env` 檔案

路徑：`~/trading-journal/.env`

```bash
DATABASE_URL=postgresql://neondb_owner:npg_PQl1Rn2HewMk@ep-morning-sunset-ao0clhy4-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require

BINANCE_API_KEY=
BINANCE_API_SECRET=

FLIPSTER_API_KEY=
FLIPSTER_API_SECRET=
FLIPSTER_BASE_URL=https://trading-api.flipster.io

DASHBOARD_USER=admin
DASHBOARD_PASS=cm2025
```

---

## 4. 資料庫 Schema 初始化

在 VM 上執行 `init_db.py`（已在 `/home/ubuntu/trading-journal/init_db.py`）建立四張 table：

| Table | 用途 |
|-------|------|
| `binance_spot_trades` | Binance 現貨交易記錄 |
| `binance_coinm_trades` | Binance COIN-M 幣本位合約交易記錄 |
| `flipster_trades` | Flipster Prex USDT 合約交易記錄 |
| `sync_status` | 同步狀態追蹤（斷點續傳）|

---

## 5. 同步腳本

已在 `/home/ubuntu/trading-journal/` 建立的腳本：

| 檔案 | 內容 |
|------|------|
| `sync_binance_spot.py` | Binance 現貨 myTrades 同步 |
| `sync_binance_coinm.py` | Binance COIN-M myTrades 同步 |
| `sync_flipster.py` | Flipster Prex Trading API 同步 |

**使用方式**（等 API Key 設定後）：
```bash
cd ~/trading-journal
python3 sync_binance_spot.py
python3 sync_binance_coinm.py
python3 sync_flipster.py
```

---

## 6. FastAPI Dashboard 部署

### 6.1 啟動

```bash
cd ~/trading-journal
nohup python3 -m uvicorn dashboard:app --host 0.0.0.0 --port 8000 > /tmp/dashboard.log 2>&1 &
```

### 6.2 Dashboard 路由

| 路由 | 說明 |
|------|------|
| `/` | API Root，純 JSON |
| `/summary` | Summary 總覽頁（需登入）|
| `/performance` | Performance 績效頁（需登入）|
| `/analytics` | Analytics 分析頁（需登入）|
| `/calendar` | Calendar 日曆頁（需登入）|

### 6.3 登入資訊

```
URL: http://158.179.178.105:8000/
帳號: admin
密碼: cm2025
```

---

## 7. 部署難題與解法

### 難題 1：Neon 連線密碼錯誤

**問題**：
```
psycopg2.OperationalError: password authentication failed for user 'neondb_owner'
```

**原因**：使用了錯誤的 Neon 密碼。Neon 的密碼格式是 `npg_...`，不是一般自設密碼。

**解法**：使用 Neon Console 提供的完整連線字串（pooler endpoint）。

---

### 難題 2：Neon 不支援 SNI（Endpoint ID 未指定）

**問題**：
```
psycopg2.OperationalError: endpoint ID is not specified. Either please upgrade the postgres client library (libpq) for SNI support or pass the endpoint ID as a parameter
```

**原因**：舊版 libpq 不支援 SNI，Neon 需要在連線字串中明確指定 endpoint ID。

**解法**：在 `~/.env` 的 `DATABASE_URL` 中加入 `?options=endpoint%3Dep-morning-sunset-ao0clhy4` 參數。但後來發現使用 **pooler endpoint**（`pooler.c-2.ap-southeast-1.aws.neon.tech`）就完全不需要這個參數，直接用：

```
postgresql://.../neondb?sslmode=require&channel_binding=require
```

---

### 難題 3：Oracle 防火牆 port 8000 未開

**問題**：外部無法存取 `http://158.179.178.105:8000`，curl 回 timeout。

**解法**：
1. Oracle Cloud Console → Compute → Instances → Trading CMM → Networking
2. 進入 Subnet → Security Lists → 點「Add Ingress Rules」：
   - Source CIDR: `0.0.0.0/0`
   - IP Protocol: `TCP`
   - Destination Port Range: `8000`
3. 儲存後 VM 外部即可存取。

---

### 難題 4：VM 內部 iptables 阻擋外部流量

**問題**：Oracle 防火牆已開 port，但 VM 內部的 `iptables` REJECT 規則仍然阻擋外部流量。

**發現方式**：
```bash
# 在 VM 內部測試正常（127.0.0.1:8000 OK）
curl http://localhost:8000/  # 200 OK

# 從外部測試失敗（公網 IP:8000 timeout）
# VM 內執行 socket 測試
python3 -c "import socket; print(socket.socket().connect_ex(('158.179.178.105', 8000)))"  # 回傳 113
```

**解法**：
```bash
# 在 VM 上執行，插入一條 ACCEPT 規則到 REJECT 規則之前
sudo iptables -I INPUT 4 -p tcp --dport 8000 -j ACCEPT
```

**驗證**：
```bash
# 外部測試
curl -s -o /dev/null -w "%{http_code}" http://158.179.178.105:8000/
# 回傳 200 ✓
```

---

### 難題 5：Ubuntu 24.04 Minimal 無 pip

**問題**：`pip3: command not found`

**原因**：Ubuntu 24.04 Minimal 預設不安裝 pip。

**解法**：
```bash
sudo apt-get install -y python3-pip
# pip3 會被安裝為 pip 但需要用 python3 -m pip 執行
```

---

## 8. 目前進度

### 已完成

- [x] Neon PostgreSQL 專案建立（Project: Tradingjournal）
- [x] Oracle Cloud VM 建立（Ubuntu 24.04，IP: 158.179.178.105）
- [x] VM SSH 連線驗證
- [x] VM Python 環境安裝
- [x] VM `.env` 設定（DATABASE_URL 已填入）
- [x] Neon 資料庫 Schema 建立（四張 table）
- [x] 三個交易所同步腳本寫入 VM
- [x] FastAPI Dashboard 部署（port 8000）
- [x] Oracle 防火牆 Ingress Rule 開 port 8000
- [x] VM 內部 iptables 修正
- [x] Dashboard HTTP Basic Auth 驗證完成

### 待完成

- [ ] 申請 Binance API Key（需 VM IP `158.179.178.105` 加入白名單）
- [ ] 填入 `BINANCE_API_KEY` 和 `BINANCE_API_SECRET` 到 VM 的 `.env`
- [ ] 申請 Flipster API Key（用戶已有 Key `3|...`，尚未設定進 VM）
- [ ] 執行第一次完整同步（`python3 sync_binance_spot.py && sync_binance_coinm.py && sync_flipster.py`）
- [ ] 驗證 Dashboard 有資料顯示
- [ ] 設定每週 Cron Job 自動同步
- [ ] 設定 Telegram Bot 通知
