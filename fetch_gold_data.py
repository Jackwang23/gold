#!/usr/bin/env python3
"""
贪狼黄金终端 · 免费数据源抓取脚本
--------------------------------
从多个数据源拉取数据，写入本地 gold-data.json：
  1. gold-api.com      -> 现货黄金/白银价格 (XAU/USD, XAG/USD)，免费无需Key
  2. Yahoo Finance      -> DXY美元指数, VIX恐慌指数, WTI原油, 比特币, 标普500 (通过 yfinance 库，免费无需Key)
  3. FRED               -> 10年期名义/实际利率 (美联储圣路易斯分行，完全免费无限量)
  4. GoldAPI.io         -> 可选，更丰富的金价数据，需要免费注册的Key(存GitHub Secret)
  5. 华尔街见闻         -> 可选，实时快讯，非官方接口，随时可能失效，失败自动跳过

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


HISTORY_FILE = "gold-history.json"
MIN_POINTS_FOR_CORRELATION = 10   # 至少积累这么多天的数据才计算相关系数，样本太少的相关系数没有意义
MAX_HISTORY_DAYS = 120            # 只保留最近120天，避免文件无限增长


def analyze_price_action(bars, decimals=2):
    """Al Brooks风格价格行为分析：从真实H1 OHLC棒数据计算读盘结论。
    bars需按时间正序排列，每个元素 {open, high, low, close}。
    数据不足20-40根时返回None，不给出不可靠的结论。"""
    if len(bars) < 25:
        return None

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    def bar_type(b):
        body = abs(b["close"] - b["open"])
        rng = b["high"] - b["low"]
        if rng == 0:
            return "doji"
        ratio = body / rng
        direction = "bull" if b["close"] > b["open"] else "bear"
        return f"{direction}_trend" if ratio > 0.65 else "reversal"

    last3 = bars[-3:]
    types3 = [bar_type(b) for b in last3]
    bull_trend_count = sum(1 for t in types3 if t == "bull_trend")
    bear_trend_count = sum(1 for t in types3 if t == "bear_trend")

    sma20 = sum(closes[-20:]) / 20
    always_in = "long" if closes[-1] > sma20 else "short"

    recent_range = max(highs[-20:]) - min(lows[-20:])
    prior_range = max(highs[-40:-20]) - min(lows[-40:-20]) if len(bars) >= 40 else recent_range
    is_trend_structure = recent_range > prior_range * 1.15

    last5 = bars[-5:]
    def signal_quality(b):
        body = abs(b["close"] - b["open"])
        rng = b["high"] - b["low"]
        if rng == 0:
            return 0
        close_pos = (b["close"] - b["low"]) / rng if b["close"] >= b["open"] else (b["high"] - b["close"]) / rng
        return (body / rng) * close_pos
    signal_bar = max(last5, key=signal_quality)
    signal_is_bull = signal_bar["close"] > signal_bar["open"]

    swing_low = min(lows[-20:])
    swing_high = max(highs[-20:])
    swing_range = swing_high - swing_low
    current = closes[-1]
    if always_in == "long":
        target_low = round(current + swing_range * 0.5, decimals)
        target_high = round(current + swing_range * 1.0, decimals)
    else:
        target_low = round(current - swing_range * 1.0, decimals)
        target_high = round(current - swing_range * 0.5, decimals)

    return {
        "alwaysIn": always_in,
        "bullTrendBarsInLast3": bull_trend_count,
        "bearTrendBarsInLast3": bear_trend_count,
        "isTrendStructure": is_trend_structure,
        "signalBarBull": signal_is_bull,
        "signalBarPrice": round(signal_bar["close"], decimals),
        "measuredMoveLow": target_low,
        "measuredMoveHigh": target_high,
        "sma20": round(sma20, decimals),
        "currentPrice": round(current, decimals),
    }


def aggregate_bars(bars, group_size):
    """把细颗粒度的棒线按group_size根一组聚合成粗颗粒度的棒线(比如4根H1聚合成1根H4)"""
    aggregated = []
    for i in range(0, len(bars), group_size):
        chunk = bars[i:i+group_size]
        if not chunk:
            continue
        aggregated.append({
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
        })
    return aggregated


def fetch_hourly_bars_and_analyze():
    """通过yfinance拉取黄金期货最近的H1棒线，用于Al Brooks价格行为分析和蜡烛图展示。
    免费无需Key，但yfinance对小时线历史长度有限制(通常近几十天)，够这个分析用了。
    返回 (价格行为分析结果, 用于蜡烛图的H1棒线, 用于蜡烛图的H4棒线) 三元组，任一项失败时对应位置为None。"""
    try:
        import yfinance as yf
    except ImportError:
        print("[警告] 未安装 yfinance，跳过价格行为分析和K线图")
        return None, None, None
    try:
        t = yf.Ticker("GC=F")  # COMEX黄金期货，比现货XAUUSD=X在yfinance上数据更完整
        hist = t.history(period="7d", interval="1h")
        if len(hist) < 25:
            print(f"[警告] H1棒线数据不足({len(hist)}根)，跳过价格行为分析和K线图")
            return None, None, None
        bars = [
            {"open": float(row.Open), "high": float(row.High), "low": float(row.Low), "close": float(row.Close)}
            for row in hist.itertuples()
        ]
        price_action = analyze_price_action(bars)
        bars1h_display = bars[-40:]   # 蜡烛图只展示最近40根，太多了手机屏幕上挤不下
        bars4h_display = aggregate_bars(bars, 4)[-30:]
        return price_action, bars1h_display, bars4h_display
    except Exception as e:
        print(f"[警告] 拉取H1棒线失败: {e}")
        return None, None, None


def load_history():
    """读取已积累的历史数据文件，不存在则返回空列表"""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[警告] 读取历史数据文件失败，将从头开始积累: {e}")
        return []


def update_history(history, today_str, gold_price, dxy, real_rate, vix, btc, spx):
    """把今天的数据追加进历史序列（每天只记一条，重复运行不会重复添加），并裁剪到最大天数"""
    if history and history[-1].get("date") == today_str:
        # 今天已经记录过了，更新为最新值而不是重复追加
        history[-1] = {
            "date": today_str, "gold": gold_price, "dxy": dxy,
            "realRate": real_rate, "vix": vix, "btc": btc, "spx": spx,
        }
    else:
        history.append({
            "date": today_str, "gold": gold_price, "dxy": dxy,
            "realRate": real_rate, "vix": vix, "btc": btc, "spx": spx,
        })
    return history[-MAX_HISTORY_DAYS:]


def pearson_correlation(xs, ys):
    """手写皮尔逊相关系数，不依赖numpy/scipy，保持脚本零额外依赖"""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < MIN_POINTS_FOR_CORRELATION:
        return None
    xs2 = [p[0] for p in pairs]
    ys2 = [p[1] for p in pairs]
    mean_x = sum(xs2) / n
    mean_y = sum(ys2) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    var_x = sum((x - mean_x) ** 2 for x in xs2)
    var_y = sum((y - mean_y) ** 2 for y in ys2)
    denom = (var_x * var_y) ** 0.5
    if denom == 0:
        return None
    return round(cov / denom, 3)


def compute_correlations(history):
    """基于积累的历史序列，计算金价与各因子的滚动相关系数"""
    n = len(history)
    gold_series = [h.get("gold") for h in history]
    result = {"dataPoints": n, "minRequired": MIN_POINTS_FOR_CORRELATION}
    for factor_key, factor_name in [("dxy", "dxy"), ("realRate", "realRate"), ("vix", "vix"), ("btc", "btc"), ("spx", "spx")]:
        factor_series = [h.get(factor_key) for h in history]
        result[factor_name] = pearson_correlation(gold_series, factor_series)
    return result


def fetch_fed_rss(limit=6):
    """抓取美联储官方RSS（新闻/讲话），这是政府官网直接提供的标准RSS，
    稳定性远高于任何逆向接口，几乎不会失效。缺点：内容是英文（美联储官方语言），
    且更新频率不如综合财经新闻高（一般几天一条），但权威性和稳定性最好。
    返回 (news列表或None, 错误信息或None) 这样的二元组，方便把失败原因写进输出文件供前端直接显示。"""
    import xml.etree.ElementTree as ET

    feeds = [
        "https://www.federalreserve.gov/feeds/press_monetary.xml",   # 货币政策相关新闻稿
        "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml",  # 官员讲话/证词
    ]
    items = []
    errors = []
    for url in feeds:
        try:
            resp = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            })
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:limit]:
                title = (item.findtext("title") or "").strip()
                pub_date = (item.findtext("pubDate") or "").strip()
                if title:
                    items.append({"title": title, "pubDate": pub_date})
        except Exception as e:
            msg = f"{url} -> {type(e).__name__}: {e}"
            print(f"[警告] 拉取美联储RSS失败: {msg}")
            errors.append(msg)
    if not items:
        return None, ("两个Fed RSS源均失败: " + " | ".join(errors)) if errors else "两个Fed RSS源均返回空内容"

    # 按发布时间倒序，取前 limit 条
    def parse_date(d):
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(d)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda x: parse_date(x["pubDate"]), reverse=True)
    news = []
    for it in items[:limit]:
        dt = parse_date(it["pubDate"])
        time_str = dt.strftime("%m-%d %H:%M") if dt != datetime.min.replace(tzinfo=timezone.utc) else ""
        news.append({"time": time_str, "text": "[Fed] " + it["title"]})
    return news, None


def fetch_wallstreetcn_news(limit=6):
    """抓取华尔街见闻实时快讯。
    重要说明：华尔街见闻没有官方公开API，这里用的是业内广泛使用的非官方接口
    （很多开源项目如RSSHub也是通过这个接口实现的），不保证长期稳定，随时可能失效或改版。
    个人自用没问题，但不建议依赖它做成对外产品。
    返回 (news列表或None, 错误信息或None) 这样的二元组。"""
    try:
        resp = requests.get(
            "https://api-prod.wallstreetcn.com/apiv1/content/lives",
            params={"channel": "global-channel", "client": "pc", "cursor": 0, "limit": limit},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://wallstreetcn.com/",
            },
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("data", {}).get("items", [])
        if not items:
            return None, f"接口返回但items为空，原始响应片段: {str(payload)[:200]}"
        news = []
        for item in items[:limit]:
            text = item.get("content_text") or item.get("title") or ""
            text = text.strip()
            if not text:
                continue
            ts = item.get("display_time")
            time_str = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%H:%M") if ts else ""
            news.append({"time": time_str, "text": text[:120]})
        if not news:
            return None, "接口返回了items但没有一条能提取出正文文本"
        return news, None
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[警告] 拉取华尔街见闻快讯失败（非官方接口，可能已变更或限流）: {msg}")
        return None, msg


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
    """通过 yfinance 从 Yahoo Finance 拉取 DXY / VIX / WTI原油 / 比特币 / 标普500，免费无需 Key"""
    try:
        import yfinance as yf
    except ImportError:
        print("[警告] 未安装 yfinance，跳过宏观数据抓取。运行: pip install yfinance --break-system-packages")
        return {"dxy": None, "vix": None, "wti": None, "btc": None, "spx": None}

    tickers = {
        "dxy": "DX-Y.NYB",   # 美元指数
        "vix": "^VIX",       # VIX恐慌指数
        "wti": "CL=F",       # WTI原油期货
        "btc": "BTC-USD",    # 比特币
        "spx": "^GSPC",      # 标普500
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

    # ---- 08关联源：积累历史数据 + 计算真实相关系数 ----
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    gold_price = (gold_silver.get("XAU") or {}).get("price")
    dxy_val = (macro.get("dxy") or {}).get("value")
    real_rate_val = (fred_data.get("10y_real_rate") or {}).get("value")
    vix_val = (macro.get("vix") or {}).get("value")
    btc_val = (macro.get("btc") or {}).get("value")
    spx_val = (macro.get("spx") or {}).get("value")

    history = load_history()
    if gold_price is not None:
        history = update_history(history, today_str, gold_price, dxy_val, real_rate_val, vix_val, btc_val, spx_val)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    correlations = compute_correlations(history)

    # 新闻：美联储官方RSS作为可靠主源，华尔街见闻作为非官方补充（能抓到就加进来，抓不到不影响主源）
    fed_news, fed_error = fetch_fed_rss()
    wscn_news, wscn_error = fetch_wallstreetcn_news()
    fed_news = fed_news or []
    wscn_news = wscn_news or []
    news = (wscn_news + fed_news) or None  # 中文快讯排在前面，最后放不进任何数据时给None
    news_debug = {
        "fedError": fed_error,
        "wscnError": wscn_error,
    } if (fed_error or wscn_error) else None

    price_action, bars1h, bars4h = fetch_hourly_bars_and_analyze()

    output = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "gold": gold_silver.get("XAU"),
        "silver": gold_silver.get("XAG"),
        "dxy": macro.get("dxy"),
        "vix": macro.get("vix"),
        "wti": macro.get("wti"),
        "btc": macro.get("btc"),
        "spx": macro.get("spx"),
        "fred": fred_data,
        "goldapi": goldapi_data,
        "correlations": correlations,
        "news": news,
        "newsDebug": news_debug,
        "priceAction": price_action,
        "bars1h": bars1h,
        "bars4h": bars4h,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"完成，已写入 {OUTPUT_FILE}（历史数据已积累 {len(history)} 天，存于 {HISTORY_FILE}）")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
