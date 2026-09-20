"""
实时价格刷新脚本（每天盘中运行4-6次）— Yahoo Finance 版
——————————————————————
用 Yahoo Finance 免费接口（不需要 API Key）抓取：
- 真实指数：标普500、纳斯达克综合、道琼斯、VIX
- 真实期货价格：黄金、WTI原油
- ETF：20年期以上国债（没有对应的简单"指数"代码，用ETF）

然后合并 data/daily.json 里的缓存数据（历史图表/固定收益/新闻/IPO），
渲染成完整的 docs/index.html。

注意：Yahoo 这个接口是非官方的，没有公开文档，理论上随时可能变化或
被限速。如果某天突然抓取失败，是这个接口的已知风险，不是你的配置问题。
"""

import os
import json
import datetime
import requests

YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

TICKERS = {
    "^GSPC": "标普500指数",
    "^IXIC": "纳斯达克综合指数",
    "^DJI": "道琼斯工业指数",
    "^VIX": "VIX 波动率指数",
    "GC=F": "黄金期货",
    "CL=F": "WTI原油期货",
    "TLT": "20年期以上国债 ETF",
}


def fetch_yahoo_quote(symbol: str) -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "5d", "interval": "1d"}
    resp = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=10)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    meta = result["meta"]
    price = meta.get("regularMarketPrice", 0) or 0
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose") or price
    pct = (price / prev_close - 1) * 100 if prev_close else 0
    return {"c": price, "dp": pct}


def build_quotes() -> dict:
    quotes = {}
    for symbol in TICKERS:
        try:
            quotes[symbol] = fetch_yahoo_quote(symbol)
        except Exception as e:
            print(f"Quote fetch failed for {symbol}: {e}")
            quotes[symbol] = {"c": 0, "dp": 0}
    return quotes


def load_daily() -> dict:
    try:
        with open("data/daily.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"history": {}, "fixed_income": {}, "heat_topics": [], "ipos": []}


def render_ticker_rows(quotes: dict) -> str:
    rows = []
    for symbol, label in TICKERS.items():
        q = quotes.get(symbol, {})
        price = q.get("c", 0) or 0
        pct = q.get("dp", 0) or 0
        arrow = "▲" if pct >= 0 else "▼"
        cls = "up" if pct >= 0 else "down"
        rows.append(
            f'<div class="ticker"><div class="label">{label} ({symbol})</div>'
            f'<div class="val">{price:,.2f}</div>'
            f'<div class="chg {cls}"><span class="arrow">{arrow}</span>{pct:+.2f}%</div></div>'
        )
    return "\n".join(rows)


def render_heat_topics(topics: list[dict]) -> str:
    if not topics:
        return '<div class="empty-note">暂无数据，等待下次每日刷新。</div>'
    items = []
    for t in topics:
        items.append(
            f'<div class="heat-item">'
            f'<a class="name" href="{t.get("link", "#")}" target="_blank" rel="noopener">'
            f'{t.get("title", "")} <span class="go">→</span></a>'
            f'<div class="src">{t.get("source", "")}</div>'
            f'</div>'
        )
    return "\n".join(items)


def render_ipo_table(ipos: list[dict]) -> str:
    if not ipos:
        return '<tr><td colspan="4" class="empty-note">近期没有查到新的IPO数据</td></tr>'
    rows = ["<tr><th>公司</th><th>代码</th><th>日期</th><th>发行价区间</th></tr>"]
    for ipo in ipos:
        rows.append(
            f'<tr><td class="name">{ipo.get("company","")}</td>'
            f'<td class="mono">{ipo.get("symbol","")}</td>'
            f'<td class="mono">{ipo.get("date","")}</td>'
            f'<td class="mono">{ipo.get("price","—")}</td></tr>'
        )
    return "\n".join(rows)


def render_fixed_income(fi: dict) -> str:
    def fmt(key, suffix="%"):
        val = fi.get(key)
        return f"{val:.2f}{suffix}" if val is not None else "—"

    return f"""
    <div class="ticker"><div class="label">2年期收益率</div><div class="val">{fmt('y2')}</div></div>
    <div class="ticker"><div class="label">10年期收益率</div><div class="val">{fmt('y10')}</div></div>
    <div class="ticker"><div class="label">30年期收益率</div><div class="val">{fmt('y30')}</div></div>
    <div class="ticker"><div class="label">联邦基金利率</div><div class="val">{fmt('fedfunds')}</div></div>
    <div class="ticker"><div class="label">CPI同比通胀</div><div class="val">{fmt('cpi_yoy')}</div></div>
    """


def render_html(quotes: dict, daily: dict) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")

    with open("template.html", "r", encoding="utf-8") as f:
        template = f.read()

    html = template
    html = html.replace("{{TIMESTAMP}}", ts)
    html = html.replace("{{TICKER_ROWS}}", render_ticker_rows(quotes))
    html = html.replace("{{HEAT_TOPICS}}", render_heat_topics(daily.get("heat_topics", [])))
    html = html.replace("{{IPO_ROWS}}", render_ipo_table(daily.get("ipos", [])))
    html = html.replace("{{FIXED_INCOME}}", render_fixed_income(daily.get("fixed_income", {})))
    html = html.replace("{{HISTORY_JSON}}", json.dumps(daily.get("history", {})))
    return html


def build_email_summary(quotes: dict, daily: dict) -> str:
    lines = ["【实时报价】"]
    for symbol, label in TICKERS.items():
        q = quotes.get(symbol, {})
        lines.append(f"{label} ({symbol}): {q.get('c', 0):,.2f}  ({q.get('dp', 0):+.2f}%)")

    fi = daily.get("fixed_income", {})
    if fi:
        lines.append("\n【固定收益】")
        lines.append(f"10年期收益率: {fi.get('y10', '—')}%")
        lines.append(f"CPI同比通胀: {fi.get('cpi_yoy', '—')}%")

    topics = daily.get("heat_topics", [])
    if topics:
        lines.append("\n【热门话题】")
        for t in topics[:5]:
            lines.append(f"- {t.get('title','')} ({t.get('source','')})")

    return "\n".join(lines)


def main():
    quotes = build_quotes()
    daily = load_daily()
    html = render_html(quotes, daily)

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    with open("email_body.txt", "w", encoding="utf-8") as f:
        f.write(build_email_summary(quotes, daily))

    print("Build complete.")


if __name__ == "__main__":
    main()
