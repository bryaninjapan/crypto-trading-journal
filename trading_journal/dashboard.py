"""
Trading Journal Dashboard — 统一 trades 表版
数据源：Neon `trades` 单表（exchange, market[spot|usdm|coinm], realized_pnl 等）。
设计：三市场各自分区、原生单位。
  - USD-M  : 真实 realized_pnl(USDT)
  - COIN-M : 真实 realized_pnl，按 symbol 币本位（BTC / ADA …）
  - 现货   : 无 realized_pnl，FIFO 配对估算(USDT)
手续费按 fee_asset 分别汇总，不跨币种相加。
Pages: Summary | Performance | Analytics | Calendar
JSON API: /trades  /trades/stats
"""

import os

import pandas as pd
import plotly.graph_objects as go
from plotly.io import to_html
from psycopg2 import connect
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse

load_dotenv()
app = FastAPI()
security = HTTPBasic()


def get_user():
    def inner(cred: HTTPBasicCredentials = Depends(security)):
        if cred.username != os.getenv("DASHBOARD_USER") or cred.password != os.getenv("DASHBOARD_PASS"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return cred
    return inner


def db():
    return connect(os.getenv("DATABASE_URL"))


def q(conn, sql, args=(), single=False):
    cur = conn.cursor()
    cur.execute(sql, args)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    if single:
        return dict(zip(cols, rows[0])) if rows else None
    return [dict(zip(cols, r)) for r in rows]


# ─── Theme ────────────────────────────────────────────────────────────────────
BG = "#0d1117"
CARD_BG = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#58a6ff"
GREEN = "#3fb950"
RED = "#f85149"
FONT = "Segoe UI, sans-serif"

MARKET_LABEL = {"usdm": "USD-M", "coinm": "COIN-M", "spot": "现货"}


def card(h2, content):
    return f'<div class="card"><h2>{h2}</h2>{content}</div>'


def grid(items):
    return '<div class="grid">' + "".join(items) + "</div>"


PLOTLY_OPTS = dict(responsive=True, displayModeBar=False)


def plot(fig, height=320):
    return to_html(fig, full_html=False, include_plotlyjs=False,
                   config={**PLOTLY_OPTS, "height": height})


def init_plot():
    return go.Figure().update_layout(
        paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TEXT, family=FONT),
        margin=dict(l=16, r=16, t=16, b=16),
    )


def theme_fig(fig):
    fig.update_layout(
        paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TEXT, family=FONT),
        legend=dict(bgcolor=BG, font=dict(color=MUTED)),
        xaxis=dict(color=MUTED, gridcolor=BORDER, showgrid=True, gridwidth=1),
        yaxis=dict(color=MUTED, gridcolor=BORDER, showgrid=True, gridwidth=1),
        hoverlabel=dict(bgcolor=CARD_BG, font=dict(color=TEXT)),
    )
    fig.update_xaxes(tickfont=dict(color=MUTED))
    fig.update_yaxes(tickfont=dict(color=MUTED))
    return fig


# ─── Page Template ─────────────────────────────────────────────────────────────
TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Journal — {title}</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{BG};color:{TEXT};font-family:{FONT};padding:20px;min-height:100vh}}
.nav{{display:flex;gap:16px;padding:16px 0;border-bottom:1px solid {BORDER};margin-bottom:28px}}
.nav a{{color:{MUTED};text-decoration:none;font-size:15px;transition:color .2s}}
.nav a:hover,.nav a.active{{color:{TEXT}}}
.nav a.active{{border-bottom:2px solid {ACCENT};padding-bottom:2px}}
h1{{font-size:20px;margin-bottom:20px;color:{TEXT};font-weight:600}}
h3{{font-size:14px;margin:24px 0 12px;color:{TEXT};font-weight:600}}
.card{{background:{CARD_BG};border:1px solid {BORDER};border-radius:8px;padding:20px;margin-bottom:16px}}
.card h2{{font-size:11px;color:{MUTED};text-transform:uppercase;letter-spacing:1.2px;margin-bottom:14px;font-weight:500}}
.metric{{font-size:30px;font-weight:700;margin-bottom:2px;letter-spacing:-.5px}}
.up{{color:{GREEN}}} .down{{color:{RED}}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 12px;border-bottom:1px solid {BORDER};color:{MUTED};font-size:10px;text-transform:uppercase;letter-spacing:.8px;font-weight:500}}
td{{padding:9px 12px;border-bottom:1px solid #21262d;color:{TEXT}}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#1c2128}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}}
.badge-buy{{background:rgba(63,185,80,.15);color:{GREEN}}}
.badge-sell{{background:rgba(248,81,73,.15);color:{RED}}}
.badge-mkt{{background:rgba(88,166,255,.15);color:{ACCENT}}}
.two-col{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.kpi{{flex:1;min-width:160px;background:{CARD_BG};border:1px solid {BORDER};border-radius:8px;padding:16px}}
.kpi label{{display:block;font-size:10px;text-transform:uppercase;letter-spacing:1px;color:{MUTED};margin-bottom:6px}}
.kpi .val{{font-size:22px;font-weight:700;color:{TEXT}}}
.kpi .val.up{{color:{GREEN}}} .kpi .val.down{{color:{RED}}}
.empty{{color:{MUTED};font-size:13px;text-align:center;padding:40px 0}}
.note{{color:{MUTED};font-size:12px;margin-top:8px;line-height:1.5}}
</style>
</head>
<body>
<nav class="nav">
  <a href="/summary">Summary</a>
  <a href="/performance">Performance</a>
  <a href="/analytics">Analytics</a>
  <a href="/calendar">Calendar</a>
</nav>
<h1>{title}</h1>
{body}
</body>
</html>"""


def page(title, body):
    return TEMPLATE.format(title=title, body=body,
                           BG=BG, CARD_BG=CARD_BG, BORDER=BORDER,
                           TEXT=TEXT, MUTED=MUTED, ACCENT=ACCENT,
                           GREEN=GREEN, RED=RED, FONT=FONT)


# ─── 数据加载（统一 trades 表）─────────────────────────────────────────────────
TRADE_COLS = ["price", "qty_base", "quote_qty", "realized_pnl", "fee"]


def load_trades(conn, market=None):
    sql = """
        SELECT market, symbol, side, price, qty_base, quote_qty,
               realized_pnl, fee, fee_asset, margin_asset, position_side,
               trade_id, order_id, is_maker, trade_time
        FROM trades WHERE exchange = 'binance'
    """
    args = ()
    if market:
        sql += " AND market = %s"
        args = (market,)
    sql += " ORDER BY trade_time ASC"
    rows = q(conn, sql, args)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["trade_time"], unit="ms")
    df["date"] = df["ts"].dt.date
    for col in TRADE_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["fee"] = df["fee"].fillna(0)
    return df


# ─── PnL 工具 ──────────────────────────────────────────────────────────────────
def closing_fills(df):
    """合约平仓成交 = realized_pnl != 0 的行。"""
    if df.empty or "realized_pnl" not in df.columns:
        return pd.DataFrame()
    return df[df["realized_pnl"].fillna(0) != 0].copy()


def cum_curve(daily_series):
    """日聚合 → 累计曲线（从 0 起算的累计已实现盈亏）。"""
    s = daily_series.sort_index()
    s.index = pd.to_datetime(s.index)
    return s.cumsum()


def max_drawdown_abs(cum):
    """累计盈亏曲线的最大回撤（绝对额，峰到谷）。"""
    if cum.empty:
        return 0.0
    peak = cum.cummax()
    return float((cum - peak).min())


def winloss(closed):
    """基于 realized_pnl 符号统计胜负（按平仓成交计，非按持仓）。"""
    wins = int((closed["realized_pnl"] > 0).sum()) if not closed.empty else 0
    losses = int((closed["realized_pnl"] < 0).sum()) if not closed.empty else 0
    decided = wins + losses
    wr = wins / decided * 100 if decided else 0.0
    return wins, losses, wr


def fee_breakdown(df):
    """按 fee_asset 分别汇总手续费，避免跨币种相加。"""
    if df.empty:
        return "—"
    g = df.groupby("fee_asset")["fee"].sum()
    parts = [f"{v:.4f} {a}" for a, v in g.items() if v and a]
    return ", ".join(parts) if parts else "—"


def estimate_spot_pnl(df):
    """现货无 realized_pnl：按 symbol FIFO 配对 BUY/SELL，估算每笔已实现盈亏(USDT，已扣手续费)。"""
    if df.empty:
        return pd.DataFrame()
    df = df.sort_values("ts").copy()
    records = []
    for symbol, grp in df.groupby("symbol"):
        grp = grp.sort_values("ts")
        lots = []  # FIFO 队列：[{price, qty, fee}]
        for _, row in grp.iterrows():
            qty = float(row["qty_base"])
            price = float(row["price"])
            fee = float(row["fee"]) if pd.notna(row["fee"]) else 0.0
            if row["side"] == "BUY":
                lots.append({"price": price, "qty": qty, "fee": fee})
            else:  # SELL：从最旧的 BUY 批次扣
                remain = qty
                cost = 0.0
                buy_fee = 0.0
                while remain > 1e-12 and lots:
                    lot = lots[0]
                    take = min(remain, lot["qty"])
                    cost += take * lot["price"]
                    buy_fee += lot["fee"] * (take / lot["qty"]) if lot["qty"] else 0.0
                    lot["qty"] -= take
                    remain -= take
                    if lot["qty"] <= 1e-12:
                        lots.pop(0)
                proceeds = (qty - remain) * price
                pnl = proceeds - cost - fee - buy_fee
                roi = pnl / cost * 100 if cost else 0.0
                records.append({
                    "symbol": symbol, "side": "SELL", "ts": row["ts"], "date": row["date"],
                    "price": price, "qty_base": qty, "pnl": pnl, "roi": roi,
                    "trade_id": row["trade_id"],
                })
    return pd.DataFrame(records)


# ─── 区块构造 ──────────────────────────────────────────────────────────────────
def kpi(label, value, cls=""):
    return f'<div class="kpi"><label>{label}</label><div class="val {cls}">{value}</div></div>'


def usdt_equity_section(title, daily_pnl_series, color):
    """给定 date→pnl 的 Series，返回累计曲线 HTML（USDT）。"""
    if daily_pnl_series.empty:
        return '<div class="empty">无数据</div>'
    cum = cum_curve(daily_pnl_series)
    if cum.empty:
        return '<div class="empty">无数据</div>'
    fig = init_plot()
    fig.add_trace(go.Scatter(
        x=cum.index, y=cum.values, mode="lines", fill="tozeroy",
        line=dict(color=color, width=2),
        fillcolor="rgba(88,166,255,.10)",
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>累计: $%{y:,.2f}<extra></extra>",
    ))
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return plot(theme_fig(fig), height=280)


def daily_bars(daily_pnl_series):
    if daily_pnl_series.empty or len(daily_pnl_series) < 1:
        return '<div class="empty">无数据</div>'
    s = daily_pnl_series.sort_index()
    colors = [GREEN if v >= 0 else RED for v in s.values]
    fig = init_plot()
    fig.add_trace(go.Bar(
        x=pd.to_datetime(s.index), y=s.values, marker_color=colors,
        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>$%{y:,.2f}<extra></extra>",
    ))
    fig.update_yaxes(tickprefix="$", tickformat=",.2f")
    return plot(theme_fig(fig), height=220)


def winloss_pie(wins, losses):
    if wins + losses == 0:
        return '<div class="empty">无平仓成交</div>'
    fig = init_plot()
    fig.add_trace(go.Pie(
        labels=["盈", "亏"], values=[wins, losses],
        marker=dict(colors=[GREEN, RED]),
        textinfo="label+value+percent", hole=0.45,
    ))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-.1))
    return plot(theme_fig(fig), height=240)


# ─── Page: Summary ─────────────────────────────────────────────────────────────
@app.get("/summary", dependencies=[Depends(get_user())])
def summary():
    conn = db()
    df = load_trades(conn)
    conn.close()

    if df.empty:
        return HTMLResponse(page("Summary", '<div class="empty">trades 表暂无数据</div>'))

    counts = df["market"].value_counts().to_dict()
    start, end = df["date"].min(), df["date"].max()
    days_span = (pd.to_datetime(end) - pd.to_datetime(start)).days + 1

    kpis = grid([
        kpi("总成交笔数", f"{len(df):,}"),
        kpi("USD-M", f"{counts.get('usdm', 0):,}"),
        kpi("COIN-M", f"{counts.get('coinm', 0):,}"),
        kpi("现货", f"{counts.get('spot', 0):,}"),
        kpi("交易对数", f"{df['symbol'].nunique()}"),
        kpi("周期", f"{start} → {end}（{days_span}d）"),
    ])

    # 各市场盈亏小结
    cards = []
    # USD-M
    usdm = df[df["market"] == "usdm"]
    if not usdm.empty:
        closed = closing_fills(usdm)
        pnl = closed["realized_pnl"].sum() if not closed.empty else 0.0
        cls = "up" if pnl >= 0 else "down"
        cards.append(card("USD-M 已实现盈亏",
                          f'<div class="metric {cls}">${pnl:+,.2f}</div>'
                          f'<div class="note">手续费 {fee_breakdown(usdm)}</div>'))
    # COIN-M（按 symbol 币本位）
    coinm = df[df["market"] == "coinm"]
    if not coinm.empty:
        lines = []
        for sym, grp in coinm.groupby("symbol"):
            closed = closing_fills(grp)
            pnl = closed["realized_pnl"].sum() if not closed.empty else 0.0
            unit = grp["margin_asset"].dropna().iloc[0] if grp["margin_asset"].notna().any() else ""
            col = GREEN if pnl >= 0 else RED
            lines.append(f'<div style="margin-bottom:6px">{sym}: '
                         f'<span style="color:{col};font-weight:600">{pnl:+.6f} {unit}</span></div>')
        cards.append(card("COIN-M 已实现盈亏（币本位）", "".join(lines)))
    # 现货
    spot = df[df["market"] == "spot"]
    if not spot.empty:
        est = estimate_spot_pnl(spot)
        pnl = est["pnl"].sum() if not est.empty else 0.0
        cls = "up" if pnl >= 0 else "down"
        cards.append(card("现货 估算盈亏（FIFO·USDT）",
                          f'<div class="metric {cls}">${pnl:+,.2f}</div>'
                          f'<div class="note">FIFO 配对估算，非交易所口径</div>'))

    # 最近成交
    recent = df.sort_values("ts", ascending=False).head(12)
    rows_html = ""
    for _, r in recent.iterrows():
        badge = "badge-buy" if r["side"] == "BUY" else "badge-sell"
        rp = "" if pd.isna(r["realized_pnl"]) or r["realized_pnl"] == 0 else f"{r['realized_pnl']:+.4f}"
        rows_html += f"""<tr>
            <td><span class="badge badge-mkt">{MARKET_LABEL.get(r['market'], r['market'])}</span></td>
            <td>{r['symbol']}</td>
            <td><span class="badge {badge}">{r['side']}</span></td>
            <td style="text-align:right">{r['price']:,.4f}</td>
            <td style="text-align:right">{r['qty_base']:.6f}</td>
            <td style="text-align:right">{rp}</td>
            <td>{r['ts'].strftime('%m-%d %H:%M')}</td>
        </tr>"""
    recent_html = f"""<table>
        <tr><th>市场</th><th>交易对</th><th>方向</th><th style="text-align:right">价格</th>
        <th style="text-align:right">数量</th><th style="text-align:right">已实现</th><th>时间</th></tr>
        {rows_html}</table>"""

    body = kpis + grid(cards) + card("最近成交", recent_html)
    return HTMLResponse(page("Summary", body))


# ─── Page: Performance ─────────────────────────────────────────────────────────
@app.get("/performance", dependencies=[Depends(get_user())])
def performance():
    conn = db()
    df = load_trades(conn)
    conn.close()

    if df.empty:
        return HTMLResponse(page("Performance", '<div class="empty">trades 表暂无数据</div>'))

    sections = []

    # ── USD-M（USDT 真实 realized_pnl）──
    usdm = df[df["market"] == "usdm"]
    if not usdm.empty:
        closed = closing_fills(usdm)
        total_pnl = closed["realized_pnl"].sum() if not closed.empty else 0.0
        wins, losses, wr = winloss(closed)
        avg_win = closed[closed["realized_pnl"] > 0]["realized_pnl"].mean() if wins else 0.0
        avg_loss = closed[closed["realized_pnl"] < 0]["realized_pnl"].mean() if losses else 0.0
        rr = abs(avg_win / avg_loss) if avg_loss else 0.0
        daily = closed.groupby("date")["realized_pnl"].sum() if not closed.empty else pd.Series(dtype=float)
        mdd = max_drawdown_abs(cum_curve(daily)) if not daily.empty else 0.0
        kpis = grid([
            kpi("已实现盈亏", f"${total_pnl:+,.2f}", "up" if total_pnl >= 0 else "down"),
            kpi("胜率", f"{wr:.1f}%", "up"),
            kpi("盈亏比 R/R", f"{rr:.2f}"),
            kpi("平仓笔数", f"{wins + losses}"),
            kpi("最大回撤", f"${mdd:,.2f}", "down"),
            kpi("手续费", f"{fee_breakdown(usdm)}"),
        ])
        charts = (f'<div class="two-col">'
                  f'{card("累计已实现盈亏", usdt_equity_section("USD-M", daily, ACCENT))}'
                  f'{card("每日盈亏", daily_bars(daily))}</div>'
                  + card("盈亏分布", winloss_pie(wins, losses)))
        sections.append("<h3>USD-M 合约（USDT）</h3>" + kpis + charts)

    # ── 现货（FIFO 估算 USDT）──
    spot = df[df["market"] == "spot"]
    if not spot.empty:
        est = estimate_spot_pnl(spot)
        if not est.empty:
            total_pnl = est["pnl"].sum()
            wins = int((est["pnl"] > 0).sum())
            losses = int((est["pnl"] <= 0).sum())
            wr = wins / (wins + losses) * 100 if (wins + losses) else 0.0
            daily = est.groupby("date")["pnl"].sum()
            kpis = grid([
                kpi("估算盈亏", f"${total_pnl:+,.2f}", "up" if total_pnl >= 0 else "down"),
                kpi("胜率(估)", f"{wr:.1f}%", "up"),
                kpi("平仓笔数(估)", f"{wins + losses}"),
            ])
            charts = (f'<div class="two-col">'
                      f'{card("累计估算盈亏", usdt_equity_section("现货", daily, GREEN))}'
                      f'{card("每日估算盈亏", daily_bars(daily))}</div>')
            sections.append('<h3>现货（FIFO 估算·USDT）</h3>' + kpis + charts
                            + '<div class="note">现货无交易所 realized_pnl，以上为 FIFO 配对估算，仅供参考。</div>')

    # ── COIN-M（币本位，按 symbol）──
    coinm = df[df["market"] == "coinm"]
    if not coinm.empty:
        rows = ""
        for sym, grp in coinm.groupby("symbol"):
            closed = closing_fills(grp)
            pnl = closed["realized_pnl"].sum() if not closed.empty else 0.0
            wins, losses, wr = winloss(closed)
            unit = grp["margin_asset"].dropna().iloc[0] if grp["margin_asset"].notna().any() else ""
            col = GREEN if pnl >= 0 else RED
            rows += (f'<tr><td style="font-weight:600">{sym}</td>'
                     f'<td style="text-align:center">{len(grp)}</td>'
                     f'<td style="text-align:right;color:{col}">{pnl:+.6f} {unit}</td>'
                     f'<td style="text-align:center">{wins + losses}</td>'
                     f'<td style="text-align:right">{wr:.0f}%</td></tr>')
        table = (f'<table><tr><th>交易对</th><th style="text-align:center">成交</th>'
                 f'<th style="text-align:right">已实现(币本位)</th>'
                 f'<th style="text-align:center">平仓</th><th style="text-align:right">胜率</th></tr>'
                 f'{rows}</table>')
        sections.append('<h3>COIN-M 合约（币本位）</h3>' + card("各交易对盈亏", table)
                        + '<div class="note">COIN-M 盈亏以币计价（BTC/ADA…），不同币不可相加，故按 symbol 分列。</div>')

    return HTMLResponse(page("Performance", "".join(sections) or '<div class="empty">无数据</div>'))


# ─── Page: Analytics ───────────────────────────────────────────────────────────
@app.get("/analytics", dependencies=[Depends(get_user())])
def analytics():
    conn = db()
    df = load_trades(conn)
    conn.close()

    if df.empty:
        return HTMLResponse(page("Analytics", '<div class="empty">trades 表暂无数据</div>'))

    # 跨市场 symbol 明细
    rows = ""
    for (market, sym), grp in df.groupby(["market", "symbol"]):
        n = len(grp)
        vol = grp["quote_qty"].sum()
        vol_txt = f"{vol:,.0f}" if pd.notna(vol) and vol else f"{grp['qty_base'].sum():.4f}"
        if market == "spot":
            est = estimate_spot_pnl(grp)
            pnl = est["pnl"].sum() if not est.empty else 0.0
            wins = int((est["pnl"] > 0).sum()) if not est.empty else 0
            dec = len(est)
            unit = "USDT(估)"
        else:
            closed = closing_fills(grp)
            pnl = closed["realized_pnl"].sum() if not closed.empty else 0.0
            wins, losses, _ = winloss(closed)
            dec = wins + losses
            unit = grp["margin_asset"].dropna().iloc[0] if (market == "coinm" and grp["margin_asset"].notna().any()) else "USDT"
        wr = wins / dec * 100 if dec else 0.0
        col = GREEN if pnl >= 0 else RED
        pnl_fmt = f"{pnl:+,.2f}" if market != "coinm" else f"{pnl:+.6f}"
        rows += (f'<tr><td><span class="badge badge-mkt">{MARKET_LABEL.get(market, market)}</span></td>'
                 f'<td style="font-weight:600">{sym}</td>'
                 f'<td style="text-align:center">{n}</td>'
                 f'<td style="text-align:right">{vol_txt}</td>'
                 f'<td style="text-align:right;color:{col}">{pnl_fmt} {unit}</td>'
                 f'<td style="text-align:right">{wr:.0f}%</td></tr>')
    sym_table = (f'<table><tr><th>市场</th><th>交易对</th><th style="text-align:center">成交</th>'
                 f'<th style="text-align:right">成交额</th><th style="text-align:right">盈亏</th>'
                 f'<th style="text-align:right">胜率</th></tr>{rows}</table>')

    # USD-M 单笔盈亏分布
    usdm_closed = closing_fills(df[df["market"] == "usdm"])
    pnl_hist = '<div class="empty">无 USD-M 平仓成交</div>'
    if not usdm_closed.empty:
        fig = init_plot()
        fig.add_trace(go.Histogram(
            x=usdm_closed["realized_pnl"].values, nbinsx=30, marker_color=ACCENT,
            hovertemplate="盈亏: $%{x:.2f}<br>笔数: %{y}<extra></extra>",
        ))
        fig.update_xaxes(title="单笔已实现盈亏 ($)")
        fig.update_yaxes(title="笔数")
        pnl_hist = plot(theme_fig(fig), height=260)

    # 各市场成交笔数占比
    mkt_counts = df["market"].value_counts()
    fig = init_plot()
    fig.add_trace(go.Pie(
        labels=[MARKET_LABEL.get(m, m) for m in mkt_counts.index],
        values=mkt_counts.values, hole=0.45,
        marker=dict(colors=[ACCENT, GREEN, "#d29922"]),
        textinfo="label+percent",
    ))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-.1))
    mkt_pie = plot(theme_fig(fig), height=260)

    body = (card("交易对明细（跨市场）", sym_table)
            + f'<div class="two-col">{card("USD-M 单笔盈亏分布", pnl_hist)}{card("各市场成交占比", mkt_pie)}</div>')
    return HTMLResponse(page("Analytics", body))


# ─── Page: Calendar ───────────────────────────────────────────────────────────
@app.get("/calendar", dependencies=[Depends(get_user())])
def calendar():
    conn = db()
    df = load_trades(conn)
    conn.close()

    if df.empty:
        return HTMLResponse(page("Calendar", '<div class="empty">trades 表暂无数据</div>'))

    usdm_closed = closing_fills(df[df["market"] == "usdm"])

    # USD-M 每日盈亏热力图
    heatmap_html = '<div class="empty">无 USD-M 平仓成交</div>'
    if not usdm_closed.empty:
        daily = usdm_closed.groupby("date")["realized_pnl"].sum().reset_index()
        daily["date"] = pd.to_datetime(daily["date"])
        daily = daily.sort_values("date")
        if len(daily) >= 2:
            fig = init_plot()
            fig.add_trace(go.Heatmap(
                x=daily["date"].dt.strftime("%Y-%m"),
                y=daily["date"].dt.strftime("%d"),
                z=daily["realized_pnl"].values,
                colorscale=[[0, "rgba(248,81,73,.85)"], [.5, BG], [1, "rgba(63,185,80,.85)"]],
                zmid=0,
                hovertemplate="<b>%{x}-%{y}</b><br>盈亏: $%{z:.2f}<extra></extra>",
                showscale=True, colorbar=dict(title="$"),
            ))
            fig.update_layout(xaxis=dict(type="category"), yaxis=dict(type="category"))
            heatmap_html = plot(theme_fig(fig), height=340)

    # 各市场月度汇总
    monthly_rows = ""
    df2 = df.copy()
    df2["month"] = pd.to_datetime(df2["date"]).dt.to_period("M").astype(str)
    for (month, market), grp in df2.groupby(["month", "market"]):
        if market == "spot":
            est = estimate_spot_pnl(grp)
            pnl = est["pnl"].sum() if not est.empty else 0.0
            pnl_fmt, unit = f"{pnl:+,.2f}", "USDT(估)"
        else:
            closed = closing_fills(grp)
            pnl = closed["realized_pnl"].sum() if not closed.empty else 0.0
            if market == "coinm":
                unit = grp["margin_asset"].dropna().iloc[0] if grp["margin_asset"].notna().any() else ""
                pnl_fmt = f"{pnl:+.6f}"
            else:
                unit, pnl_fmt = "USDT", f"{pnl:+,.2f}"
        col = GREEN if pnl >= 0 else RED
        monthly_rows += (f'<tr><td>{month}</td>'
                         f'<td><span class="badge badge-mkt">{MARKET_LABEL.get(market, market)}</span></td>'
                         f'<td style="text-align:center">{len(grp)}</td>'
                         f'<td style="text-align:right;color:{col}">{pnl_fmt} {unit}</td></tr>')
    monthly_html = (f'<table><tr><th>月份</th><th>市场</th><th style="text-align:center">成交</th>'
                    f'<th style="text-align:right">盈亏</th></tr>{monthly_rows}</table>'
                    if monthly_rows else '<div class="empty">无数据</div>')

    body = (card("USD-M 每日盈亏热力图", heatmap_html)
            + card("月度汇总（按市场）", monthly_html))
    return HTMLResponse(page("Calendar", body))


# ─── Trades API（统一 trades 表）──────────────────────────────────────────────
@app.get("/trades", dependencies=[Depends(get_user())])
def get_trades(
    market: str = None,        # spot | usdm | coinm
    symbol: str = None,
    side: str = None,          # BUY | SELL
    exchange: str = "binance",
    start_time: int = None,    # ms
    end_time: int = None,      # ms
    limit: int = 100,
    offset: int = 0,
    order: str = "desc",       # trade_time 排序：asc | desc
):
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    direction = "ASC" if order.lower() == "asc" else "DESC"

    where = ["exchange = %s"]
    args = [exchange]
    if market:
        where.append("market = %s"); args.append(market)
    if symbol:
        where.append("symbol = %s"); args.append(symbol)
    if side:
        where.append("side = %s"); args.append(side.upper())
    if start_time is not None:
        where.append("trade_time >= %s"); args.append(start_time)
    if end_time is not None:
        where.append("trade_time <= %s"); args.append(end_time)
    where_sql = " AND ".join(where)

    conn = db()
    total = q(conn, f"SELECT count(*) AS n FROM trades WHERE {where_sql}",
              tuple(args), single=True)["n"]
    rows = q(conn, f"""
        SELECT exchange, market, symbol, trade_id, order_id, side,
               price, qty_base, quote_qty, realized_pnl,
               margin_asset, position_side, fee, fee_asset,
               is_maker, trade_time
        FROM trades WHERE {where_sql}
        ORDER BY trade_time {direction}, trade_id {direction}
        LIMIT %s OFFSET %s
    """, tuple(args + [limit, offset]))
    conn.close()
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


@app.get("/trades/stats", dependencies=[Depends(get_user())])
def trades_stats(
    market: str = None,
    symbol: str = None,
    exchange: str = "binance",
    start_time: int = None,
    end_time: int = None,
):
    where = ["exchange = %s"]
    args = [exchange]
    if market:
        where.append("market = %s"); args.append(market)
    if symbol:
        where.append("symbol = %s"); args.append(symbol)
    if start_time is not None:
        where.append("trade_time >= %s"); args.append(start_time)
    if end_time is not None:
        where.append("trade_time <= %s"); args.append(end_time)
    where_sql = " AND ".join(where)

    conn = db()
    by_market = q(conn, f"""
        SELECT market,
               count(*)                                  AS trades,
               count(DISTINCT symbol)                    AS symbols,
               COALESCE(SUM(realized_pnl), 0)            AS realized_pnl,
               COALESCE(SUM(fee), 0)                     AS total_fee,
               COUNT(*) FILTER (WHERE realized_pnl > 0)  AS wins,
               COUNT(*) FILTER (WHERE realized_pnl < 0)  AS losses,
               MIN(trade_time)                           AS first_ms,
               MAX(trade_time)                           AS last_ms
        FROM trades WHERE {where_sql}
        GROUP BY market ORDER BY market
    """, tuple(args))
    for m in by_market:
        decided = (m["wins"] or 0) + (m["losses"] or 0)
        m["win_rate"] = round(m["wins"] / decided * 100, 2) if decided else None

    overall = q(conn, f"""
        SELECT count(*) AS trades,
               COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
               COALESCE(SUM(fee), 0)          AS total_fee,
               MIN(trade_time) AS first_ms, MAX(trade_time) AS last_ms
        FROM trades WHERE {where_sql}
    """, tuple(args), single=True)
    conn.close()
    return {
        "overall": overall,
        "by_market": by_market,
        "note": "realized_pnl 仅合约(usdm/coinm)有值；现货(spot)为 NULL，需 FIFO 配对估算。"
                "COIN-M 的 realized_pnl 为币本位，不同 symbol 不可相加。"
                "win/loss/win_rate 基于 realized_pnl 符号，仅统计平仓成交。",
    }


@app.get("/")
def root():
    return {"message": "Trading Journal Dashboard",
            "pages": ["/summary", "/performance", "/analytics", "/calendar"],
            "api": ["/trades", "/trades/stats"]}
