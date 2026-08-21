#!/usr/bin/env python3
"""Calibrate portfolio-optimizer parameters from market data.

Data sources:
- Yahoo Finance: US/HK/JP/SG ETFs, stocks, BTC, buffer ETF proxy
- Eastmoney: China open-end fund NAV history (LJJZ/cumulative NAV)

Method:
- Use ~3 years of daily data.
- Volatility: annualized std of daily log returns.
- Correlations: daily return correlations on common dates.
- Expected returns: 50% historical annualized return + 50% prior forward-looking assumption,
  clamped to [0%, 15%] to stay forward-looking and avoid overfitting short histories.
- Money-market funds keep a fixed 2%/1% assumption because NAV alone does not reflect money-market yield.

Output:
- /Users/sectator/MEGA/Finance/tmp/calibrated_params.json
"""

import json
import math
import re
import datetime
import time
import ssl
import sys
import urllib.request

import numpy as np
import pandas as pd

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


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


sources = {
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
    # Japan equity factor second-level
    "1364": ("yahoo", "1364.T"),
    "1698": ("yahoo", "1698.T"),
    "2516": ("yahoo", "2516.T"),
    "1477": ("yahoo", "1477.T"),
    "1478": ("yahoo", "1478.T"),
    "1490": ("yahoo", "1490.T"),
    "2529": ("yahoo", "2529.T"),
    # Hong Kong equity factor second-level
    "2800": ("yahoo", "2800.HK"),
    "2828": ("yahoo", "2828.HK"),
    "3031": ("yahoo", "3031.HK"),
    "3067": ("yahoo", "3067.HK"),
    "3110": ("yahoo", "3110.HK"),
    "3070": ("yahoo", "3070.HK"),
    # Greater China equity second-level
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
proxy_map = {
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
    # QDII 细分 (Bug C): 股票用标普代理, 稳健债性用美国综合债 BND, 商品用原油
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

prior = {
    "D_HSBC_SP500": 0.09,
    "D_HSBC_CSI300": 0.08,
    "D_HSBC_DIVLOWVOL": 0.075,
    "D_HSBC_CDB": 0.035,
    "D_HSBC_GOLD": 0.06,
    "D_HSBC_HSTECH": 0.08,
    "D_HSBC_MMF": 0.02,
    "D_CN_CREDIT_BOND": 0.045,
    "D_CN_DIVLOWVOL100": 0.075,
    "D_CN_STOCK": 0.08,
    "D_CN_MIXED": 0.07,
    "D_CN_INDEX": 0.075,
    "D_CN_HK": 0.075,
    "D_CN_QDII_STOCK": 0.085,
    "D_CN_QDII_STABLE": 0.045,
    "D_CN_QDII_COMMODITY": 0.05,
    "O_US_CORE": 0.09,
    "O_BTC": 0.15,
    "O_HYLB": 0.065,
    "O_HK_HSTECH": 0.08,
    "O_HK_HIGHDIV": 0.07,
    "O_JP_EQ": 0.07,
    "O_SG_EQ": 0.065,
    "O_US_TLT": 0.04,
    "O_US_REIT": 0.065,
    "O_US_ENERGY": 0.075,
    "O_HK_GOLD": 0.06,
    "O_GOLD": 0.06,
    "SPLG": 0.09,
    "VTV": 0.085,
    "SPMO": 0.095,
    "UNH": 0.09,
    "GOOG": 0.09,
    "AAPL": 0.09,
    "MSFT": 0.09,
    "NVDA": 0.11,
    "AMZN": 0.095,
    "META": 0.10,
    "TSLA": 0.10,
    "BUFFER": 0.07,
    "1364": 0.07,
    "1698": 0.07,
    "2516": 0.08,
    "1477": 0.065,
    "1478": 0.07,
    "1490": 0.065,
    "2529": 0.075,
    "2800": 0.07,
    "2828": 0.075,
    "3031": 0.07,
    "3067": 0.08,
    "3110": 0.07,
    "3070": 0.07,
    "CSI300": 0.075,
    "DIVLOWVOL": 0.07,
    "DIVLOWVOL100": 0.075,
    "STOCK": 0.08,
    "MIXED": 0.07,
    "INDEX": 0.075,
    "HK_BROAD": 0.07,
    "HK_DIV": 0.07,
    "HK_TECH": 0.08,
}

series = {}
meta = {}
for key, (typ, sym) in sources.items():
    try:
        # Try actual symbol first, using its full available history.
        if typ == "yahoo":
            prices = yahoo_history(sym, years=None)
        else:
            prices = eastmoney_nav(sym, years=None)
        best_prices = prices
        best_sym = sym

        # If a long-history proxy exists and has more data, prefer it.
        proxy = proxy_map.get(key)
        if proxy is not None:
            ptyp, psym = proxy
            try:
                if ptyp == "yahoo":
                    pprices = yahoo_history(psym, years=None)
                else:
                    pprices = eastmoney_nav(psym, years=None)
                if len(pprices) > len(best_prices):
                    best_prices = pprices
                    best_sym = psym
            except Exception:
                pass

        df = to_series(best_prices)
        if df is None or len(df) < 30:
            print(f"{key:22} {best_sym:12} insufficient n={len(best_prices) if best_prices else 0}")
            continue
        series[key] = df
        n = len(df)
        ann_ret = (float(df["close"].iloc[-1]) / float(df["close"].iloc[0])) ** (252.0 / n) - 1.0
        ann_vol = float(df["ret"].std(ddof=1) * math.sqrt(252.0))
        meta[key] = {"symbol": best_sym, "ret": ann_ret, "vol": ann_vol, "n": n}
        print(f"{key:22} {best_sym:12} ret={ann_ret:.4f} vol={ann_vol:.4f} n={n}")
    except Exception as e:
        print(f"{key:22} {sym:12} ERR {type(e).__name__}: {e}")

# Build full return DataFrame (do not drop all rows with any NaN; pairwise corr handles this)
ret_df = pd.DataFrame({k: df.set_index("date")["ret"] for k, df in series.items()})
corr = ret_df.corr(min_periods=180).round(4)
# Pairs with too little common history are not reliable -> use a conservative prior correlation.
corr = corr.fillna(0.30)

# SPLG fallback: if Yahoo did not provide enough SPLG data, use SPY as the same-index proxy.
if "SPLG" not in series and "O_US_CORE" in series:
    for other in list(corr.index):
        corr.loc["SPLG", other] = corr.loc["O_US_CORE", other]
        corr.loc[other, "SPLG"] = corr.loc[other, "O_US_CORE"]
    corr.loc["SPLG", "SPLG"] = 1.0

# Conservative volatility floors: avoid unrealistically low risk from a benign 3y window.
VOL_FLOOR = {
    "O_HYLB": 0.08,
    "O_US_REIT": 0.15,
    "O_US_ENERGY": 0.20,
    "O_BTC": 0.40,
    "D_HSBC_GOLD": 0.15,
    "O_HK_GOLD": 0.15,
    "O_GOLD": 0.15,
    "O_HK_HSTECH": 0.25,
    "D_HSBC_HSTECH": 0.25,
    "O_JP_EQ": 0.15,
    "O_SG_EQ": 0.13,
    "O_US_TLT": 0.10,
    "BUFFER": 0.08,
    "1364": 0.15,
    "1698": 0.13,
    "2516": 0.18,
    "1477": 0.10,
    "1478": 0.12,
    "1490": 0.10,
    "2529": 0.14,
    "2800": 0.18,
    "2828": 0.20,
    "3031": 0.14,
    "3067": 0.25,
    "3110": 0.14,
    "3070": 0.14,
    "CSI300": 0.17,
    "DIVLOWVOL": 0.14,
    "DIVLOWVOL100": 0.14,
    "STOCK": 0.18,
    "MIXED": 0.13,
    "INDEX": 0.15,
    "HK_BROAD": 0.20,
    "HK_DIV": 0.14,
    "HK_TECH": 0.25,
    "SPMO": 0.18,
    "UNH": 0.20,
    "GOOG": 0.20,
    "AAPL": 0.20,
    "MSFT": 0.20,
    "NVDA": 0.35,
    "AMZN": 0.25,
    "META": 0.25,
    "TSLA": 0.40,
    "SPLG": 0.15,
    "VTV": 0.13,
    "D_HSBC_SP500": 0.15,
    "D_HSBC_CSI300": 0.17,
    "D_CN_STOCK": 0.18,
    "D_CN_MIXED": 0.13,
    "D_CN_INDEX": 0.15,
    "D_CN_HK": 0.22,
    "D_CN_QDII_STOCK": 0.15,
    "D_CN_QDII_STABLE": 0.05,
    "D_CN_QDII_COMMODITY": 0.20,
}

calib = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "assets": {},
    "us_internal": {},
    "jp_internal": {},
    "hk_internal": {},
    "gc_internal": {},
    "corr": {f"{a}|{b}": float(corr.loc[a, b]) for a in corr.index for b in corr.index if a < b},
}
JP_KEYS = ["1364", "1698", "2516", "1477", "1478", "1490", "2529"]
HK_KEYS = ["2800", "2828", "3031", "3067", "3110", "3070"]
GC_KEYS = ["CSI300", "DIVLOWVOL", "DIVLOWVOL100", "STOCK", "MIXED", "INDEX", "HK_BROAD", "HK_DIV", "HK_TECH"]
for key, st in meta.items():
    mu_hist = st["ret"]
    mu_prior = prior.get(key, 0.07)
    mu = 0.5 * mu_hist + 0.5 * mu_prior
    mu = max(0.0, min(0.15, mu))
    vol = max(st["vol"], VOL_FLOOR.get(key, 0.0))
    vol = min(vol, 0.6)  # sanity cap: avoid data glitches / unrealistic single-name vol
    if key == "D_HSBC_MMF":
        mu = 0.02
        vol = 0.01
    calib["assets"][key] = {"mu": round(mu, 4), "vol": round(vol, 4)}
    if key in ["SPLG", "VTV", "SPMO", "UNH", "GOOG", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "BUFFER"]:
        calib["us_internal"][key] = {"mu": round(mu, 4), "vol": round(vol, 4)}
    if key in JP_KEYS:
        calib["jp_internal"][key] = {"mu": round(mu, 4), "vol": round(vol, 4)}
    if key in HK_KEYS:
        calib["hk_internal"][key] = {"mu": round(mu, 4), "vol": round(vol, 4)}
    if key in GC_KEYS:
        calib["gc_internal"][key] = {"mu": round(mu, 4), "vol": round(vol, 4)}

# SPLG fallback to SPY proxy if missing
if "SPLG" not in calib["assets"] and "O_US_CORE" in calib["assets"]:
    calib["assets"]["SPLG"] = dict(calib["assets"]["O_US_CORE"])
    calib["us_internal"]["SPLG"] = dict(calib["assets"]["O_US_CORE"])

_OUT = "/Users/sectator/MEGA/Finance/tmp/calibrated_params.json"
if len(sys.argv) > 1 and sys.argv[1] == "--out" and len(sys.argv) > 2:
    _OUT = sys.argv[2]  # 允许写出到任意路径(如 bundle data 目录), 避免沙箱只允许工作区
with open(_OUT, "w") as f:
    json.dump(calib, f, ensure_ascii=False, indent=2)
print(f"\nsaved {_OUT}")
print("assets calibrated:", len(calib["assets"]))
print("corr pairs:", len(calib["corr"]))
