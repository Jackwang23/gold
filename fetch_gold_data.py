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
GOLDAPI_KEY = os.environ.get("GOLDAPI_KEY")

# FRED 免费数据不需要 Key 也能拉 CSV，但用官方 API 更规范一些。
# 如果你想用官方 API，需要免费注册一个 FRED API Key（在 fred.stlouisfed.org 免费申请）。
# 这里给一个不需要 Key 的 CSV 备用方案，二选一即可。
FRED_SERIES = {
    "10y_treasury": "DGS10",       # 10年期名义国债收益率
    "10y_real_rate": "DFII10",     # 10年期实际利率(TIPS)
}


def fetch_goldapi():
    """从 GoldAPI.io 拉取更丰富的金价数据（涨跌额/涨跌幅/最高/最低/昨收）。
    需要免费注册的Key，通过环境变量 GOLDAPI_KEY 传入。这是服务器端调用，
    Key不会出现在任何公开文件或网页里。没设置Key时直接跳过，不影响其他数据正常抓取。"""
    if not GOLDAPI_KEY:
        print("[信息] 未设置 GOLDAPI_KEY 环境变量，跳过 GoldAPI.io 抓取（可选数据源，不影响其他部分）")
        return None
    try:
        resp = requests.get(
            "https://www.goldapi.io/api/XAU/USD",
            headers={"x-access-token": GOLDAPI_KEY, "Content-Type": "application/json"},
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
            hist = t.history(period="2d")
            if len(hist) >= 2:
                last = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2]
                change_pct = (last - prev) / prev * 100
                result[key] = {"value": round(float(last), 2), "changePct": round(float(change_pct), 2)}
            else:
                result[key] = None
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


def main():
    print("开始抓取数据...")

    gold_silver = fetch_gold_silver()
    time.sleep(1)  # 简单限速，友好一点
    macro = fetch_dxy_vix_oil()
    fred_data = {name: fetch_fred_series(series_id) for name, series_id in FRED_SERIES.items()}
    goldapi_data = fetch_goldapi()

    output = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "gold": gold_silver.get("XAU"),
        "silver": gold_silver.get("XAG"),
        "dxy": macro.get("dxy"),
        "vix": macro.get("vix"),
        "wti": macro.get("wti"),
        "fred": fred_data,
        "goldapi": goldapi_data,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"完成，已写入 {OUTPUT_FILE}")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
