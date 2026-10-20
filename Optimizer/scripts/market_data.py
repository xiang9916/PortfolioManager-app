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
- cached_series: work_dir 内按 symbol 缓存, 优化器关闭时随 work_dir 一起清理;
  目录自带取数口径版本标记, 升级/崩溃残留导致的旧日期缓存会自动失效
"""

import datetime
import json
import math
import os
import re
import shutil
import ssl
import time
import urllib.request

import pandas as pd

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

_MIN_SERIES = 30

# 东财 pingzhongdata 的 `x` 毫秒时间戳按**北京时间**记账（当日 00:00），
# 取日必须用 UTC+8；按 UTC 取会把境内基金日期整体提前一天。
_CN_TZ = datetime.timezone(datetime.timedelta(hours=8))

# prices_cache 的格式/取数口径版本。写入 work_dir/prices_cache/.cache_version；
# 标记不一致或缺失时缓存目录整体清空（见 _prices_cache_dir）。
# 2 = 2026-10-18 起：东财毫秒时间戳按北京时间（UTC+8）取日。
PRICES_CACHE_SCHEMA = 2


def _eastmoney_date(ts_ms):
    """东财毫秒时间戳 → 北京时间日期（纯函数：不联网、不依赖本机时区）。"""
    return datetime.datetime.fromtimestamp(ts_ms / 1000, _CN_TZ).date()


def yahoo_history(ticker, years=None, adjusted=False, on_fallback=None):
    """日线收盘序列 [(date, close)]。

    adjusted=True 时取复权收盘价（adjclose，含分红/派息）。做**远期收益/收益率回归**
    与总回报校准必须用它：价格序列把分红/票息丢掉了，会系统性低估收益（ADR 0005）。
    adjclose 缺失时回落 close；传了 on_fallback 就通知调用方（校准侧据此报警）。
    """
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
    if adjusted:
        adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
        if adj:
            closes = adj
        elif on_fallback is not None:
            on_fallback(ticker)
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
        d = _eastmoney_date(ts)
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


def _prices_cache_dir(work_dir):
    """取 prices_cache 目录, 并做**版本自愈**: 标记不匹配/缺失时整体清空后重建.

    为什么必须有这一步: 缓存文件存的是 `[date, close]`, 日期标签由取数口径决定。
    口径修正后（例如 2026-10-18 东财时间戳改按北京时间取日）旧缓存里的日期是错的,
    「命中即复用」会让修正对老用户**不生效**。正常关闭 App 会删整个 work_dir, 但
    崩溃 / 强制退出 / 跨版本升级时的残留只能靠这里兜底。

    返回 (cache_dir) 路径; 清空是原子的「删目录 → 重建 → 写标记」。
    """
    cache_dir = os.path.join(work_dir, "prices_cache")
    marker = os.path.join(cache_dir, ".cache_version")
    version = None
    try:
        with open(marker, encoding="utf-8") as f:
            version = int(f.read().strip())
    except Exception:
        version = None
    if version != PRICES_CACHE_SCHEMA:
        shutil.rmtree(cache_dir, ignore_errors=True)
        os.makedirs(cache_dir, exist_ok=True)
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(str(PRICES_CACHE_SCHEMA))
        except Exception:
            pass
    else:
        os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def cached_series(work_dir, source, symbol, log=None):
    """按 (source, symbol) 缓存全历史收盘序列 [(date, close)], 命中直接返回.

    缓存文件位于 work_dir/prices_cache/, 生命周期与优化器联网管线共享目录一致
    (App 关闭优化器时由 Swift 侧 cleanupWorkDir 整体删除)。

    缓存里存的是**日期标签**, 取数口径一旦变化旧标签就永远是错的（不会自愈）——
    因此目录带版本标记, 由 `_prices_cache_dir` 在版本不一致时整体清空。
    """
    cache_dir = _prices_cache_dir(work_dir)
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
    "D_HSBC_CDB": ("fund", "007485"),
    "D_HSBC_MMF": ("fund", "000891"),
    "D_CN_CREDIT_BOND": ("fund", "110035"),
    "D_CN_QDII_STOCK": ("fund", "000043"),
    "D_CN_QDII_STABLE": ("fund", "017970"),
    "D_CN_QDII_COMMODITY": ("yahoo", "CL=F"),
    # 大中华权益一级大类（桶内＝沪深300/红利低波/主动/港股混合，没有自己的行情）
    # → 用恒生指数长历史作为代表（2026-10-14 用户定稿）
    "D_GREATER_CN": ("yahoo", "^HSI"),
    "O_US_CORE": ("yahoo", "SPY"),
    "O_BTC": ("yahoo", "BTC-USD"),
    "O_HYLB": ("yahoo", "HYLB"),
    "O_JP_EQ": ("yahoo", "1698.T"),
    "O_SG_EQ": ("yahoo", "G3B.SI"),
    "O_US_TLT": ("yahoo", "TLH"),
    "O_US_REIT": ("yahoo", "REZ"),
    "O_US_ENERGY": ("yahoo", "XOM"),
    "O_GOLD": ("yahoo", "GC=F"),
    # 标普500 宽基档用 SPY（SPDR S&P 500 ETF Trust，1993 年成立、数据最稳）。
    # 历史坑：这一档原写 SPLG（SPDR Portfolio S&P 500 ETF）——发行方后来把代码改成 SPYM，
    # 旧代码在 Yahoo 一律 404；而 Yahoo 搜 "SPLG" 命中的是 LSE 上的 Invesco 标普500低波
    # UCITS（另一只基金）。2026-10-14 用户定稿：直接用 SPY，不再挂会 404 的旧代码。
    "SPY": ("yahoo", "SPY"),
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
    # 短样本标的的长历史代理（真实标的历史太短会让 μ 每次校准都跳）
    "HK_DIV": ("yahoo", "^HSI"),              # 3031.HK 仅 129 天 → 恒指长历史
    "DIVLOWVOL100": ("fund", "005051"),        # 021550 仅 532 天 → 同类红利低波策略长历史
    "D_HSBC_SP500": ("yahoo", "^GSPC"),
    "D_HSBC_CDB": None,
    "D_HSBC_MMF": None,
    "D_CN_CREDIT_BOND": None,
    "D_CN_DIVLOWVOL100": None,
    "D_CN_QDII_STOCK": ("yahoo", "^GSPC"),
    "D_CN_QDII_STABLE": ("yahoo", "BND"),
    "D_CN_QDII_COMMODITY": ("yahoo", "CL=F"),
    "D_GREATER_CN": None,                      # 代表序列本身就是恒指，无需再配代理
    "O_US_CORE": ("yahoo", "^GSPC"),
    "O_BTC": None,
    "O_HYLB": None,
    "O_JP_EQ": None,                         # 与执行标的 1698.T 同源，避免同敞口两套参数
    "O_SG_EQ": ("yahoo", "^STI"),
    "O_US_TLT": None,
    "O_US_REIT": ("yahoo", "VNQ"),
    "O_US_ENERGY": ("yahoo", "CL=F"),
    "SPY": ("yahoo", "^GSPC"),
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
    prices = []
    if src is not None:
        typ, _sym = src
        prices = cached_series(work_dir, typ, _sym, log=log)
    proxy = PROXY_MAP.get(key)
    if proxy is not None:
        ptyp, psym = proxy
        try:
            pprices = cached_series(work_dir, ptyp, psym, log=log)
            if len(pprices) > len(prices):
                prices = pprices
        except Exception:
            pass
    return prices if len(prices) >= _MIN_SERIES else []


# ---------------------------------------------------------------- 缓冲期权篮子（ADR 0006）
# 成员是同一系列、按季度错峰的四只 Max Buffer ETF。篮子 = 等权、每季度初再平衡；
# 历史只取四只**同时存续**后的共同窗口（不向前拼接），单日缺口取交集。
# 本函数是纯函数：取数经 fetch 注入，便于离线单测（不联网、不读真实持仓）。

def _quarter_key(day):
    """日期的日历季度标识 (year, 0..3)。"""
    return (day.year, (day.month - 1) // 3)


def build_buffer_basket(members, fetch, min_days=30):
    """四只等权、季度再平衡的合成净值序列。

    参数
    ----
    members : 有序 symbol 序列（等权；顺序只影响输出，不影响数值）
    fetch   : callable(symbol) -> [(date, close)]；取数失败可抛异常
    min_days: 共同窗口的最小交易日数（不足视为不可用）

    返回 (series, meta)：
      - 可用：series = [(date, level)]，level 在共同窗口首日归一为 1.0；
        meta = {members, window, n_days, rebalance, rebalance_days}
      - 不可用：series = None，meta 带 reason（missing_member / short_window / no_members）
        以及 missing / errors / n_days 等诊断字段。
    """
    members = [str(m).strip() for m in (members or []) if str(m).strip()]
    if not members:
        return None, {"reason": "no_members", "members": []}

    prices, missing, errors = {}, [], {}
    for sym in members:
        try:
            rows = fetch(sym) or []
        except Exception as exc:  # noqa: BLE001 — 单只失败由调用方决定丢弃整条篮子
            rows = []
            errors[sym] = f"{type(exc).__name__}: {exc}"
        if len(rows) < 2:
            missing.append(sym)
            continue
        prices[sym] = dict(rows)

    if missing:
        return None, {"reason": "missing_member", "members": members,
                      "missing": missing, "errors": errors}

    common = sorted(set.intersection(*(set(prices[s]) for s in members)))
    if len(common) < min_days:
        return None, {"reason": "short_window", "members": members,
                      "n_days": len(common), "errors": errors}

    # 每个日历季度的第一个共同交易日 = 再平衡日；首日已经是等权，不再重复调仓。
    first_in_quarter = {}
    for day in common:
        first_in_quarter.setdefault(_quarter_key(day), day)
    rebalance_days = {d for d in first_in_quarter.values() if d != common[0]}

    weight = 1.0 / len(members)
    units = {sym: weight / prices[sym][common[0]] for sym in members}
    series = []
    for day in common:
        if day in rebalance_days:
            value = sum(units[sym] * prices[sym][day] for sym in members)
            units = {sym: (value * weight) / prices[sym][day] for sym in members}
        series.append((day, sum(units[sym] * prices[sym][day] for sym in members)))

    base = series[0][1]
    if base <= 0:
        return None, {"reason": "bad_level", "members": members, "n_days": len(common)}
    series = [(day, value / base) for day, value in series]
    meta = {
        "members": members,
        "window": [common[0].isoformat(), common[-1].isoformat()],
        "n_days": len(common),
        "rebalance": "quarterly",
        "rebalance_days": [d.isoformat() for d in sorted(rebalance_days)],
    }
    if errors:
        meta["errors"] = errors
    return series, meta
