#!/usr/bin/env python3
"""Calibrate portfolio-optimizer parameters from market data.

规则（混合比例、截断线、历史窗口、兜底矩阵）全部定义在
`asset_classes.py` —— 本文件只负责取数、算历史年化收益/波动、按规则组装。

Data sources:
- Yahoo Finance: US/HK/JP/SG ETFs, stocks, BTC；缓冲期权 = 四只 Max Buffer 的等权篮子
- Eastmoney: China open-end fund NAV history (LJJZ/cumulative NAV)

Method:
- 序列口径：Yahoo 一律取复权收盘价（adjclose，含分红/派息；缺失回落 close 并报警），
  中国基金走累计净值 —— 统一为总回报（ADR 0005）。
- 历史窗口按类别：宽基/大类 = 全历史；策略指数 = 20 年；个股/主动 = 10 年（不足则用可得全历史）。
- σ = 日收益标准差 × √252（窗口内），**无人工下限**（2026-10-14 取消）。
- 相关性 = 窗口内日收益的皮尔逊相关（共同日期 ≥180 天），不足填 0.30。
- 见 asset_classes.py 的 docstring：三个类别各自的 μ 构造与兜底矩阵。

Output:
- $DSH_FINANCE_DIR/calibrated_params.json (默认 ~/Finance/tmp/)，或 --out 指定

JSON 里额外写入：
- `provenance[key]`: {class, window_years, n_days, mu_hist, mu, vol, note, vol_note, source}
  —— 说明每个数的来历（历史 / 人工先验 / 人工下限 / 写死），供 App 与审计追溯。
- `warnings`: 需要人看的提示（丢弃、σ 毛刺、缺少先验用纯历史、无行情用先验…）。
"""

import json
import math
import datetime
import os
import sys

import pandas as pd

from market_data import (
    SOURCES as sources,
    PROXY_MAP as proxy_map,
    build_buffer_basket,
    eastmoney_nav,
    to_series,
    yahoo_history,
)
import asset_classes as AC

PROVENANCE, WARNINGS, DROPPED = {}, [], []


def _fetch(typ, sym, years, adjusted=False):
    """adjusted=True → Yahoo 取复权价（含分红/票息）。中国基金走累计净值，天然是全收益。

    adjclose 缺失时 yahoo_history 回落 close；这里把回落记进 WARNINGS（ADR 0005）。
    """
    if typ == "yahoo":
        def _fallback(symbol):
            WARNINGS.append(f"{symbol}: adjclose 不可用，已回落 close（总回报口径不完整）")
        return yahoo_history(sym, years=years, adjusted=adjusted,
                             on_fallback=_fallback if adjusted else None)
    return eastmoney_nav(sym, years=years)


def _label(key):
    return f"{key}（{AC.ASSET_CLASS.get(key, '未登记')}）"


def _pick_series(key, sym, fetched, years):
    """在"真实标的"与"长历史代理"之间选序列。

    规则（2026-10-13 修）：窗口内已经够长的就用**真实标的**，代理只在真实标的
    铺不满窗口（或取数失败）时顶上。此前一律"谁的点数多用谁"，导致 10 年窗口下
    原油期货 CL=F 的点数超过 XOM，把"能源生产商个股"的 σ 算成了 114%。
    """
    actual = fetched.get(sym) or []
    proxy_syms = [s for s in fetched if s != sym]
    proxy_sym = proxy_syms[0] if proxy_syms else None
    proxy = fetched.get(proxy_sym) or [] if proxy_sym else []
    if not proxy:
        return actual, (sym if actual else None)
    if not actual:
        return proxy, proxy_sym
    if years is not None:
        need = int(years * 252 * 0.85)      # 铺满窗口的 85% 即认为够长
        if len(actual) >= need:
            return actual, sym
        if len(proxy) >= need:
            return proxy, proxy_sym
    return (proxy, proxy_sym) if len(proxy) > len(actual) else (actual, sym)


series = {}
meta = {}
retired = []

# ── 债券大类的**动态先验**：先验 =（回归 μ + 当期锚定收益率）/2 ───────────────────
# 回归自变量固定为同币种 10 年期国债收益率，锚定期限见 asset_classes.BOND_PRIOR_FORMULA。
# 取数/拟合失败 → 回落到静态先验并报警（公式是主路径，写死值只是保险）。
BOND_ANCHORS = {}
BOND_DETAIL = {}
PRIOR_DETAIL = {}
_YIELD_HISTORY = []
try:
    import bond_prior as _BP
    _YIELD_HISTORY = _BP.fetch_yield_series(log=lambda m: print(m))
    BOND_ANCHORS = _BP.latest_anchors(_YIELD_HISTORY)
except Exception as exc:  # noqa: BLE001
    WARNINGS.append(f"国债收益率取数失败（{type(exc).__name__}: {exc}），"
                    f"债券公式与 RF+溢价 先验本次均回落到兜底表")

# 本币无风险利率：**只来自数据源**（东财中美国债收益率），程序里不写死。
# 先验 = 本币 RF + 溢价（溢价见 asset_classes.RF_PREMIUM）。
_PRIOR_RF = {"usd": BOND_ANCHORS.get("us_10y"), "cny": BOND_ANCHORS.get("cn_10y")}
if any(v is None for v in _PRIOR_RF.values()):
    WARNINGS.append(f"本币无风险利率不完整（usd={_PRIOR_RF['usd']}、cny={_PRIOR_RF['cny']}），"
                    f"相关先验回落到兜底表")
else:
    AC.set_rf(_PRIOR_RF)
    print(f"本币无风险利率（数据源）：USD 10y {_PRIOR_RF['usd']:.3%}、CNY 10y {_PRIOR_RF['cny']:.3%}")


def _resolve_bond_prior(key, prices, adjusted_prices=None):
    """按动态公式算该债券标的的先验；(prior, detail) —— 失败时 prior 为 None。

    `adjusted_prices`：债券 ETF 的**复权**序列。回归必须用它 —— 价格序列把票息丢掉了，
    用未复权价会系统性低估远期收益、把斜率和截距都带偏（实测 TLT：0.53/3.36 → 0.60/3.64）。
    """
    if not _YIELD_HISTORY:
        return None, {"reason": "no_yield_history"}
    try:
        import bond_prior as _BP
        spec = AC.BOND_PRIOR_FORMULA[key]
        x = _BP.yield_series_for_market(_YIELD_HISTORY, spec["market"])
        use = adjusted_prices if (adjusted_prices and len(adjusted_prices) >= 260) else prices
        fit = _BP.fit_asset(use, x)
        fit["adjusted"] = use is adjusted_prices
        m = fit.get("monthly")
        if not m:
            return None, {"reason": fit.get("error", "fit_failed")}
        fits = {key: {"a": m["a"], "b": m["b"], "r2": m["r2"], "n_obs": fit["n"],
                      "n_years": fit["n_years"], "source": None,
                      "window": [fit["first"], fit["last"]]}}
        return AC.bond_prior(key, BOND_ANCHORS, fits)
    except Exception as exc:  # noqa: BLE001
        return None, {"reason": f"{type(exc).__name__}: {exc}"}

for key, (typ, sym) in sources.items():
    cls = AC.asset_class(key)
    if cls is None:
        # 已退役的候选代码（不在任何池里、也没登记类别）→ 汇总成一条提示，不再逐个刷屏
        retired.append(key)
        continue
    years = AC.window_years(key)
    try:
        basket_meta = None
        if key == "BUFFER":
            # ADR 0006：四只 Max Buffer 的等权篮子，历史取共同窗口、每季度初再平衡。
            best_prices, basket_meta = build_buffer_basket(
                AC.BUFFER_FAMILY,
                lambda symbol: _fetch("yahoo", symbol, years, adjusted=True),
            )
            best_sym, best_typ = "SYN:4", "yahoo"
            if not best_prices:
                missing = basket_meta.get("missing") or []
                reason = (f"缺 {'、'.join(missing)}" if missing
                          else str(basket_meta.get("reason")))
                WARNINGS.append(f"{_label(key)}: 四只篮子序列不可用（{reason}）"
                                f" → 丢弃（不再参与优化）")
                DROPPED.append(key)
                PROVENANCE[key] = {"class": cls, "window_years": years,
                                   "n_days": basket_meta.get("n_days", 0),
                                   "source": None, "note": "no_history",
                                   "adjusted": True, "series": "synthetic_basket",
                                   **basket_meta}
                continue
        else:
            fetched = {}
            for _typ, _sym in ((typ, sym),) + (((proxy_map[key]),) if proxy_map.get(key) else ()):
                try:
                    fetched[_sym] = _fetch(_typ, _sym, years, adjusted=True)
                except Exception as exc:  # 单个源失败不该丢掉整个标的（源站偶尔 404/限流）
                    fetched[_sym] = []
                    if _sym == sym:
                        WARNINGS.append(f"{_label(key)}: {_sym} 取数失败 "
                                        f"{type(exc).__name__}: {exc}，尝试代理源")
            best_prices, best_sym = _pick_series(key, sym, fetched, years)
            best_typ = typ
            if best_sym != sym and proxy_map.get(key):
                best_typ = proxy_map[key][0]
        if not best_prices:
            if key in AC.SPECIAL_FIXED:
                WARNINGS.append(f"{_label(key)}: 所有数据源都取不到行情，该标的另有写死值，用写死值兜底")
            else:
                WARNINGS.append(f"{_label(key)}: 所有数据源都取不到行情，且没有写死值可用 → 丢弃（不再参与优化）")
                DROPPED.append(key)
            PROVENANCE[key] = {"class": cls, "window_years": years, "n_days": 0,
                               "source": None, "note": "no_history",
                               "adjusted": None, "series": None}
            continue
        # 窗口裁剪：取不到那么长就用可得全历史
        windowed = AC.slice_window(best_prices, years)
        df = to_series(windowed)
        if df is None or len(windowed) < 30:
            if key in AC.SPECIAL_FIXED:
                WARNINGS.append(f"{_label(key)}: 行情历史不足 30 天（{best_sym}），用写死值兜底")
            else:
                WARNINGS.append(f"{_label(key)}: 行情历史不足 30 天（{best_sym}）→ 丢弃（不再参与优化）")
                DROPPED.append(key)
            PROVENANCE[key] = {"class": cls, "window_years": years, "n_days": len(windowed or []),
                               "source": best_sym, "note": "no_history",
                               "adjusted": best_typ == "yahoo",
                               "series": "adjclose" if best_typ == "yahoo" else "cumulative_nav"}
            continue
        series[key] = df
        n = len(df)
        ann_ret = (float(df["close"].iloc[-1]) / float(df["close"].iloc[0])) ** (252.0 / n) - 1.0
        ann_vol = float(df["ret"].std(ddof=1) * math.sqrt(252.0))

        if key in AC.BOND_PRIOR_FORMULA:
            # 主序列已是复权（ADR 0005），不再重复取数；基金侧本身就是累计净值。
            adj = windowed if best_typ == "yahoo" else None
            prior_val, detail = _resolve_bond_prior(key, windowed, adj)
            BOND_DETAIL[key] = detail
            if prior_val is not None:
                AC.set_prior_overrides({**AC.PRIOR_OVERRIDE, key: prior_val})
                WARNINGS.append(
                    f"{_label(key)}: 先验走动态公式 {prior_val:.2%} =（回归 μ {detail['reg_mu']:.2%}"
                    f" + {detail['anchor_label']} {detail['anchor_value']:.2%}）/2"
                    f"（拟合 a={detail['a']:.2f}、R²={detail['r2']:.2f}、非重叠 {detail['n_years']} 个窗口）")
            else:
                WARNINGS.append(
                    f"{_label(key)}: 动态先验不可用（{detail.get('reason')}），"
                    f"回落到静态先验 {AC.PRIOR[key]:.2%}")
        if key == "BUFFER":
            # ADR 0006：μSPY 指 SPY 的**先验**（RF+4%），不是校准后的 μ。
            # 先验 = min((0.5×μSPY先验 + 0.5×Rf)/2, 10%)。
            spy_prior = AC.prior_for("SPY")
            rf_usd = _PRIOR_RF.get("usd")
            prior_val = AC.buffer_prior(spy_prior, rf_usd)
            if prior_val is not None:
                AC.set_prior_overrides({**AC.PRIOR_OVERRIDE, key: prior_val})
                PRIOR_DETAIL[key] = {
                    "formula": "min((0.5*mu_SPY_prior + 0.5*rf_usd)/2, 10%)",
                    "mu_spy_prior": spy_prior, "rf_usd": rf_usd,
                    "cap": AC.BUFFER_PRIOR["cap"], "prior": prior_val,
                }
                WARNINGS.append(
                    f"{_label(key)}: 先验走动态公式 {prior_val:.2%} = "
                    f"min((0.5×μSPY先验 {spy_prior:.2%} + 0.5×Rf {rf_usd:.2%})/2, "
                    f"{AC.BUFFER_PRIOR['cap']:.0%})")
            else:
                PRIOR_DETAIL[key] = {"reason": "missing_mu_spy_prior_or_rf",
                                     "mu_spy_prior": spy_prior, "rf_usd": rf_usd}
                WARNINGS.append(
                    f"{_label(key)}: 动态先验不可用（μSPY先验={spy_prior}、Rf={rf_usd}），"
                    f"回落到静态先验 {AC.prior_for(key):.2%}")

        mu, note = AC.build_mu(key, ann_ret)
        vol, vol_note = AC.build_vol(key, ann_vol)
        if vol_note == "sigma_glitch":
            WARNINGS.append(f"{_label(key)}: σ={ann_vol:.0%} 超过 150%，按数据毛刺丢弃（不再参与优化）")
            DROPPED.append(key)
            series.pop(key, None)
            continue
        if mu is None or vol is None:
            WARNINGS.append(f"{_label(key)}: 既有行情也缺写死兜底值，已丢弃（不再参与优化）")
            DROPPED.append(key)
            series.pop(key, None)
            continue

        if note == "prior_fallback":
            WARNINGS.append(f"{_label(key)}: 无行情历史，改用人工先验 μ={mu:.2%}")
        elif note == "history_only":
            WARNINGS.append(f"{_label(key)}: 无人工先验，broad/strategy 类改用纯历史 μ={mu:.2%}（口径与其它标的不同）")
        if note == "fixed":
            WARNINGS.append(f"{_label(key)}: 货币基金按写死值 μ=2%、σ=1%")

        meta[key] = {"symbol": best_sym, "ret": ann_ret, "vol": ann_vol, "n": n}
        PROVENANCE[key] = {
            "class": cls, "window_years": years, "n_days": n, "source": best_sym,
            "adjusted": best_typ == "yahoo",
            "series": ("synthetic_basket" if basket_meta
                       else ("adjclose" if best_typ == "yahoo" else "cumulative_nav")),
            "mu_hist": round(ann_ret, 6), "vol_hist": round(ann_vol, 6),
            "mu": round(mu, 6), "vol": round(vol, 6),
            "note": note or "blend", "vol_note": vol_note or "history",
            "prior": AC.prior_for(key),
            "prior_formula": BOND_DETAIL.get(key) or PRIOR_DETAIL.get(key),
        }
        if basket_meta:
            PROVENANCE[key].update(basket_meta)
        print(f"{key:22} {best_sym:12} {AC.CLASS_RULES[cls]['label']:<6} n={n:>6} "
              f"hist={ann_ret:>7.2%} → μ={mu:>7.2%}  vol {ann_vol:>6.2%} → {vol:>6.2%}")
    except Exception as e:
        WARNINGS.append(f"{_label(key)}: 取数失败 {type(e).__name__}: {e}")
        print(f"{key:22} {sym:12} ERR {type(e).__name__}: {e}")

if retired:
    WARNINGS.append(
        f"已退役的候选代码 {len(retired)} 个（不在任何池里、未登记类别，已跳过）: " + "、".join(retired))

# ---- 相关性：窗口内日收益的皮尔逊相关（不丢全 NaN 行；pairwise 处理缺失）----
ret_df = pd.DataFrame({k: df.set_index("date")["ret"] for k, df in series.items()})
corr = ret_df.corr(min_periods=180).round(4)
missing_pairs = int(corr.isna().sum().sum() // 2)
corr = corr.fillna(0.30)
if missing_pairs:
    WARNINGS.append(f"相关性：{missing_pairs} 对共同历史不足 180 天，已按保守先验 0.30 填充")

# SPY 兜底：这一档与 O_US_CORE 是同一个敞口，取数失败时直接借它的相关性行
if "SPY" not in series and "O_US_CORE" in series:
    for other in list(corr.index):
        corr.loc["SPY", other] = corr.loc["O_US_CORE", other]
        corr.loc[other, "SPY"] = corr.loc["O_US_CORE", other]
    corr.loc["SPY", "SPY"] = 1.0

calib = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "rules": "asset_classes.py（2026-10-13 定稿：按类别混合/截断/窗口 + 兜底矩阵；ADR 0005 起统一总回报序列）",
    "series_basis": "total_return",
    "assets": {},
    "us_internal": {},
    "jp_internal": {},
    "hk_internal": {},
    "gc_internal": {},
    "corr": {f"{a}|{b}": float(corr.loc[a, b]) for a in corr.index for b in corr.index if a < b},
    "provenance": PROVENANCE,
    "warnings": WARNINGS,
    # 运行期解析出的先验（债券动态公式的结果）。params.py 加载时用它覆盖静态兜底表，
    # 于是"公式"这条路径离线也能复现（bundle 里就带这个 JSON）。
    # 解析后的全部先验（RF+溢价 / 债券公式）——params.py 加载时注入，离线可复现
    "priors": {k: round(float(AC.prior_for(k)), 6) for k in AC.ASSET_CLASS
               if AC.prior_for(k) is not None},
    "prior_rf": {k: v for k, v in _PRIOR_RF.items() if v is not None},
    "bond_anchors": {k: round(float(v), 6) for k, v in BOND_ANCHORS.items()},
    "bond_prior_detail": BOND_DETAIL,
}
JP_KEYS = ["1364", "1698", "2516", "1477", "1478", "1490", "2529"]
HK_KEYS = ["2800", "2828", "3031", "3067", "3110", "3070"]
GC_KEYS = ["CSI300", "DIVLOWVOL", "DIVLOWVOL100", "STOCK", "MIXED", "INDEX", "HK_BROAD", "HK_DIV", "HK_TECH"]
for key, st in meta.items():
    p = PROVENANCE[key]
    entry = {"mu": round(p["mu"], 4), "vol": round(p["vol"], 4)}
    calib["assets"][key] = entry
    if key in ["SPY", "VTV", "SPMO", "UNH", "GOOG", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "BUFFER"]:
        calib["us_internal"][key] = entry
    if key in JP_KEYS:
        calib["jp_internal"][key] = entry
    if key in HK_KEYS:
        calib["hk_internal"][key] = entry
    if key in GC_KEYS:
        calib["gc_internal"][key] = entry

# 没有行情的标的：这里**不写兜底值**——params.py 加载时只对"有写死值"的标的
# （SPECIAL_FIXED，如货币基金）兜底，其余一律按缺失处理并报警，避免编造 σ。
for key, cls in AC.ASSET_CLASS.items():
    if key in calib["assets"]:
        continue
    PROVENANCE.setdefault(key, {"class": cls, "note": "no_history"})
    if cls == "active":
        WARNINGS.append(f"{_label(key)}: 无行情历史，active 类不设先验/下限，已丢弃（不再参与优化）")

_OUT = os.path.join(os.environ.get("DSH_FINANCE_DIR", os.path.expanduser("~/Finance/tmp")),
                    "calibrated_params.json")
if len(sys.argv) > 1 and sys.argv[1] == "--out" and len(sys.argv) > 2:
    _OUT = sys.argv[2]  # 允许写出到任意路径(如 bundle data 目录), 避免沙箱只允许工作区
with open(_OUT, "w") as f:
    json.dump(calib, f, ensure_ascii=False, indent=2)

print(f"\nsaved {_OUT}")
print("assets calibrated:", len(calib["assets"]))
print("corr pairs:", len(calib["corr"]))
print(f"dropped: {DROPPED or '无'}")
print(f"warnings: {len(WARNINGS)}")
for w in WARNINGS:
    print("  -", w)
