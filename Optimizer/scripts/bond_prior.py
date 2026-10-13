#!/usr/bin/env python3
"""债券大类先验的回归法（2026-10-13 用户提出的口径）。

方法
----
对每个债券大类，拟合

    y_t = a · x_t + b

- `x_t` = t 时刻**同币种**的 10 年期国债收益率（US 债用美 10y，CN 债用中 10y）
- `y_t` = 从 t 时刻起**持有 5 年的年化收益率**（用该资产的全收益/累计净值序列）
- t 取"距现在 > 5 年"的所有可得时点（这样 y 才观测得到）
- 再把**当下**的 10y 收益率 r 代入：μ = a·r + b

不预设斜率符号：利率高时买入债券后续收益更高（a>1，估值均值回复）还是更低（a<1），由数据决定。

实现要点（都是踩过的坑）
------------------------
1. **必须用全收益序列**：美债 ETF 走 Yahoo `adjclose`（含票息），中国基金走天天基金累计净值；
   用未复权价会把票息丢掉，斜率符号都可能反。
2. **重叠窗口**：5 年远期收益按月采样时相邻样本重叠 59/60，OLS 的 R²/显著性会虚高。
   本脚本同时给"逐月（重叠）"与"逐年（近似独立）"两组拟合，后者更保守。
3. **数据深度**：东财「中美国债收益率」实测只能翻到 2008-01（CN 10y 历史 ≈ 18 年），
   于是 t 可用区间 ≈ 2008–2021，**非重叠 5 年窗口只有 2~3 个** —— CN 的斜率不可靠，
   脚本会显式标注置信度。

用法
----
    Optimizer/.venv/bin/python3 Optimizer/scripts/bond_prior.py            # 打印报告
    Optimizer/.venv/bin/python3 Optimizer/scripts/bond_prior.py --json out.json
"""

import argparse
import datetime
import os
import json
import statistics
import sys
import urllib.parse
import urllib.request

NOMINAL_YEARS = 5.0
MIN_OBS = 24          # 少于这么多个逐月观测点就不给结论
_MACRO_API = "https://datacenter.eastmoney.com/api/data/get"
_MACRO_TOKEN = "894050c76af8597a853f5b408b759f5d"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_classes as AC           # noqa: E402  锚定期限表（唯一事实来源）


# ---------------------------------------------------------------- 10y 收益率历史

def fetch_yield_series(pages=12, log=print):
    """取 ANCHOR_SPECS 里所有东财字段的历史 → [(date, {anchor_name: value})]（升序）。

    实测该接口每页 500 行、有效页约 12 页（2004-04 起）；中美休市日不同步，各自取自己有值的行。
    """
    wanted = {name: spec["field"] for name, spec in AC.ANCHOR_SPECS.items()
              if spec["source"] == "eastmoney"}
    rows = []
    for p in range(1, pages + 1):
        params = {"type": "RPTA_WEB_TREASURYYIELD", "sty": "ALL", "st": "SOLAR_DATE", "sr": "-1",
                  "token": _MACRO_TOKEN, "p": str(p), "ps": "500"}
        url = _MACRO_API + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = (json.load(resp).get("result") or {}).get("data") or []
        except Exception as exc:  # noqa: BLE001
            log(f"  [warn] 第 {p} 页取数失败: {type(exc).__name__}: {exc}")
            break
        if not data:
            break
        rows.extend(data)
    out = {}
    for r in rows:
        try:
            d = datetime.date.fromisoformat(str(r.get("SOLAR_DATE", ""))[:10])
        except ValueError:
            continue
        vals = {name: _num(r.get(field)) for name, field in wanted.items()}
        if any(v is not None for v in vals.values()):
            out[d] = vals
    return sorted(out.items())


def latest_anchors(history):
    """每个锚定字段各自的"最新有值"值（中美休市日不同步，不能共用最后一行）。"""
    anchors = {}
    for name in AC.ANCHOR_SPECS:
        if AC.ANCHOR_SPECS[name]["source"] != "eastmoney":
            continue
        val = next((row.get(name) for _, row in reversed(history) if row.get(name) is not None), None)
        if val is not None:
            anchors[name] = val
    return anchors


def yield_series_for_market(history, market):
    """把历史摊平成 [(date, 10 年期收益率)]，供回归当自变量 x 用（x 固定为 10 年期）。"""
    key = f"{market}_10y"
    return [(d, row[key]) for d, row in history if row.get(key) is not None]


def _num(v):
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 回归

def _ols(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0:
        return None
    a = sxy / sxx
    b = my - a * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"a": a, "b": b, "r2": r2, "n": n}


def forward_5y(prices, day, years=NOMINAL_YEARS):
    """从 day 起持有 `years` 的年化收益率（价格序列为 [(date, close)]，升序）。"""
    target = day + datetime.timedelta(days=int(years * 365.25))
    if target > prices[-1][0]:
        return None
    start = _closest(prices, day)
    end = _closest(prices, target)
    if start is None or end is None or start[1] <= 0 or end[0] <= start[0]:
        return None
    return (end[1] / start[1]) ** (1.0 / years) - 1.0


def _closest(prices, day, tol_days=15):
    lo, hi = 0, len(prices) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        d = prices[mid][0]
        if d == day:
            return prices[mid]
        if d < day:
            lo = mid + 1
        else:
            hi = mid - 1
        if best is None or abs((d - day).days) < abs((best[0] - day).days):
            best = prices[mid]
    if best is None or abs((best[0] - day).days) > tol_days:
        return None
    return best


def fit_asset(prices, yields, market=None, step_days=30, since=None):
    """prices: 资产全收益序列 [(date, price)]；yields: [(date, 10 年期收益率)]。

    回归自变量固定为**同币种 10 年期国债收益率**（见 asset_classes.BOND_PRIOR_FORMULA 的说明）。
    """
    ymap = dict(yields)
    ydays = sorted(ymap)
    obs = []
    day = prices[0][0]
    if since is not None:
        day = max(day, datetime.date(since, 1, 1))
    last = prices[-1][0]
    while day <= last:
        r = forward_5y(prices, day)
        if r is not None:
            yd = min(ydays, key=lambda d: abs((d - day).days)) if ydays else None
            if yd is not None and abs((yd - day).days) <= 7:
                obs.append((ymap[yd], r, day))
        day += datetime.timedelta(days=step_days)
    if len(obs) < MIN_OBS:
        return {"error": f"有效观测点只有 {len(obs)} 个（<{MIN_OBS}）", "n": len(obs)}
    monthly = _ols([o[0] for o in obs], [o[1] for o in obs])
    # 逐年取样（近似独立）作为稳健性检查
    yearly_obs, seen = [], set()
    for x, y, d in obs:
        if d.year not in seen:
            seen.add(d.year)
            yearly_obs.append((x, y))
    yearly = _ols([o[0] for o in yearly_obs], [o[1] for o in yearly_obs])
    return {"monthly": monthly, "yearly": yearly, "n": len(obs), "n_years": len(yearly_obs),
            "x_name": "10y",
            "x_min": min(o[0] for o in obs), "x_max": max(o[0] for o in obs),
            "y_min": min(o[1] for o in obs), "y_max": max(o[1] for o in obs),
            "first": obs[0][2].isoformat(), "last": obs[-1][2].isoformat()}


# 每个动态公式标的对应的价格序列：资金/ETF 代码 + 取数方式
FORMULA_SERIES = {
    "D_HSBC_CDB":       ("fund",  "007485"),
    "O_US_TLT":         ("yahoo", "TLH"),
}
# 仅诊断（未纳入动态公式，供人工复核）
DIAGNOSTIC = [
    ("D_CN_CREDIT_BOND", "fund",  "110035", "cn"),
    ("D_CN_QDII_STABLE", "fund",  "017970", "us"),
    ("O_HYLB",           "yahoo", "HYLB",   "us"),
]


def load_prices(kind, sym):
    from market_data import eastmoney_nav, yahoo_history
    return eastmoney_nav(sym) if kind == "fund" else yahoo_history(sym, adjusted=True)


def bond_prior_inputs(history, prices, market, step_days=30, since=None):
    """返回 (fit, 动态先验, detail)。动态先验由 asset_classes 的公式算（唯一事实来源）。"""
    x = yield_series_for_market(history, market)
    fit = fit_asset(prices, x, step_days=step_days, since=since)
    return fit, x


def main():
    ap = argparse.ArgumentParser(description="债券大类先验的动态公式：回归 μ + 当期锚定收益率")
    ap.add_argument("--json", dest="out", help="把结果写到 JSON")
    ap.add_argument("--step-days", type=int, default=30, help="观测点间隔（天），默认 30")
    ap.add_argument("--since", type=int, default=None, help="只用该年之后的起点（检验 regime 敏感性）")
    args = ap.parse_args()

    print("== 取国债收益率历史（东财，实测 2004-04 起）")
    history = fetch_yield_series()
    if not history:
        print("   取数失败：无收益率数据"); return 1
    anchors = latest_anchors(history)
    print(f"   共 {len(history)} 个交易日：{history[0][0]} .. {history[-1][0]}")
    for name in AC.ANCHOR_SPECS:
        spec = AC.ANCHOR_SPECS[name]
        if spec["source"] == "eastmoney":
            val = anchors.get(name)
            print(f"   {spec['label']:<22} {('—' if val is None else format(val, '.3%'))}"
                  f"（字段 {spec['field']}）")
        else:
            v = AC.resolve_anchor(name, anchors)
            print(f"   {spec['label']:<22} {('—' if v is None else format(v, '.3%'))}")
    print()

    result = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "anchors": anchors, "formula": {}, "diagnostic": {}}

    print("== 动态公式标的（进校准）")
    print(f"{'标的':<20}{'源':<10}{'非重叠':>6}{'a':>7}{'b':>8}{'R²':>6}{'回归μ':>8}{'锚定':>8}{'先验':>8}{'静态兜底':>9}")
    fits = {}
    for key, (kind, sym) in FORMULA_SERIES.items():
        spec = AC.BOND_PRIOR_FORMULA[key]
        try:
            prices = load_prices(kind, sym)
        except Exception as exc:  # noqa: BLE001
            print(f"{key:<20}{sym:<10} 取数失败 {type(exc).__name__}: {exc}")
            continue
        fit, _ = bond_prior_inputs(history, prices, spec["market"], step_days=args.step_days, since=args.since)
        m = fit.get("monthly")
        if not m:
            print(f"{key:<20}{sym:<10} {fit.get('error', '无法拟合')}")
            continue
        fits[key] = {"a": m["a"], "b": m["b"], "r2": m["r2"], "n_obs": fit["n"],
                     "n_years": fit["n_years"], "source": sym, "window": [fit["first"], fit["last"]]}
        prior, detail = AC.bond_prior(key, anchors, fits)
        fmt = lambda v: "—" if v is None else format(v, ".2%")
        print(f"{key:<20}{sym:<10}{fit['n_years']:>6}{m['a']:>7.2f}{m['b']:>8.2%}{m['r2']:>6.2f}"
              f"{fmt(detail.get('reg_mu')):>8}{fmt(detail.get('anchor_value')):>8}{fmt(prior):>8}"
              f"{fmt(spec['fallback']):>9}")
        result["formula"][key] = {"series": sym, "market": spec["market"], "fit": fits[key],
                                  "detail": detail, "prior": prior}

    print("\n== 仅诊断（回归能否用；未纳入动态公式）")
    print(f"{'标的':<20}{'市场':<6}{'非重叠':>6}{'a':>7}{'R²':>6}  判断")
    for key, kind, sym, market in DIAGNOSTIC:
        try:
            prices = load_prices(kind, sym)
        except Exception as exc:  # noqa: BLE001
            print(f"{key:<20} 取数失败 {type(exc).__name__}: {exc}")
            continue
        fit, _ = bond_prior_inputs(history, prices, market, step_days=args.step_days, since=args.since)
        m = fit.get("monthly")
        if not m:
            print(f"{key:<20}{market.upper():<6}{'-':>6}{'-':>7}{'-':>6}  {fit.get('error', '无法拟合')}")
            continue
        note = []
        if m["r2"] < 0.15:
            note.append(f"10 年期收益率几乎无解释力（R²={m['r2']:.2f}）→ 不适合公式化")
        if fit["n_years"] < 4:
            note.append(f"非重叠窗口仅 {fit['n_years']} 个 → 斜率不可靠")
        if m["a"] < 0:
            note.append("斜率为负 → 与利率直觉相反，需换自变量（信用利差/收益率）")
        print(f"{key:<20}{market.upper():<6}{fit['n_years']:>6}{m['a']:>7.2f}{m['r2']:>6.2f}  {'；'.join(note) or 'OK'}")
        result["diagnostic"][key] = {"series": sym, "market": market, "fit": fit, "notes": note}

    print("\n公式：先验 =（回归 μ + 当期锚定收益率）/ 2，回归 μ = a×当期 10 年期 + b（见 asset_classes.BOND_PRIOR_FORMULA）")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"已写出 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
