# Decision Log — COIN-M PNL 计价单位换算（USD）

日期：2026-06-04
文件：`trading_journal/api.py` → `/api/summary`

## 问题

`equity_curve` 与 `kpi.futures_realized_pnl` 把三种不同计价单位的 `realized_pnl`
直接相加，没有物理意义：

| market | symbol        | realized_pnl 计价单位 | DB sum            |
|--------|---------------|----------------------|-------------------|
| coinm  | ADAUSD_PERP   | **ADA 颗数**          | -438.06 ADA       |
| coinm  | BTCUSD_PERP   | **BTC 数量**          | -0.00126 BTC      |
| usdm   | *USDT         | USDT（=USD）          | +5.92 USDT        |

旧逻辑硬加得 -432，基本是 ADA 颗数，不是美元。

## 修法

累加进 `futures_pnl` / `equity_curve` 前，把 COIN-M 的 `realized_pnl` 换算成 USD：

- 标的币取自 `trades.margin_asset`（ADA / BTC；fallback 解析 symbol 去掉 `USD_PERP`）。
- 乘以该币现价 `prices[f"{coin}USDT"]`（来自 `client.get_all_tickers()`）。
- USDM 原样（已是 USDT）。

## 关键决策

1. **用「现价」近似，非成交当时价。**
   精确版需按每笔 `trade_time` 取历史 kline 收盘价（110 笔 COIN-M fill → 多次 API
   调用），成本高，留作后续迭代。现价换算作为第一版，足以让核心数字回到合理 USD 量级。
   `klines.fetch_klines()` 已具备按时间取 kline 的能力，未来可据此升级。

2. **缺现价时排除该笔（返回 None），不回退到原始币本位数量。**
   宁可少算一笔，也不把币数量混进 USD 合计重蹈覆辙。正常情况下 ADAUSDT / BTCUSDT
   现货对都存在，不会触发。

3. **win_rate 无需换算。** 胜负只看 `realized_pnl` 正负号，USD 换算是乘正数现价，
   不改变符号，故 COIN-M 单位问题不影响胜率分类。已在代码注释说明。

## 验证（2026-06-04，现价 ADA≈$0.197 / BTC≈$63,164）

| 指标            | 旧（混合单位） | 新（USD）   |
|-----------------|---------------|------------|
| futures PNL     | -432.15       | **-159.79**|
| 曲线谷底 min    | -606.59       | **-280.15**|
| 曲线终点 last   | -432.15       | **-159.79**|

对账：ADA -438.06×0.197 ≈ -$86.5；BTC -0.00126×63164 ≈ -$79.4（**非可忽略**，
旧估计以为 BTC 极小，但 BTC 高价使其占约一半亏损）；USDM +$5.92。
合计 ≈ -$159.9，与端点输出一致（微小差异为两次调用间现价波动）。
