#!/usr/bin/env python3
"""Shared market-data infrastructure for the portfolio optimizer.

从 calibrate_params.py 原样抽出的取数/统计/数据表, 供校准脚本与「新标的测试」
共用, 保证两条链路的参数口径永远单一来源。

- yahoo_history / eastmoney_nav: Yahoo chart API 与天天基金(东财)累计净值抓取
- to_series: prices -> DataFrame(date, close, ret)
- annualized_cagr / annualized_vol: 全历史年化统计 (样本 < 30 视为无效)
- daily_return_map / pairwise_corr: 日收益两两相关 (重叠不足回退默认值)
- SOURCES / PROXY_MAP: 校准数据表, 原样迁自 calibrate_params.py
- series_for_broad_key: 大类取数 (代理历史更长则优先代理, 与校准同逻辑)
- cached_series: work_dir 内按 symbol 缓存, 优化器关闭时随 work_dir 一起清理
"""

import datetime
import json
import math
import os
import re
import ssl
import time
import urllib.request

import pandas as pd

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

_MIN_SERIES = 30


def yahoo_history(ticker, years=None):
    end = int(time.time())
    if years is None:
        # Use an early start so Yahoo returns the longest daily history it can.
        start = 0
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start}&period2={end}&interval=1d"
    else:
        start = end - int(years * 365.25 * 24 * 3600)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start}&period2={end}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        data = json.loads(r.read().decode())
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    out = []
    for t, c in zip(ts, closes):
        if c is not None:
            out.append((datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date(), float(c)))
    out.sort()
    return out


def eastmoney_nav(code, years=None):
    """Fetch full China fund NAV history from pingzhongdata JS (one request)."""
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html",
        },
    )
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        text = r.read().decode("utf-8", "replace")
    # Prefer cumulative NAV (includes dividends); fall back to unit NAV.
    m = re.search(r"var Data_ACWorthTrend = (\[.*?\]);", text)
    if not m:
        m = re.search(r"var Data_netWorthTrend = (\[.*?\]);", text)
    if not m:
        return []
    arr = json.loads(m.group(1))
    rows = []
    for item in arr:
        if isinstance(item, list) and len(item) >= 2:
            ts, val = item[0], item[1]
        elif isinstance(item, dict):
            ts, val = item.get("x"), item.get("y")
        else:
            continue
        if ts is None or val is None:
            continue
        d = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).date()
        rows.append((d, float(val)))
    rows.sort()
    seen = set()
    uniq = []
    for d, v in rows:
        if d not in seen:
            seen.add(d)
            uniq.append((d, v))
    return uniq


def to_series(prices):
    if len(prices) < 30:
        return None
    df = pd.DataFrame(prices, columns=["date", "close"]).drop_duplicates("date").sort_values("date")
    df["ret"] = df["close"].pct_change()
    return df


def annualized_cagr(prices):
    """100% 历史年化收益 (几何, 252 交易日/年). 样本不足或数据无效返回 None."""
    if not prices or len(prices) < _MIN_SERIES:
        return None
    first, last = float(prices[0][1]), float(prices[-1][1])
    n = len(prices)
    if first <= 0 or last <= 0:
        return None
    return (last / first) ** (252.0 / n) - 1.0


def annualized_vol(prices):
    """历史年化波动率 (日收益标准差 * sqrt(252)). 样本不足返回 None."""
    df = to_series(prices)
    if df is None:
        return None
    return float(df["ret"].std(ddof=1) * math.sqrt(252.0))


def daily_return_map(prices):
    """{date: daily_return} 字典."""
    df = to_series(prices)
    if df is None:
        return None
    return dict(zip(df["date"], df["ret"]))


def pairwise_corr(rets_a, rets_b, min_periods=180, default=0.30):
    """日收益皮尔逊相关; 公共交易日不足 min_periods 回退 default (与校准口径一致)."""
    if not rets_a or not rets_b:
        return default
    common = sorted(set(rets_a) & set(rets_b))
    pairs = []
    for d in common:
        x, y = rets_a[d], rets_b[d]
        if x == x and y == y:  # 过滤 NaN
            pairs.append((x, y))
    if len(pairs) < min_periods:
        return default
    c = pd.Series([p[0] for p in pairs]).corr(pd.Series([p[1] for p in pairs]))
    if c is None or pd.isna(c):
        return default
    return round(float(c), 4)


def cached_series(work_dir, source, symbol, log=None):
    """按 (source, symbol) 缓存全历史收盘序列 [(date, close)], 命中直接返回.

    缓存文件位于 work_dir/prices_cache/, 生命周期与优化器联网管线共享目录一致
    (App 关闭优化器时由 Swift 侧 cleanupWorkDir 整体删除)。
    """
    cache_dir = os.path.join(work_dir, "prices_cache")
    os.makedirs(cache_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.=\-]", "_", symbol)
    path = os.path.join(cache_dir, f"{source}_{safe}.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            return [(datetime.date.fromisoformat(d), float(v)) for d, v in raw]
        except Exception:
            pass
    try:
        prices = yahoo_history(symbol) if source == "yahoo" else eastmoney_nav(symbol)
    except Exception as exc:
        if log:
            log(f"{symbol} fetch failed: {type(exc).__name__}: {exc}", level="warning")
        return []
    if prices:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump([[d.isoformat(), v] for d, v in prices], f)
        except Exception:
            pass
    return prices


# ---------------------------------------------------------------------------
# 校准数据表 (原样迁自 calibrate_params.py, 保持单一来源)
# ---------------------------------------------------------------------------

SOURCES = {
    "D_HSBC_SP500": ("fund", "050025"),
    "D_HSBC_CSI300": ("fund", "000051"),
    "D_HSBC_DIVLOWVOL": ("fund", "007605"),
    "D_HSBC_CDB": ("fund", "007485"),
    "D_HSBC_GOLD": ("fund", "000307"),
    "D_HSBC_HSTECH": ("fund", "013402"),
    "D_HSBC_MMF": ("fund", "000891"),
    "D_CN_CREDIT_BOND": ("fund", "110035"),
    "D_CN_DIVLOWVOL100": ("fund", "021550"),
    "D_CN_STOCK": ("fund", "000251"),
    "D_CN_MIXED": ("fund", "000849"),
    "D_CN_INDEX": ("fund", "001237"),
    "D_CN_HK": ("fund", "005698"),
    "D_CN_QDII_STOCK": ("fund", "000043"),
    "D_CN_QDII_STABLE": ("fund", "017970"),
    "D_CN_QDII_COMMODITY": ("yahoo", "CL=F"),
    "O_US_CORE": ("yahoo", "SPY"),
    "O_BTC": ("yahoo", "BTC-USD"),
    "O_HYLB": ("yahoo", "HYLB"),
    "O_HK_HSTECH": ("yahoo", "3067.HK"),
    "O_HK_HIGHDIV": ("yahoo", "3031.HK"),
    "O_JP_EQ": ("yahoo", "1698.T"),
    "O_SG_EQ": ("yahoo", "G3B.SI"),
    "O_US_TLT": ("yahoo", "TLH"),
    "O_US_REIT": ("yahoo", "REZ"),
    "O_US_ENERGY": ("yahoo", "XOM"),
    "O_HK_GOLD": ("yahoo", "3170.HK"),
    "O_GOLD": ("yahoo", "GC=F"),
    "SPLG": ("yahoo", "SPLG"),
    "VTV": ("yahoo", "VTV"),
    "SPMO": ("yahoo", "SPMO"),
    "UNH": ("yahoo", "UNH"),
    "GOOG": ("yahoo", "GOOG"),
    "AAPL": ("yahoo", "AAPL"),
    "MSFT": ("yahoo", "MSFT"),
    "NVDA": ("yahoo", "NVDA"),
    "AMZN": ("yahoo", "AMZN"),
    "META": ("yahoo", "META"),
    "TSLA": ("yahoo", "TSLA"),
    "BUFFER": ("yahoo", "MAXJ"),
    "1364": ("yahoo", "1364.T"),
    "1698": ("yahoo", "1698.T"),
    "2516": ("yahoo", "2516.T"),
    "1477": ("yahoo", "1477.T"),
    "1478": ("yahoo", "1478.T"),
    "1490": ("yahoo", "1490.T"),
    "2529": ("yahoo", "2529.T"),
    "2800": ("yahoo", "2800.HK"),
    "2828": ("yahoo", "2828.HK"),
    "3031": ("yahoo", "3031.HK"),
    "3067": ("yahoo", "3067.HK"),
    "3110": ("yahoo", "3110.HK"),
    "3070": ("yahoo", "3070.HK"),
    "CSI300": ("fund", "110020"),
    "DIVLOWVOL": ("fund", "008163"),
    "DIVLOWVOL100": ("fund", "021550"),
    "STOCK": ("fund", "005225"),
    "MIXED": ("fund", "004604"),
    "INDEX": ("fund", "011608"),
    "HK_BROAD": ("yahoo", "2800.HK"),
    "HK_DIV": ("yahoo", "3031.HK"),
    "HK_TECH": ("yahoo", "3067.HK"),
}

# Long-history proxies: when the actual fund/ETF has short history, use a broad
# index/futures proxy that covers a longer economic cycle.
PROXY_MAP = {
    "D_HSBC_SP500": ("yahoo", "^GSPC"),
    "D_HSBC_CSI300": ("yahoo", "000001.SS"),
    "D_HSBC_DIVLOWVOL": ("yahoo", "000001.SS"),
    "D_HSBC_CDB": None,
    "D_HSBC_GOLD": ("yahoo", "GC=F"),
    "D_HSBC_HSTECH": ("yahoo", "^HSI"),
    "D_HSBC_MMF": None,
    "D_CN_CREDIT_BOND": None,
    "D_CN_DIVLOWVOL100": None,
    "D_CN_STOCK": ("yahoo", "000001.SS"),
    "D_CN_MIXED": ("yahoo", "000001.SS"),
    "D_CN_INDEX": ("yahoo", "000001.SS"),
    "D_CN_HK": ("yahoo", "^HSI"),
    "D_CN_QDII_STOCK": ("yahoo", "^GSPC"),
    "D_CN_QDII_STABLE": ("yahoo", "BND"),
    "D_CN_QDII_COMMODITY": ("yahoo", "CL=F"),
    "O_US_CORE": ("yahoo", "^GSPC"),
    "O_BTC": None,
    "O_HYLB": None,
    "O_HK_HSTECH": ("yahoo", "^HSI"),
    "O_HK_HIGHDIV": ("yahoo", "^HSI"),
    "O_JP_EQ": ("yahoo", "^N225"),
    "O_SG_EQ": ("yahoo", "^STI"),
    "O_US_TLT": None,
    "O_US_REIT": ("yahoo", "VNQ"),
    "O_US_ENERGY": ("yahoo", "CL=F"),
    "O_HK_GOLD": ("yahoo", "GC=F"),
    "SPLG": ("yahoo", "^GSPC"),
    "VTV": None,
    "SPMO": None,
    "UNH": None,
    "GOOG": None,
    "AAPL": None,
    "MSFT": None,
    "NVDA": None,
    "AMZN": None,
    "META": None,
    "TSLA": None,
    "BUFFER": None,
}


def series_for_broad_key(work_dir, key, log=None):
    """大类资产的代表收盘序列: 代理历史更长则优先代理 (与校准同逻辑)."""
    src = SOURCES.get(key)
    prices, best_sym = [], None
    if src is not None:
        typ, sym = src
        prices = cached_series(work_dir, typ, sym, log=log)
        best_sym = sym
    proxy = PROXY_MAP.get(key)
    if proxy is not None:
        ptyp, psym = proxy
        try:
            pprices = cached_series(work_dir, ptyp, psym, log=log)
            if len(pprices) > len(prices):
                prices, best_sym = pprices, psym
        except Exception:
            pass
    return prices if len(prices) >= _MIN_SERIES else []
