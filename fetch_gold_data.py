#!/usr/bin/env python3
"""
金鉴 · 免费数据源抓取脚本
--------------------------------
从三个完全免费、无需信用卡的数据源拉取数据，写入本地 gold-data.json：
  1. gold-api.com      -> 现货黄金/白银价格 (XAU/USD, XAG/USD)
  2. Yahoo Finance      -> DXY美元指数, VIX恐慌指数, WTI原油 (通过 yfinance 库，无需Key)
  3. FRED               -> 10年期名义/实际利率 (美联储圣路易斯分行，完全免费无限量)

用法：
  pip install requests yfinance --break-system-packages
  python3 fetch_gold_data.py

建议：写一个 cron/定时任务，每 15-30 分钟跑一次，
     不要在前端页面里直接每次刷新都调用，避免浪费额度、也避免把API Key暴露在浏览器里。
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

OUTPUT_FILE = "gold-data.json"

# GoldAPI.io：更丰富的数据(涨跌额/涨跌幅/最高/最低/昨收)，需要免费注册的Key。
# 安全起见，Key不写在代码里，而是从环境变量读取——本地跑就在命令行里临时设置，
# 部署到GitHub Actions就存成仓库的加密Secret，永远不会出现在公开代码或网页里。
GOLDAPI_IO_TOKEN = os.environ.get("GOLDAPI_IO_TOKEN")

# FRED 免费数据不需要 Key 也能拉 CSV，但用官方 API 更规范一些。
# 如果你想用官方 API，需要免费注册一个 FRED API Key（在 fred.stlouisfed.org 免费申请）。
# 这里给一个不需要 Key 的 CSV 备用方案，二选一即可。
FRED_SERIES = {
    "10y_treasury": "DGS10",       # 10年期名义国债收益率
    "10y_real_rate": "DFII10",     # 10年期实际利率(TIPS)
}


def fetch_goldapi():
    """从 GoldAPI.io 拉取更丰富的金价数据（涨跌额/涨跌幅/最高/最低/昨收）。
    需要免费注册的Key，通过环境变量 GOLDAPI_IO_TOKEN 传入。这是服务器端调用，
    Key不会出现在任何公开文件或网页里。没设置Key时直接跳过，不影响其他数据正常抓取。"""
    if not GOLDAPI_IO_TOKEN:
        print("[信息] 未设置 GOLDAPI_IO_TOKEN 环境变量，跳过 GoldAPI.io 抓取（可选数据源，不影响其他部分）")
        return None
    try:
        resp = requests.get(
            "https://www.goldapi.io/api/XAU/USD",
            headers={"x-access-token": GOLDAPI_IO_TOKEN, "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        d = resp.json()
        return {
            "price": d.get("price"),
            "change": d.get("ch"),
            "changePercent": d.get("chp"),
            "open": d.get("open_price"),
            "high": d.get("high_price"),
            "low": d.get("low_price"),
            "prevClose": d.get("prev_close_price"),
            "timestamp": d.get("timestamp"),
        }
    except Exception as e:
        print(f"[警告] 拉取 GoldAPI.io 失败: {e}")
        return None


def fetch_gold_silver():
    """从 gold-api.com 拉取现货金价、银价，完全免费不需要 Key"""
    result = {}
    for symbol in ["XAU", "XAG"]:
        try:
            resp = requests.get(f"https://api.gold-api.com/price/{symbol}", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            result[symbol] = {
                "price": data.get("price"),
                "updatedAt": data.get("updatedAt"),
            }
        except Exception as e:
            print(f"[警告] 拉取 {symbol} 失败: {e}")
            result[symbol] = None
    return result


def fetch_dxy_vix_oil():
    """通过 yfinance 从 Yahoo Finance 拉取 DXY / VIX / WTI原油，免费无需 Key"""
    try:
        import yfinance as yf
    except ImportError:
        print("[警告] 未安装 yfinance，跳过 DXY/VIX/原油 抓取。运行: pip install yfinance --break-system-packages")
        return {"dxy": None, "vix": None, "wti": None}

    tickers = {
        "dxy": "DX-Y.NYB",   # 美元指数
        "vix": "^VIX",       # VIX恐慌指数
        "wti": "CL=F",       # WTI原油期货
    }
    result = {}
    for key, symbol in tickers.items():
        try:
            t = yf.Ticker(symbol)
            # 用7天窗口而不是2天：DXY/VIX是指数，遇到周末/假期/当天未收盘时，
            # 2天窗口经常凑不够2条数据导致误判失败。7天足以覆盖任何长周末。
            hist = t.history(period="7d")
            if len(hist) >= 2:
                last = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                change_pct = (last - prev) / prev * 100
                result[key] = {"value": round(float(last), 2), "changePct": round(float(change_pct), 2)}
            elif len(hist) == 1:
                # 只抓到1条也比null强：至少把最新价显示出来，涨跌幅留空
                last = hist["Close"].iloc[-1]
                result[key] = {"value": round(float(last), 2), "changePct": None}
                print(f"[提示] {symbol} 只抓到1条历史数据，已显示最新价，涨跌幅暂缺")
            else:
                result[key] = None
                print(f"[警告] {symbol} 未返回任何数据（7天窗口内），标记为null")
        except Exception as e:
            print(f"[警告] 拉取 {symbol} 失败: {e}")
            result[key] = None
    return result


def fetch_fred_series(series_id):
    """从 FRED 免费 CSV 接口拉取最新值，不需要注册 Key"""
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        # 最后一行是最新数据，格式: DATE,VALUE
        last_line = lines[-1]
        date_str, value_str = last_line.split(",")
        if value_str == ".":
            # 最新一天可能还没更新，往前找一行有效数据
            for line in reversed(lines[:-1]):
                d, v = line.split(",")
                if v != ".":
                    return {"date": d, "value": float(v)}
            return None
        return {"date": date_str, "value": float(value_str)}
    except Exception as e:
        print(f"[警告] 拉取 FRED 系列 {series_id} 失败: {e}")
        return None


def fetch_news():
    """从 Investing.com 免费公开RSS抓取黄金相关新闻标题，完全免费、无需注册Key。
    只取标题/来源/时间/链接，不抓取正文，避免版权问题，也符合"资讯速览"这种
    标题式展示的定位。"""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    feeds = {
        "commodities": ("https://www.investing.com/rss/news_11.rss", "大宗商品"),
        "economy": ("https://www.investing.com/rss/news_14.rss", "经济数据"),
        "central_banks": ("https://www.investing.com/rss/central_banks.rss", "央行动向"),
    }

    headers = {"User-Agent": "Mozilla/5.0 (compatible; GoldDashboardBot/1.0)"}
    items = []
    for category, (url, label) in feeds.items():
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:4]:  # 每个分类最多取4条
                title = item.findtext("title", default="").strip()
                link = item.findtext("link", default="").strip()
                pub_date_raw = item.findtext("pubDate", default="")
                try:
                    pub_dt = parsedate_to_datetime(pub_date_raw)
                    pub_iso = pub_dt.isoformat()
                except Exception:
                    pub_iso = None
                if title:
                    items.append({
                        "category": category,
                        "categoryLabel": label,
                        "title": title,
                        "link": link,
                        "publishedAt": pub_iso,
                    })
        except Exception as e:
            print(f"[警告] 拉取新闻源 {label}({url}) 失败: {e}")

    # 按发布时间倒序排列，最新的在前面
    items.sort(key=lambda x: x["publishedAt"] or "", reverse=True)
    return items[:12]  # 总共最多保留12条，避免文件过大


def main():
    print("开始抓取数据...")

    gold_silver = fetch_gold_silver()
    time.sleep(1)  # 简单限速，友好一点
    macro = fetch_dxy_vix_oil()
    fred_data = {name: fetch_fred_series(series_id) for name, series_id in FRED_SERIES.items()}
    goldapi_data = fetch_goldapi()
    news_data = fetch_news()

    output = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "gold": gold_silver.get("XAU"),
        "silver": gold_silver.get("XAG"),
        "dxy": macro.get("dxy"),
        "vix": macro.get("vix"),
        "wti": macro.get("wti"),
        "fred": fred_data,
        "goldapi": goldapi_data,
        "news": news_data,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"完成，已写入 {OUTPUT_FILE}")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
