# 前端重做规格（Stitch 设计接入）

> 本文档锁定 2026-06-01 grill 出来的设计决策，作为执行依据。
> 设计源：`stitch_trading_journal_design_system`（Stitch 导出，7 屏，3 屏有 HTML，4 屏仅截图）。

## 一、背景：Stitch 产出的真相

Stitch 给的是**静态视觉稿**，不是能跑的前端：

- 所有数据写死（`ADAUSD_PERP`、`-218`、`+$40.96` 全是 placeholder）。
- 「图表」是 googleusercontent 的 **PNG 图片**，不是真图表。
- 用 Tailwind CDN + Material Symbols + Inter/JetBrains，附带完整 `tailwind.config`（设计 token）。
- 6 个页面只有 3 个有 HTML：`detailed_trade_record`、`trade_analysis_reports`、`traded_symbols_report`；
  `multi_exchange_dashboard`、`performance_stats`、`performance_drawdown_analysis` 仅截图。

所以「替换前端」= 拿 Stitch 的结构/样式当新模板 + 把数据重新接到真实 DB + 假图换真图表。

## 二、锁定的决策（11 项）

| # | 决策点 | 选择 |
|---|--------|------|
| 1 | 集成架构 | **SPA**（React + Vite + TS + Tailwind）消费 JSON API |
| 2 | 数据缺口处理顺序 | **先补后端数据，再做前端** |
| 3 | 持仓模型（fill→position） | **净持仓归零边界**（净仓离开 0 = 开仓，回到 0 = 平仓，中间加减仓算同一持仓） |
| 4 | K 线/MAE-MFE 行情来源 | **按需拉取 + 缓存**（打开持仓详情时按 open→close 窗口拉 Binance K 线） |
| 5 | 资金费 | **采集**，复用现有 income 接口（`incomeType=FUNDING_FEE`） |
| 6 | 仪表盘指标定义 | **用 MAE/MFE 推导**：OpportunityCapture = 已实现盈亏 / MFE；EntryQuality = 1 − 入场初期 MAE / 持仓区间总波幅 |
| 7 | 前端框架 | **React + Vite + TypeScript + Tailwind** |
| 8 | 部署形态 | **同仓库** `frontend/` → 打包 `dist/` → FastAPI `StaticFiles` 托管；**保留 HTTP Basic**（同源） |
| 9 | 图表库 | **lightweight-charts**（蜡烛详情） + **Plotly/react-plotly.js**（统计图） |
| 10 | 主 Dashboard | **只做 Binance**；新增**账户余额采集**（Binance account 接口）；Flipster 延后 |
| 11 | calendar / Profile | calendar **暂不做**；Profile **做成设置/筛选页** |

## 三、页面信息架构（IA）

底部导航 4 tab（沿用 Stitch）：

```
Dashboard ──── multi_exchange_dashboard（仅 Binance）
              余额卡 + 权益曲线 + KPI（总交易/胜率/总 PNL）
Journal ────── trade_analysis_reports + traded_symbols_report
              多空对比 / 按日·按时 / 时长 / 规模报告 + 每 symbol 卡片
              └─ 点卡片进入 → detailed_trade_record（单持仓详情）
Analytics ──── performance_stats + performance_drawdown_analysis
              权益/回撤曲线（PNL⇄Drawdown 切换）+ 期望值环 + 多空比 + 统计
Profile ────── 自建：账户信息 / 默认筛选偏好 / 数据新鲜度 + 同步状态
```

> Analytics 注脚沿用 Stitch："All metrics calculated from futures and perpetual trades."
> （现货无真实 realized_pnl，统计仅含合约；现货另用 FIFO 估算单列。）

## 四、后端工程（先做，是关键路径）

### 4.1 持仓聚合 `positions`

净持仓归零边界算法，按 `(exchange, market, symbol)` 时间序遍历 fill：

- 维护 running 净仓位 `net_qty`（BUY +、SELL −；合约用 `position_side` 校正）。
- `net_qty` 从 0 离开 → 新持仓开始；回到 0 → 持仓结束。
- 合约：区间内 `realized_pnl` 累加 = 该持仓总盈亏；区间内 `fee`、funding 累加。
- 现货：区间内用 FIFO 算成本与已实现盈亏（复用现有逻辑）。
- 产出字段：`open_time/close_time/hold_ms/side(Long/Short)/avg_entry/avg_exit/qty/realized_pnl/fees/funding/mae/mfe/entry_quality/opportunity_capture`。

实现：建 **`positions` 派生表**（从 `trades` 重建，幂等），detail 首次打开时计算 MAE/MFE + gauges 并回写缓存。

### 4.2 账户余额采集 `balances`

- sync 时调 Binance account 接口（现货 `get_account` / 合约 `futures_account_balance`），
  存 `balances(exchange, asset, free, locked, equity, snapshot_time)`。
- Dashboard「Binance Balance」与「Consolidated PNL」从此表 + positions 汇总。

### 4.3 资金费 `funding`

- backfill/sync 多拉 `incomeType=FUNDING_FEE`，按 symbol 归一化存入 `funding` 表（或 trades 加类型字段）。
- 持仓详情按 open→close 区间求和 = Total Funding。

### 4.4 K 线按需 + MAE/MFE

- `GET /api/positions/{id}/klines`：按持仓时间窗调 Binance klines，内存/磁盘缓存。
- MAE/MFE = 持仓窗口内对不利/有利方向的最大价差（按 side）。算完缓存到 positions 行。

### 4.5 新 API 契约（前缀 `/api`）

| 端点 | 用途 |
|------|------|
| `GET /api/summary` | Dashboard：余额、权益曲线、KPI |
| `GET /api/positions` | 持仓列表（filter: market/symbol/side/日期；分页） |
| `GET /api/positions/{id}` | 持仓详情：开平/hold/PNL/fees/funding/MAE-MFE/gauges |
| `GET /api/positions/{id}/klines` | 按需 K 线 |
| `GET /api/analytics` | 权益+回撤序列、期望值(W/L)、多空比、统计 |
| `GET /api/reports` | 多空对比、按日、按时、时长桶、规模桶 |
| `GET /api/symbols` | 每 symbol 聚合 |
| `GET /trades`、`/trades/stats` | 保留：原始 fill 访问 |

所有 `/api/*` 沿用现有 HTTP Basic 依赖。

## 五、前端工程（后端就绪后）

```
frontend/
  src/
    main.tsx
    api/client.ts            # fetch wrapper（同源，带 Basic）
    theme/tailwind.config.ts # 直接搬 Stitch 的 config（colors/fonts/spacing/radius）
    components/
      GlassCard.tsx          # rgba(14,22,38,.7)+blur(24px)+ghost border
      Badge.tsx              # Buy/Sell pill、市场 tag(USD-M/SPOT)
      KpiCard.tsx
      charts/CandleChart.tsx # lightweight-charts，透明背景+glow
      charts/EquityChart.tsx # react-plotly.js
    pages/
      Dashboard.tsx
      Journal.tsx            # 报告 + symbol 卡片，路由到详情
      PositionDetail.tsx
      Analytics.tsx
      Profile.tsx
    nav/BottomNav.tsx        # mobile 浮岛；desktop sidebar
  vite.config.ts             # build outDir → ../trading_journal/static
```

- 设计 token 全部从 `DESIGN.md` 的 frontmatter 搬入 tailwind config（已存在于 Stitch HTML 的 `tailwind-config` 脚本，可直接抄）。
- 玻璃卡、徽章、按钮、导航的样式规范见 `DESIGN.md` 的 Components 段。
- FastAPI 端：`app.mount("/", StaticFiles(directory="static", html=True))`，并把 `/api/*` 与 `/trades*` 放在 mount 之前。

## 六、执行顺序

1. **后端**：positions 聚合 → balances 采集 → funding 采集 → klines 按需 + MAE/MFE → `/api/*` 契约。
2. **前端脚手架**：Vite+React+TS+Tailwind，搬 Stitch token 与组件样式。
3. **逐页接数据**：Dashboard → Journal/列表 → PositionDetail → Analytics → Profile。
4. **集成**：vite build → static/，FastAPI 托管，Basic 验证，systemd 重启。

## 七、明确延后（不在本次范围）

- Flipster 接入（Consolidated PNL 暂仅 Binance）。
- calendar 热力图。
- klines 全量存库（采用按需拉取替代）。

## 八、实施进度（2026-06-01 首轮）

### 后端 ✅ 已完成（代码层，待 VM 上跑数据）

| 模块 | 文件 | 状态 |
|------|------|------|
| 持仓聚合（净持仓归零边界） | `trading_journal/positions.py` | ✅ 8 场景单测通过 |
| 资金费采集（FUNDING_FEE） | `common.py` `fetch_funding/upsert_funding` | ✅ |
| 账户余额快照 | `common.py` `snapshot_balances` | ✅ |
| 接入 backfill / sync | `backfill_binance.py` / `sync_binance.py` | ✅ |
| K 线按需拉取 + MAE/MFE + gauges | `trading_journal/klines.py` | ✅ |
| `/api/*` 契约 + SPA 托管 | `trading_journal/api.py` | ✅ TestClient 通过 |

> 派生表 `positions`/`funding`/`balances` 由代码自动建。
> 部署后需在 VM 跑一次 `backfill_binance`（或 `python -m trading_journal.positions` 重建持仓），
> 之后每日 `sync_binance` 增量 + 重建持仓 + 余额快照。

### 前端 ✅ 脚手架 + 全 4 tab 已接 API（`tsc && vite build` 通过）

```
frontend/  React18 + Vite5 + TS + Tailwind3（Stitch token 已搬入 tailwind.config.js）
  src/components/  GlassCard / Badge / KpiCard / Gauge / Nav / StateBlock
                   charts/PlotlyChart（统计图）· charts/CandleChart（lightweight-charts 蜡烛）
  src/pages/       Dashboard · Journal(+symbols) · PositionDetail · Analytics · Profile
  src/api/         client.ts + types.ts（与 /api/* 契约对齐）
```

- 构建产物 → `trading_journal/static/`，FastAPI `api:app` 托管 + SPA 路由兜底。
- 已知：Plotly 使包体偏大（gzip ~1.5MB），后续可代码分割 / 懒加载详情页蜡烛。

### 下一步（待用户在 VM 验证数据后迭代）
- 跑 backfill 填 positions/funding/balances，逐页用真实数据核对数值口径。
- `trade_analysis_reports` 的「按日/按时/时长/规模」四图接 `/api/reports`（接口已就绪，前端报告区块待补）。
- 视觉细调：对照 Stitch 截图微调间距/glow/字号。
</content>
</invoke>
