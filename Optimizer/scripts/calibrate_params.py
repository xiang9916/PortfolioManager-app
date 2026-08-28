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
import datetime
import sys


import numpy as np
import pandas as pd

from market_data import (
    SOURCES as sources,
    PROXY_MAP as proxy_map,
    eastmoney_nav,
    to_series,
    yahoo_history,
)

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
