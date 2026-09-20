"""
市场简报自动生成脚本
——————————————————————
每次运行会：
1. 从 Finnhub 抓取最新报价（SPY/QQQ/DIA 代表三大指数，GLD 代表黄金，
   USO 代表原油，VIXY 代表 VIX 波动率）
2. 把数值写入 docs/index.html（供 GitHub Pages 发布为公开网页）
3. 把简报正文作为邮件发送

免费版 Finnhub 的美股报价接口延迟通常在几分钟以内，不是逐笔 Level-1 实时数据，
但对于"每天盯盘几次"的场景已经足够。
"""

import os
import json
import datetime
import requests

FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]
TICKERS = {
    "SPY": "标普500 ETF",
    "QQQ": "纳斯达克100 ETF",
    "DIA": "道琼斯 ETF",
    "GLD": "黄金 ETF",
    "USO": "原油 ETF",
    "VIXY": "VIX 波动率 ETF",
    "TLT": "20年期以上国债 ETF",
}


def fetch_quote(symbol: str) -> dict:
    url = "https://finnhub.io/api/v1/quote"
    resp = requests.get(url, params={"symbol": symbol, "token": FINNHUB_KEY}, timeout=10)
    resp.raise_for_status()
    return resp.json()  # {c: current, d: change, dp: %change, h, l, o, pc}


def build_rows() -> list[str]:
    rows = []
    for symbol, label in TICKERS.items():
        q = fetch_quote(symbol)
        price = q.get("c", 0)
        change = q.get("d", 0)
        pct = q.get("dp", 0)
        arrow = "▲" if change >= 0 else "▼"
        cls = "up" if change >= 0 else "down"
        rows.append(
            f'<div class="ticker"><div class="label">{label} ({symbol})</div>'
            f'<div class="val">{price:,.2f}</div>'
            f'<div class="chg {cls}"><span class="arrow">{arrow}</span>{pct:+.2f}%</div></div>'
        )
    return rows


def build_email_summary() -> str:
    lines = []
    for symbol, label in TICKERS.items():
        q = fetch_quote(symbol)
        lines.append(f"{label} ({symbol}): {q.get('c', 0):,.2f}  ({q.get('dp', 0):+.2f}%)")
    return "\n".join(lines)


def render_html(rows: list[str]) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    # Convert to US Eastern for a familiar "market time" label (approx, no DST handling)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")

    with open("template.html", "r", encoding="utf-8") as f:
        template = f.read()

    ticker_html = "\n".join(rows)
    html = template.replace("{{TIMESTAMP}}", ts).replace("{{TICKER_ROWS}}", ticker_html)
    return html


def main():
    rows = build_rows()
    html = render_html(rows)

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    # Write email body summary to a file the workflow step will read
    with open("email_body.txt", "w", encoding="utf-8") as f:
        f.write(build_email_summary())

    print("Build complete.")


if __name__ == "__main__":
    main()
