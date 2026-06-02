# Trading Journal — Frontend (SPA)

React + Vite + TypeScript + Tailwind 单页应用，消费后端 `/api/*` JSON。
设计源：Stitch「Obsidian Trade」设计系统（glassmorphism + 暗色终端美学）。

## 开发

```bash
npm install
# 另开一个终端跑后端：python3 -m uvicorn trading_journal.api:app --port 8000
npm run dev        # Vite :5173，/api 自动代理到 :8000
```

浏览器首次访问会弹 HTTP Basic 认证（用 .env 的 DASHBOARD_USER/PASS）。

## 构建（部署）

```bash
npm run build      # tsc 类型检查 + vite 打包 → ../trading_journal/static/
```

生产由 FastAPI（`trading_journal.api:app`）托管 `static/`，同源，无需额外 web server。

## 结构

| 路径 | 说明 |
|------|------|
| `src/api/` | API client + 类型（与后端 `/api/*` 契约对齐） |
| `src/components/` | GlassCard / Badge / KpiCard / Gauge / Nav / StateBlock |
| `src/components/charts/` | PlotlyChart（统计图）· CandleChart（lightweight-charts 蜡烛） |
| `src/pages/` | Dashboard · Journal · PositionDetail · Analytics · Profile |
| `src/lib/` | format（数值/时间/PNL 着色）· useApi（数据 hook） |
| `tailwind.config.js` | Stitch 设计 token（颜色/字体/间距/圆角） |

## 设计 token 来源

`tailwind.config.js` 的颜色、字体、间距直接来自
`stitch_trading_journal_design_system/obsidian_trade/DESIGN.md` 的 frontmatter。
玻璃卡 / pill / glow 规则在 `src/index.css` 的 `@layer components`。
