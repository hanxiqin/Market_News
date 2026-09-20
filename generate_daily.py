"""
每日数据生成脚本（每天运行1次）
——————————————————————
生成 data/daily.json，包含：
1. 历史价格走势（1M / 1Y / 5Y）— Twelve Data 免费接口
2. 固定收益数据（国债收益率、通胀）— FRED（美联储）免费接口
3. 热门话题 — 从免费 RSS 新闻源抓取真实标题+链接（不做AI总结，原文标题）
4. 近期 IPO — Financial Modeling Prep 免费接口

这个脚本只需要每天跑1次，因为历史图表/新闻/IPO这些数据变化不快，
不需要像实时报价那样频繁刷新。
"""

import os
import json
import datetime
import requests
import feedparser

FRED_KEY = os.environ["FRED_API_KEY"]
FMP_KEY = os.environ["FMP_API_KEY"]

YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ---------- 1. 历史价格（Yahoo Finance，不需要 API Key） ----------

HISTORY_SYMBOLS = {"spx": "^GSPC", "gold": "GC=F", "oil": "CL=F"}
YF_RANGES = {
    "1M": {"range": "1mo", "interval": "1d"},
    "1Y": {"range": "1y", "interval": "1wk"},
    "5Y": {"range": "5y", "interval": "1mo"},
}


def fetch_history(symbol: str, range_: str, interval: str) -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": range_, "interval": interval}
    resp = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=15)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    closes = result["indicators"]["quote"][0].get("close", [])
    pairs = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
    labels = [datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d") for t, _ in pairs]
    data = [round(c, 2) for _, c in pairs]
    return {"labels": labels, "data": data}


def build_history() -> dict:
    history = {}
    for key, symbol in HISTORY_SYMBOLS.items():
        history[key] = {}
        for range_label, cfg in YF_RANGES.items():
            try:
                history[key][range_label] = fetch_history(symbol, cfg["range"], cfg["interval"])
            except Exception as e:
                print(f"History fetch failed for {symbol} {range_label}: {e}")
                history[key][range_label] = {"labels": [], "data": []}
    return history


# ---------- 2. 固定收益（FRED） ----------

FRED_SERIES = {
    "y2": "DGS2",       # 2年期国债收益率
    "y10": "DGS10",     # 10年期国债收益率
    "y30": "DGS30",     # 30年期国债收益率
    "fedfunds": "FEDFUNDS",  # 联邦基金利率
    "cpi": "CPIAUCSL",  # CPI（用于计算同比通胀）
}


def fetch_fred_latest(series_id: str) -> float | None:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 13,  # enough to compute YoY for CPI
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    obs = [o for o in obs if o["value"] != "."]
    return obs


def build_fixed_income() -> dict:
    result = {}
    for key, series_id in FRED_SERIES.items():
        try:
            obs = fetch_fred_latest(series_id)
            if key == "cpi" and len(obs) >= 13:
                latest = float(obs[0]["value"])
                year_ago = float(obs[12]["value"])
                yoy = (latest / year_ago - 1) * 100
                result["cpi_yoy"] = round(yoy, 2)
            elif obs:
                result[key] = float(obs[0]["value"])
        except Exception as e:
            print(f"FRED fetch failed for {series_id}: {e}")
    return result


# ---------- 3. 热门话题（免费 RSS，原文标题+链接） ----------

RSS_FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Investing.com", "https://www.investing.com/rss/news_301.rss"),
    ("Investing.com Commodities", "https://www.investing.com/rss/news_11.rss"),
]


# 宏观市场关键词——用来从新闻源里筛选出真正的市场热点
# （加息/通胀/债券/黄金/IPO等），过滤掉不相关的内容（比如个股八卦、企业公告等）
MACRO_KEYWORDS = [
    # 央行/利率
    "fed", "federal reserve", "rate hike", "rate cut", "interest rate",
    "fomc", "powell", "central bank", "monetary policy",
    # 通胀/经济数据
    "inflation", "cpi", "pce", "gdp", "jobs report", "unemployment",
    "recession", "economic growth",
    # 债券/固定收益
    "bond", "treasury", "yield", "fixed income", "credit market",
    # 商品
    "gold", "oil", "crude", "opec", "commodity", "silver",
    # IPO/资本市场
    "ipo", "public offering", "listing", "debut", "underwriter",
    # 大盘/指数
    "s&p", "nasdaq", "dow jones", "stock market", "wall street",
    "market rally", "market selloff", "bull market", "bear market",
    # 货币/贸易
    "dollar", "currency", "tariff", "trade war", "forex",
]


def is_macro_relevant(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in MACRO_KEYWORDS)


def build_heat_topics(limit_per_feed: int = 40, max_total: int = 10) -> list[dict]:
    """
    只保留真正跟宏观市场相关的新闻（加息/通胀/债券/黄金/IPO等）。
    如果当天筛出来的数量不够，不会用不相关的新闻硬填——宁可少显示，
    也不显示跟"热门话题"名不副实的内容。
    """
    topics = []
    seen_titles = set()
    for source_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:limit_per_feed]:
                title = entry.get("title", "")
                if not title or title in seen_titles:
                    continue
                if not is_macro_relevant(title):
                    continue
                seen_titles.add(title)
                topics.append({
                    "title": title,
                    "link": entry.get("link", ""),
                    "source": source_name,
                })
        except Exception as e:
            print(f"RSS fetch failed for {source_name}: {e}")

    return topics[:max_total]


# ---------- 4. 近期 IPO（Financial Modeling Prep） ----------

def build_ipos() -> list[dict]:
    today = datetime.date.today()
    start = today - datetime.timedelta(days=60)
    url = "https://financialmodelingprep.com/api/v3/ipo_calendar"
    params = {
        "from": start.isoformat(),
        "to": today.isoformat(),
        "apikey": FMP_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        ipos = []
        for item in data[:10]:
            ipos.append({
                "company": item.get("company", ""),
                "symbol": item.get("symbol", ""),
                "date": item.get("date", ""),
                "price": item.get("priceRange", "—"),
                "exchange": item.get("exchange", ""),
            })
        return ipos
    except Exception as e:
        print(f"IPO fetch failed: {e}")
        return []


def main():
    daily = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "history": build_history(),
        "fixed_income": build_fixed_income(),
        "heat_topics": build_heat_topics(),
        "ipos": build_ipos(),
    }

    os.makedirs("data", exist_ok=True)
    with open("data/daily.json", "w", encoding="utf-8") as f:
        json.dump(daily, f, ensure_ascii=False, indent=2)

    print("Daily data build complete.")


if __name__ == "__main__":
    main()
