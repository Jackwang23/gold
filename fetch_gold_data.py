#!/usr/bin/env python3
"""
金鉴 · 免费数据源抓取脚本
--------------------------------
从四个数据源拉取数据，写入本地 gold-data.json：
  1. gold-api.com      -> 现货黄金/白银价格 (XAU/USD, XAG/USD)，完全免费无需Key
  2. GoldAPI.io         -> 当日开盘/最高/最低/昨收，需要免费Key（见下方说明）
  3. Yahoo Finance      -> DXY美元指数, VIX恐慌指数, WTI原油 (通过 yfinance 库，无需Key)
  4. FRED               -> 10年期名义/实际利率 (美联储圣路易斯分行，完全免费无限量)

用法：
  pip install requests yfinance --break-system-packages
  export GOLDAPI_IO_TOKEN="你的goldapi.io token"   # 可选，不设置则跳过这一项
  python3 fetch_gold_data.py

⚠️ 安全提醒（GoldAPI.io官方要求）：
  - 绝不要把 GoldAPI.io 的 token 写死在这个文件里、提交到 Git 仓库，
    或粘贴到网页前端的任何输入框里 —— 那是公开可见的。
  - 正确做法：只通过环境变量 GOLDAPI_IO_TOKEN 传入。本地跑就用 export，
    GitHub Actions 里就用 repository secret（见 fetch_gold_data.yml 里的用法）。
  - gold-api.com 和 GoldAPI.io 是两个不同的服务，认证方式不同，不要混用彼此的Key。

建议：写一个 cron/定时任务，每 15-30 分钟跑一次，
     不要在前端页面里直接每次刷新都调用，避免浪费额度、也避免把API Key暴露在浏览器里。
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

OUTPUT_FILE = "gold-data.json"

# FRED 免费数据不需要 Key 也能拉 CSV，但用官方 API 更规范一些。
# 如果你想用官方 API，需要免费注册一个 FRED API Key（在 fred.stlouisfed.org 免费申请）。
# 这里给一个不需要 Key 的 CSV 备用方案，二选一即可。
FRED_SERIES = {
    "10y_treasury": "DGS10",       # 10年期名义国债收益率
    "10y_real_rate": "DFII10",     # 10年期实际利率(TIPS)
}


def fetch_goldapi_io():
    """从 GoldAPI.io 拉取当日开盘/最高/最低/昨收等OHLC数据（gold-api.com免费接口不提供这些字段）。
    Token 只从环境变量 GOLDAPI_IO_TOKEN 读取，绝不硬编码在代码里。
    没有设置该环境变量时，直接跳过，不影响其他数据源。
    """
    token = os.environ.get("GOLDAPI_IO_TOKEN")
    if not token:
        print("[提示] 未设置 GOLDAPI_IO_TOKEN 环境变量，跳过 GoldAPI.io（不影响其他数据）。")
        return None
    try:
        resp = requests.get(
            "https://www.goldapi.io/api/XAU/USD",
            headers={"x-access-token": token, "Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "price": data.get("price"),
            "open": data.get("open_price"),
            "high": data.get("high_price"),
            "low": data.get("low_price"),
            "prevClose": data.get("prev_close_price"),
            "change": data.get("ch"),
            "changePct": data.get("chp"),
            "timestamp": data.get("timestamp"),
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
    goldapi_io = fetch_goldapi_io()
    macro = fetch_dxy_vix_oil()
    fred_data = {name: fetch_fred_series(series_id) for name, series_id in FRED_SERIES.items()}

    output = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "gold": gold_silver.get("XAU"),
        "silver": gold_silver.get("XAG"),
        "goldapi_io": goldapi_io,
        "dxy": macro.get("dxy"),
        "vix": macro.get("vix"),
        "wti": macro.get("wti"),
        "fred": fred_data,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"完成，已写入 {OUTPUT_FILE}")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
