#!/usr/bin/env python3
"""Two-stage portfolio optimization."""

import argparse
import json
import sys
from datetime import datetime, timezone

import numpy as np

try:
    from scipy.optimize import minimize
except ImportError:
    print("scipy is required.", file=sys.stderr)
    sys.exit(2)

from params import (
    BROAD_ASSETS,
    BROAD_CORR,
    CALIBRATION_WARNINGS,
    CALIBRATION_PROVENANCE,
    describe_param_source,
    param_origin,
    RF,
    TARGET_RETURN,
    US_INTERNAL,
    US_INTERNAL_CORR,
    JP_INTERNAL,
    JP_INTERNAL_CORR,
    GC_INTERNAL,
    GC_INTERNAL_CORR,
    build_broad_cov,
    classify_domestic_fund,
    build_open_pool_from_status,
    us_core_params,
)
from optimize_common import (
    portfolio_stats,
    feasible_start,
    solve_min_var,
    solve_max_sharpe,
    max_feasible_return,
    stage1_bounds,
    project_psd,
    build_corr_matrix,
)
from fund_pipeline import (
    ensure_pipeline,
    ensure_work_dir,
    filter_available_assets,
)
from test_tickers import parse_test_tickers, resolve_test_assets
import asset_classes as AC



def _upper_array(bounds):
    """把 scipy 的 bounds 列表转成上界数组（None = 无上界）。"""
    return np.array([np.inf if b[1] is None else float(b[1]) for b in bounds])


# 「个股/主动」类单标的组合权重上限（asset_classes.ACTIVE_CAP = 10%）触发记录，
# 最后合并成一条报警，避免每次跑都刷屏。
CAP_HITS = []


def _note_cap_hit(key, weight, cap):
    CAP_HITS.append((key, float(weight), float(cap)))


def _active_cap_map(assets):
    """key → 组合权重上限。仅对 class == active 的标的生效。

    必须覆盖**所有**标的：大类槽位（如 O_US_ENERGY=XOM）、美股/日本/香港/大中华内部标的
    （AAPL、NVDA…）以及 test_class=active 的测试标的 —— 漏掉内部标的会让上限形同虚设
    （2026-10-13 实测：曾出现单标的被拿到超过 10% 上限、却没人拦截的情况）。
    """
    caps = {}

    def consider(key, cls=None):
        if (cls or AC.asset_class(key)) == "active":
            caps[key] = AC.ACTIVE_CAP

    for a in assets or []:
        consider(a.get("key", ""), a.get("test_class"))
    for internal in (US_INTERNAL, JP_INTERNAL, GC_INTERNAL):
        for key in internal:
            consider(key)
    return caps


def apply_active_caps(upper, assets, cap_map, dom_idx, dom_w, warnings):
    """把 active 类单标的上限写进 stage-1 上界；与池权重约束冲突时放宽到可行并报警。

    可行性条件：每个池的 upper 之和必须 ≥ 该池的目标权重，否则优化必然不可行
    （典型情形：境内池只有一只可用标的，却要求它 ≤ 10%，而境内目标权重 30%）。
    """
    capped = []
    for i, a in enumerate(assets):
        cap = cap_map.get(a.get("key"))
        if cap is not None and upper[i] > cap:
            upper[i] = cap
            capped.append(a["key"])
    if not capped:
        return
    pool_idx = [i for i, a in enumerate(assets) if a.get("pool") == "domestic"]
    if pool_idx:
        capsum = float(sum(upper[i] for i in pool_idx))
        if capsum < dom_w - 1e-9 and capped:
            # 只放宽"被上限压住"的境内成员，补足缺口
            relaxed = [i for i in pool_idx if assets[i].get("key") in cap_map]
            need = dom_w - capsum
            if relaxed:
                add = need / len(relaxed)
                for i in relaxed:
                    upper[i] = min(1.0, upper[i] + add)
                warnings.append(
                    f"active 类单标的上限 {AC.ACTIVE_CAP:.0%} 会与境内池权重 {dom_w:.2%} 冲突"
                    f"（境内可用标的的合计上界不足），已相应放宽 {len(relaxed)} 个境内标的的上限以保证可行。")


def log_step(step, message, level="info", log_path=None):
    """Append a JSONL step record for the Swift sidecar / AI to read."""
    if not log_path:
        return
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "step": step,
             "message": message, "level": level}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# portfolio_stats is imported from optimize_common; it accepts rf as a keyword arg.
# Callers in this module pass rf=RF (module-level, dynamically overridden).


# 基金申赎管线 (实时抓汇丰 + 天天基金校验 + 开放池构建) 统一在 fund_pipeline.py;
# filter_available_assets 也从那里导入 (与 sensitivity_analysis.py 共用同一口径).


def stage2_covariance(fixed_assets, internal_tickers, broad_cov, broad_keys, broad_vols):
    """Build covariance for fixed assets + internal US holdings."""
    keys = [a["key"] for a in fixed_assets] + internal_tickers
    n = len(keys)
    vols = np.zeros(n)
    mus = np.zeros(n)
    corr = np.eye(n)

    for i, a in enumerate(fixed_assets):
        vols[i] = a["vol"]
        mus[i] = a["mu"]

    for j, ticker in enumerate(internal_tickers):
        idx = len(fixed_assets) + j
        vols[idx] = US_INTERNAL[ticker]["vol"]
        mus[idx] = US_INTERNAL[ticker]["mu"]

    broad_corr = broad_cov / np.outer(broad_vols, broad_vols)
    for i, a in enumerate(fixed_assets):
        if a["key"] == "O_US_CORE":
            continue
        ki = a["key"]
        ii = broad_keys.index(ki)
        for j, b in enumerate(fixed_assets):
            if b["key"] == "O_US_CORE" or j <= i:
                continue
            kj = b["key"]
            jj = broad_keys.index(kj)
            corr[i, j] = corr[j, i] = broad_corr[ii, jj]

    # Fixed vs internal.
    core_col = broad_keys.index("O_US_CORE")
    for i, a in enumerate(fixed_assets):
        if a["key"] == "O_US_CORE":
            continue
        base = broad_corr[broad_keys.index(a["key"]), core_col]
        for j, ticker in enumerate(internal_tickers):
            idx = len(fixed_assets) + j
            if a["key"] == "D_HSBC_SP500":
                base_ij = {"SPLG": 0.95, "VTV": 0.85, "SPMO": 0.85, "UNH": 0.75, "GOOG": 0.75, "BUFFER": 0.60}.get(ticker, 0.75)
            else:
                base_ij = base
            corr[i, idx] = corr[idx, i] = base_ij

    # Internal-internal.
    for j1, t1 in enumerate(internal_tickers):
        for j2, t2 in enumerate(internal_tickers):
            if j1 == j2:
                continue
            i1 = len(fixed_assets) + j1
            i2 = len(fixed_assets) + j2
            corr[i1, i2] = corr[i2, i1] = US_INTERNAL_CORR.get((t1, t2), US_INTERNAL_CORR.get((t2, t1), 0.5))

    # PSD projection (shared helper).
    corr = project_psd(corr)
    cov = np.outer(vols, vols) * corr
    cov += np.eye(n) * 1e-10
    return mus, cov, keys


def stage2_optimize(stage1_weights, extract, total_assets, assets, broad_cov, broad_keys, broad_vols,
                    cap_map=None):
    """Optimize internal US equity weights with total US core weight fixed and no per-ticker caps."""
    # Always consider the full US internal candidate pool, even for tickers the
    # Numbers file does not currently hold. Keeping file tickers first preserves
    # current-holding priority in tie cases, but the optimizer may add back
    # UNH/GOOG/VTV etc. if they improve the risk-adjusted result.
    file_tickers = [h["ticker"] for h in extract["us_equity"]["holdings"] if h["ticker"] in US_INTERNAL]
    internal_tickers = []
    for ticker in file_tickers + list(US_INTERNAL.keys()):
        if ticker not in internal_tickers:
            internal_tickers.append(ticker)
    if not internal_tickers:
        return None, "No supported US internal holdings found."

    core_idx = broad_keys.index("O_US_CORE")
    core_total = float(stage1_weights[core_idx])
    if core_total <= 1e-6:
        return None, "Stage 1 gave zero weight to US core equity."

    fixed_assets = [a for a in assets if a["key"] != "O_US_CORE"]
    fixed_weights = [
        float(stage1_weights[i])
        for i, a in enumerate(assets)
        if a["key"] != "O_US_CORE"
    ]

    mus, cov, keys = stage2_covariance(fixed_assets, internal_tickers, broad_cov, broad_keys, broad_vols)
    n_internal = len(internal_tickers)
    fixed_n = len(fixed_assets)
    internal_slice = slice(fixed_n, fixed_n + n_internal)

    def total_weights(internal_w):
        w = np.zeros(fixed_n + n_internal)
        w[:fixed_n] = fixed_weights
        w[internal_slice] = internal_w
        return w

    def objective(internal_w):
        w = total_weights(internal_w)
        _, _, sharpe = portfolio_stats(w, mus, cov, rf=RF)
        return -sharpe

    bounds = [(0.0, None)] * n_internal
    # active（个股/主动）类单标的组合权重上限；US 内部权重就是组合绝对权重，直接套用
    if cap_map:
        for j, t in enumerate(internal_tickers):
            cap = cap_map.get(t)
            if cap is not None:
                bounds[j] = (0.0, cap)

    cons = [
        {"type": "eq", "fun": lambda x: x.sum() - core_total},
        {"type": "ineq", "fun": lambda x: mus @ total_weights(x) - TARGET_RETURN},
    ]
    rng = np.random.default_rng(19)
    best = None
    for _ in range(80):
        caps = np.array([core_total] * n_internal)
        start = np.zeros(n_internal)
        remaining = core_total
        guard = 0
        while remaining > 1e-8 and guard < 200:
            available = np.maximum(caps - start, 0.0)
            p = rng.random(n_internal) * available
            if p.sum() <= 0:
                break
            add = min(remaining, float(p.sum()))
            start += add * p / p.sum()
            start = np.minimum(start, caps)
            remaining = core_total - start.sum()
            guard += 1
        if abs(start.sum() - core_total) > 1e-6:
            continue
        res = minimize(objective, start, method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 2000, "ftol": 1e-12})
        # SLSQP 未收敛时会返回"当前迭代点"，可能违反 bounds（实测超过单标的上限）——
        # 不要求 res.success（未收敛但可行的点仍可用），但必须显式校验 bounds（2026-10-13 修）。
        x = res.x
        w = total_weights(x)
        if abs(x.sum() - core_total) > 1e-4:
            continue
        if np.any(x < -1e-6) or np.any(x > _upper_array(bounds) + 1e-6):
            continue
        if mus @ w < TARGET_RETURN - 1e-4:
            continue
        ret, vol, sharpe = portfolio_stats(w, mus, cov, rf=RF)
        if best is None or sharpe > best[3]:
            best = (x, ret, vol, sharpe, w, internal_tickers)
    if best is not None and cap_map:
        for j, t in enumerate(best[5]):
            cap = cap_map.get(t)
            if cap is not None and best[0][j] >= cap - 1e-6:
                _note_cap_hit(t, best[0][j], cap)
    return best, None


def select_domestic_funds(stage1_weights, assets, hsbc_pool, total_assets):
    """For each allocated domestic broad category, pick the representative open fund.

    This is a simple deterministic selection from the 基金搜索易 open-fund pool:
    prefer the category's original anchor fund if it is in the pool, otherwise use
    the first open fund in that category. All open funds remain available as
    alternatives; a full per-fund internal optimizer can be added later.
    """
    selection = {}
    for i, a in enumerate(assets):
        if a["pool"] != "domestic":
            continue
        key = a["key"]
        weight = float(stage1_weights[i])
        if weight <= 1e-6:
            continue
        candidates = hsbc_pool.get(key, [])
        if not candidates:
            continue
        anchor = a.get("fund_code")
        chosen = None
        if anchor:
            for c in candidates:
                if c["code"] == anchor:
                    chosen = c
                    break
        if chosen is None:
            chosen = candidates[0]
        selection[key] = {
            "category_weight": weight,
            "amount": weight * total_assets,
            "selected": chosen,
            "candidate_count": len(candidates),
            "candidates": candidates,
        }
    return selection


def _internal_pool_optimize(stage1_weights, assets, internal_pool, internal_corr, broad_key, total_assets,
                            seed=31, cap_map=None):
    """Generic second-level optimization inside a broad equity bucket."""
    tickers = list(internal_pool.keys())
    if not tickers:
        return None
    if broad_key not in [a["key"] for a in assets]:
        return None
    idx = [i for i, a in enumerate(assets) if a["key"] == broad_key][0]
    total_w = float(stage1_weights[idx])
    if total_w <= 1e-6:
        return None

    n = len(tickers)
    mu = np.array([internal_pool[t]["mu"] for t in tickers])
    vols = np.array([internal_pool[t]["vol"] for t in tickers])
    corr = build_corr_matrix(n, tickers, internal_corr)
    corr = project_psd(corr)
    cov = np.outer(vols, vols) * corr

    def obj(w):
        ret = float(w @ mu)
        vol = float(np.sqrt(w @ cov @ w))
        return -((ret - RF) / vol if vol > 0 else 0.0)

    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    bounds = [(0.0, 1.0)] * n
    # 桶内权重是"归一化后 × 桶权重"，所以组合上限要折算：cap_norm = cap / total_w
    cap_norm = {}
    if cap_map and total_w > 1e-9:
        for t in tickers:
            cap = cap_map.get(t)
            if cap is not None:
                cap_norm[t] = min(1.0, cap / total_w)
        for i, t in enumerate(tickers):
            if t in cap_norm:
                bounds[i] = (0.0, cap_norm[t])
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(60):
        start = rng.random(n)
        start /= start.sum()
        res = minimize(obj, start, method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 1500, "ftol": 1e-12})
        x = res.x
        if abs(x.sum() - 1.0) > 1e-4:
            continue
        if np.any(x < -1e-6) or np.any(x > _upper_array(bounds) + 1e-6):
            continue
        ret = float(x @ mu)
        vol = float(np.sqrt(x @ cov @ x))
        sharpe = (ret - RF) / vol if vol > 0 else 0.0
        if best is None or sharpe > best[0]:
            best = (sharpe, x, ret, vol)
    if best is None:
        return None
    sharpe, x, ret, vol = best
    if cap_norm:
        for i, t in enumerate(tickers):
            if t in cap_norm and x[i] >= cap_norm[t] - 1e-6:
                _note_cap_hit(t, x[i] * total_w, cap_map[t])
    return {
        "broad_total_weight": total_w,
        "internal_weights_total": {t: float(x[i]) * total_w for i, t in enumerate(tickers)},
        "internal_weights_normalized": {t: float(x[i]) for i, t in enumerate(tickers)},
        "amounts": {t: float(x[i]) * total_w * total_assets for i, t in enumerate(tickers)},
        "ret": ret,
        "vol": vol,
        "sharpe": sharpe,
    }


def stage2_japan_optimize(stage1_weights, assets, total_assets, cap_map=None):
    """Optimize Japan equity internal factor weights."""
    return _internal_pool_optimize(stage1_weights, assets, JP_INTERNAL, JP_INTERNAL_CORR, "O_JP_EQ",
                                   total_assets, seed=31, cap_map=cap_map)



def stage2_gc_optimize(stage1_weights, assets, total_assets, cap_map=None):
    """Optimize Greater China equity internal (沪深300/红利低波/主动/混合/指数/港股宽基/高股息/科技)."""
    return _internal_pool_optimize(stage1_weights, assets, GC_INTERNAL, GC_INTERNAL_CORR, "D_GREATER_CN",
                                   total_assets, seed=43, cap_map=cap_map)


def build_result_summary(output, hsbc_pool=None):
    """Build a clean Finance result file: per-asset weights/return/vol/sharpe + totals."""
    # 境外/跨池大类的执行代码（标的名前加 [代码] 前缀，便于直接下单；
    # 无单一成交代码的大类保持 None，不加前缀）
    exec_code = {
        "O_BTC": "BTC",
        "O_HYLB": "HYLB",
        "O_SG_EQ": "G3B.SI",
        "O_US_TLT": "TLH",
        "O_US_REIT": "REZ",
        "O_US_ENERGY": "XOM",
    }
    # 大中华二级风格桶 → 境内基金池类别（风格桶本身无单一成交代码，
    # 执行代表基金取自对应类别的去重/最低费率代表，与境内大类同一套哲学）
    gc_bucket_pool = {
        "CSI300": "D_HSBC_CSI300",
        "DIVLOWVOL": "D_HSBC_DIVLOWVOL",
        "DIVLOWVOL100": "D_CN_DIVLOWVOL100",
        "STOCK": "D_CN_STOCK",
        "MIXED": "D_CN_MIXED",
        "INDEX": "D_CN_INDEX",
        "HK_BROAD": "D_CN_HK",
        "HK_DIV": "D_CN_HK",
        "HK_TECH": "D_HSBC_HSTECH",
    }
    assets = []
    # Domestic categories with selected representative funds
    for key, sel in output.get("domestic_selection", {}).items():
        a = next((x for x in BROAD_ASSETS if x["key"] == key), None)
        if a is None:
            continue
        mu = a["mu"]
        vol = a["vol"]
        assets.append({
            "key": key,
            "name": f"[{sel['selected']['code']}] {a['name']} → {sel['selected']['name']}",
            "weight": sel["category_weight"],
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - RF) / vol if vol else 0.0,
        })
    # US core internal split
    stage2 = output.get("stage2") or {}
    for ticker, w in stage2.get("internal_weights_total", {}).items():
        if w <= 1e-8:
            continue
        info = US_INTERNAL.get(ticker)
        if info is None:
            continue
        mu = info["mu"]
        vol = info["vol"]
        assets.append({
            "key": ticker,
            "name": f"[{ticker}] {info['name']}",
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - RF) / vol if vol else 0.0,
        })
    # Japan equity internal factor split
    jp = output.get("japan_selection")
    if jp is not None:
        for ticker, w in jp.get("internal_weights_total", {}).items():
            if w <= 1e-8:
                continue
            info = JP_INTERNAL.get(ticker)
            if info is None:
                continue
            mu = info["mu"]
            vol = info["vol"]
            assets.append({
                "key": f"JP:{ticker}",
                "name": f"[{ticker}] 日本·{info['name']}",
                "weight": w,
                "expected_return": mu,
                "volatility": vol,
                "sharpe": (mu - RF) / vol if vol else 0.0,
            })

    # Greater China equity internal factor split
    # 风格桶无单一成交代码：前缀用对应境内基金池的代表基金代码
    gc = output.get("gc_selection")
    if gc is not None:
        gc_used = {}  # pool_key -> 已用 code 列表，同池多桶避免重复同一代表
        for ticker, w in gc.get("internal_weights_total", {}).items():
            if w <= 1e-8:
                continue
            info = GC_INTERNAL.get(ticker)
            if info is None:
                continue
            mu = info["mu"]
            vol = info["vol"]
            rep = None
            if hsbc_pool:
                pool_key = gc_bucket_pool.get(ticker)
                cands = hsbc_pool.get(pool_key) or []
                used = gc_used.setdefault(pool_key, [])
                rep = next((c for c in cands if c["code"] not in used), None)
                if rep is not None:
                    used.append(rep["code"])
            name = (f"[{rep['code']}] 大中华·{info['name']} → {rep['name']}"
                    if rep is not None else f"大中华·{info['name']}")
            assets.append({
                "key": f"GC:{ticker}",
                "name": name,
                "weight": w,
                "expected_return": mu,
                "volatility": vol,
                "sharpe": (mu - RF) / vol if vol else 0.0,
            })

    # Other overseas broad assets
    for key, w in output.get("stage1", {}).get("weights", {}).items():
        if key in [a["key"] for a in assets] or key in ("O_US_CORE", "O_JP_EQ", "O_HK_HSTECH", "D_GREATER_CN"):
            continue
        a = next((x for x in BROAD_ASSETS if x["key"] == key), None)
        if a is None:
            continue
        mu = a["mu"]
        vol = a["vol"]
        code = a.get("fund_code") or exec_code.get(key)
        name = f"[{code}] {a['name']}" if code else a["name"]
        assets.append({
            "key": key,
            "name": name,
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - RF) / vol if vol else 0.0,
        })
    # 测试标的 (T_ 前缀): 它们是"新标的测试"临时注入的大类, 不在 BROAD_ASSETS 里,
    # 但同样占用第一阶段权重。以前这里直接 continue 掉, 后果是:
    # (a) 权重合计 < 1(看起来"没买满"), (b) 用户看到的清单里永远没有新标的,
    # 于是"加入新标的值不值得"这个问题的答案被整段吞掉 (2026-10-13 修)。
    test_meta = {t["key"]: t for t in (output.get("test") or {}).get("assets", [])}
    listed = {a["key"] for a in assets}
    for key, w in output.get("stage1", {}).get("weights", {}).items():
        if key in listed:
            continue
        m = test_meta.get(key)
        if m is None:
            continue
        mu = float(m["mu"])
        vol = float(m["vol"])
        src = "天天基金" if m.get("source") == "eastmoney" else "Yahoo"
        pool = "境内池" if m.get("pool") == "domestic" else "境外池"
        assets.append({
            "key": key,
            "name": f"[测试] {m['ticker']}（{src} · {pool} · what-if，不进执行清单）",
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - RF) / vol if vol else 0.0,
            "is_test": True,
        })
    assets.sort(key=lambda x: -x["weight"])
    for a in assets:
        a["weight"] = round(float(a["weight"]), 6)
        a["expected_return"] = round(float(a["expected_return"]), 6)
        a["volatility"] = round(float(a["volatility"]), 6)
        a["sharpe"] = round(float(a["sharpe"]), 6)
    final = output.get("final") or {}
    # 自检: 报告清单必须覆盖第一阶段所有权重。历史上测试标的(T_)被静默丢弃过一次,
    # 于是"清单合计 82%、指标却按 100% 算"这种错能一路滑到界面上 —— 这里把它变成显式提示。
    # 注意有些 key 是**故意**不出现在清单里的: 它们被拆成了第二阶段的子行
    # (O_US_CORE→美股内部标的、O_JP_EQ/O_HK_HSTECH/D_GREATER_CN→各内部桶、境内大类→选中基金)。
    expanded = set(output.get("domestic_selection", {}).keys())
    for sel_key, broad_key in (("stage2", "O_US_CORE"), ("japan_selection", "O_JP_EQ"),
                               ("gc_selection", "D_GREATER_CN")):
        if output.get(sel_key) is not None:
            expanded.add(broad_key)
    emitted = {a["key"] for a in assets}
    missing = [k for k, w in output.get("stage1", {}).get("weights", {}).items()
               if w > 1e-6 and k not in emitted and k not in expanded]
    total_w = sum(float(a["weight"]) for a in assets)
    # 优化器自身的提示(候选被剔除、池口径、数据源计数…)必须原样带上:
    # 它们解释了"为什么这次的可投大类和上次不一样", 是解读结果的前提。
    summary_warnings = list(output.get("warnings", []))
    if missing:
        summary_warnings.append(
            "自检: 以下权重没有出现在结果清单里（映射漏项）: " + ", ".join(sorted(missing)))
    if abs(total_w - 1.0) > 1e-4:
        summary_warnings.append(
            f"自检: 结果清单权重合计为 {total_w:.4f}，不等于 1 —— 请对照第一阶段权重核对映射是否漏项。")
    summary = {
        "generated_at": output.get("availability", {}).get("checked_at"),
        "portfolio": {
            "expected_return": round(float(final.get("ret", 0.0)), 6),
            "volatility": round(float(final.get("vol", 0.0)), 6),
            "sharpe": round(float(final.get("sharpe", 0.0)), 6),
            "worst_year_95": round(float(final.get("floor", 0.0)), 6),
        },
        "assets": assets,
        "benchmark": _build_benchmark(output, final),
        "test_assets": (output.get("test") or {}).get("assets"),
        "target_return": output.get("target_return"),
        "target_gap": output.get("target_gap"),
        "objective": output.get("objective"),
        "warnings": summary_warnings,
        "source_detail": None,
    }
    return summary


def _build_benchmark(output, final):
    """Benchmark = 境内池 100% 沪深300ETF + 海外池 100% 标普500ETF."""
    try:
        pool = output.get("pool", {})
        dw = float(pool.get("domestic_weight", 0.0))
        ow = float(pool.get("overseas_weight", 0.0))
        core = output.get("us_core_aggregate", {})
        broad = []
        for a in BROAD_ASSETS:
            item = dict(a)
            if a["key"] == "O_US_CORE":
                item["mu"] = core.get("mu", a.get("mu"))
                item["vol"] = core.get("vol", a.get("vol"))
            broad.append(item)
        # 测试标的也要进这张协方差表: 组合 Beta 用的是"组合权重 × 基准权重"的协方差,
        # 漏掉测试标的等于把它们当空仓, Beta 被低估、Alpha 被高估 (2026-10-13 修)。
        test_block = output.get("test") or {}
        test_meta = {t["key"]: t for t in test_block.get("assets", [])}
        for key, m in test_meta.items():
            broad.append({"key": key, "name": key, "mu": m["mu"], "vol": m["vol"],
                          "pool": m.get("pool")})
        keys = [a["key"] for a in broad]
        mu = np.array([a["mu"] if a["mu"] is not None else 0.0 for a in broad])
        vols = np.array([a["vol"] if a["vol"] is not None else 0.0 for a in broad])
        corr_pairs = dict(BROAD_CORR)
        for i, j, v in test_block.get("corr", []) or []:
            corr_pairs[(i, j)] = float(v)
        cov = build_broad_cov([{"key": k, "vol": v} for k, v in zip(keys, vols)], corr_pairs)

        w_bench = np.zeros(len(keys))
        if "D_GREATER_CN" in keys:
            w_bench[keys.index("D_GREATER_CN")] = dw
        elif "D_HSBC_CSI300" in keys:
            w_bench[keys.index("D_HSBC_CSI300")] = dw
        if "D_HSBC_SP500" in keys:
            w_bench[keys.index("D_HSBC_SP500")] = ow

        bench_ret = float(w_bench @ mu)
        bench_vol = float(np.sqrt(w_bench @ cov @ w_bench))

        # Portfolio broad weights (stage1) for beta approximation.
        stage1 = output.get("stage1", {}).get("weights", {})
        w_p = np.zeros(len(keys))
        for i, k in enumerate(keys):
            w_p[i] = float(stage1.get(k, 0.0))
        cov_pb = float(w_p @ cov @ w_bench)
        var_b = float(w_bench @ cov @ w_bench)
        beta = cov_pb / var_b if var_b > 0 else 0.0

        ret_p = float(final.get("ret", 0.0))
        alpha = ret_p - (RF + beta * (bench_ret - RF))

        return {
            "weights": {"domestic_CSI300": dw, "overseas_SP500": ow},
            "expected_return": round(bench_ret, 6),
            "volatility": round(bench_vol, 6),
            "sharpe": round((bench_ret - RF) / bench_vol, 6) if bench_vol > 0 else 0.0,
            "worst_year_95": round(bench_ret - 1.96 * bench_vol, 6),
            "portfolio_alpha": round(alpha, 6),
            "portfolio_beta": round(beta, 6),
            "volatility_reduction": round(bench_vol - float(final.get("vol", 0.0)), 6),
            "worst_year_improvement": round(float(final.get("floor", 0.0)) - (bench_ret - 1.96 * bench_vol), 6),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("extract_json")
    parser.add_argument("--total-assets", type=float, default=None)
    parser.add_argument(
        "--target-return",
        type=float,
        default=None,
        help="目标预期收益率覆盖 (如 0.20=20%%)；缺省用 params.TARGET_RETURN",
    )
    parser.add_argument(
        "--test-tickers",
        dest="test_tickers",
        default=None,
        help="逗号分隔的临时测试标的 (如 AAPL,0700.HK,510300)；"
             "按 --test-class 指定的类别估算 μ/σ 与相关性，作为新增资产参与优化 (T_ 前缀)；"
             "6 位纯数字按境内池处理。",
    )
    parser.add_argument(
        "--test-class",
        dest="test_class",
        default="active",
        choices=["broad", "strategy", "active"],
        # argparse 的 help 走 %-格式化，说明文字里的 % 必须转义成 %%
        help=AC.CLASS_PARAM_HELP.replace("%", "%%"),
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="联网管线临时文件目录 (hsbc_funds.json / availability.json)；缺省用系统临时目录共享目录",
    )
    parser.add_argument("--check-timeout", type=float, default=300.0,
                        help="天天基金状态校验子进程总超时秒数")
    parser.add_argument("--hsbc-timeout", type=float, default=60.0,
                        help="基金搜索易抓取单请求超时秒数")
    parser.add_argument("--request-timeout", type=float, default=12.0,
                        help="天天基金单只请求超时秒数")
    parser.add_argument("--json", dest="out_path", help="Write detailed JSON output to file")
    parser.add_argument("--result-json", dest="result_path", help="Write clean summary JSON to file")
    parser.add_argument("--log", dest="log_path", help="JSONL step log path")
    args = parser.parse_args()

    # App 侧滑块传入的目标收益率覆盖 params.py 的默认值.
    # TARGET_RETURN 是模块级全局, 下方 stage1/stage2 的约束 lambda 在调用时
    # 按全局名查找, 此处重绑后全部生效.
    global TARGET_RETURN
    if args.target_return is not None:
        TARGET_RETURN = float(args.target_return)
        log_step("target_return", f"目标收益率覆盖为 {TARGET_RETURN:.0%}", log_path=args.log_path)

    with open(args.extract_json, encoding="utf-8") as f:
        extract = json.load(f)
    log_step("load_extract", f"已加载提取结果 total={extract.get('total_value')}", log_path=args.log_path)

    # RF (无风险利率) 动态覆盖: App 侧按池比例加权中美10年国债收益率算出 rf,
    # 通过 extract_live.json 的 "rf" 字段传入, 替代 params.py 写死的 0.025.
    # RF 是模块级全局, 同 TARGET_RETURN 模式: global 重绑后约束 lambda 生效.
    global RF
    if extract.get("rf") is not None:
        RF = float(extract["rf"])
        log_step("rf", f"无风险利率覆盖为 {RF:.2%} (动态中美10年国债加权)", log_path=args.log_path)

    warnings = list(extract.get("warnings", []))
    CAP_HITS.clear()
    # 参数口径的提示（缺行情用人工兜底、σ 毛刺丢弃、相关性兜底…）一并带给结果页
    warnings.extend(CALIBRATION_WARNINGS)

    # live_anchors: 用户实际持有的境内基金代码 (从 extract_live.json domestic_holdings 提取),
    # 用 classify_domestic_fund(name) 归类, 覆盖 params.py 写死的 fund_code 作为各类锚定.
    # 效果: select_domestic_funds 优先保留用户已持有基金而非推荐换仓到同类最低费率基金.
    live_anchors = {}
    for h in extract.get("domestic_holdings", []):
        cat = classify_domestic_fund(h.get("name", ""))
        live_anchors[cat] = h.get("code")
    if live_anchors:
        log_step("live_anchors", f"动态锚定 {len(live_anchors)} 个境内基金 (覆盖 params.py 静态锚定)", log_path=args.log_path)

    # ---- 联网管线: 每次优化完整执行 (抓汇丰搜索易 → 天天基金实时校验 → 开放池构建) ----
    # 产物写入 work_dir 临时文件 (hsbc_funds.json / availability.json / check_plan.json),
    # 敏感性分析复用同一目录, 不重新联网; 优化器关闭时由 Swift 侧整体删除。
    work_dir = ensure_work_dir(args.work_dir)
    try:
        hsbc_data, availability = ensure_pipeline(
            work_dir,
            live_anchors=live_anchors,
            hsbc_request_timeout=args.hsbc_timeout,
            request_timeout=args.request_timeout,
            check_total_timeout=args.check_timeout,
            log=lambda step, message, level="info": log_step(
                step, message, level=level, log_path=args.log_path),
        )
    except RuntimeError as exc:
        print(json.dumps(
            {"error": "联网管线失败（基金搜索易/天天基金）", "detail": str(exc)},
            ensure_ascii=False))
        sys.exit(2)
    avail_funds = availability["funds"]
    hsbc_funds_live = hsbc_data.get("funds", [])
    # 汇丰原始在售状态, 供口径冲突提示 (汇丰开放 vs 天天基金暂停)
    hsbc_status = {str(f.get("code", "")).strip(): f for f in hsbc_funds_live}

    # 直接取分组早停校验 selection 中每类选中的开放代表基金
    # (组内顺序 = 锚定优先 → 费率升序 → A/C 备选垫后; 无数据的互认基金已按汇丰口径兜底)
    hsbc_pool, pool_meta = build_open_pool_from_status(
        hsbc_funds_live, availability, live_anchors=live_anchors)
    warnings.append(
        f"基金搜索易实时抓取：在售 {hsbc_data.get('total_count', '?')} 只，"
        f"开放申购 {hsbc_data.get('open_count', '?')} 只。")
    stats = availability.get("stats", {})
    warnings.append(
        f"天天基金实时校验（分组早停）：实际请求 {stats.get('checked', len(avail_funds))} 只，"
        f"开放 {availability.get('open_count', '?')} 只，"
        f"汇丰口径兜底 {stats.get('hsbc_fallback', 0)} 只。")
    log_step("build_open_pool",
             f"开放基金池：{len(hsbc_pool)} 个类别、代表基金 {pool_meta['kept']} 只"
             f"（{stats.get('checked', '?')} 只候选实时校验, 耗时 {availability.get('elapsed_seconds', '?')}s）",
             log_path=args.log_path)

    dom_w = float(extract["domestic_weight"])
    ov_w = float(extract["overseas_weight"])

    internal_weights = {h["ticker"]: h["weight"] for h in extract["us_equity"]["holdings"]}
    core_mu, core_vol = us_core_params(internal_weights)

    assets = []
    for a in BROAD_ASSETS:
        # 境内大类剔除规则 (联网口径): 汇丰在售池无候选, 或全部候选被天天基金
        # 判为暂停申购 → 买不到, 不进第一阶段 (如 QDII 商品可能无在售基金).
        if a["pool"] == "domestic" and hsbc_pool and a["key"] not in hsbc_pool:
            cat = pool_meta["categories"].get(a["key"])
            if cat:
                warnings.append(
                    f"境内大类 {a['name']}（{a['key']}）的 {len(cat.get('tried', []))} 只候选在天天基金"
                    f"全部为暂停申购/校验失败，已从第一阶段剔除。")
            else:
                warnings.append(f"境内大类 {a['name']}（{a['key']}）在汇丰在售池中无候选基金，已从第一阶段剔除。")
            continue
        item = dict(a)
        if a["key"] == "O_US_CORE":
            item["mu"] = core_mu
            item["vol"] = core_vol
        assets.append(item)
    assets = filter_available_assets(assets, avail_funds, warnings, hsbc_status)
    log_step("build_assets", f"构建大类资产 {len(assets)} 项", log_path=args.log_path)
    if not any(a["pool"] == "domestic" for a in assets):
        print(
            json.dumps(
                {"error": "没有通过天天基金校验的境内基金", "warnings": warnings},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    # ---- 新标的测试: 临时标的作为新增资产注入第一阶段 (在可投性过滤之后, 不受其约束) ----
    test_estimates, test_skipped = [], []
    if args.test_tickers:
        test_assets, test_corr_pairs, test_skipped, test_estimates = resolve_test_assets(
            args.test_tickers,
            work_dir,
            existing_keys={a["key"] for a in assets},
            internal_keys=set(US_INTERNAL.keys()),
            # 相关性要覆盖全量大类 (不只是本次可投的那几个): 基准对比用
            # D_HSBC_SP500 / D_GREATER_CN 算组合 Beta, 缺了这些行 Beta 会被低估。
            corr_keys={a["key"] for a in BROAD_ASSETS},
            test_class=args.test_class,
            log=lambda msg, level="info": log_step(
                "test_tickers", msg, level=level, log_path=args.log_path),
        )
        for t, reason in test_skipped:
            warnings.append(f"测试标的 {t} 已跳过: {reason}")
        # 测试标的没有登记先验/下限：broad/strategy 会退化成纯历史，必须显式说明口径不同
        for e in test_estimates:
            if e.get("mu_note") == "history_only":
                warnings.append(
                    f"测试标的 {e['ticker']} 标为「{AC.CLASS_RULES[e['test_class']]['label']}」，"
                    f"但该类的人工先验只对登记在册的标的有效；测试标的使用纯历史年化 "
                    f"μ={e['mu']:.2%}（组合内同类标的的口径是"
                    f"{AC.CLASS_RULES[e['test_class']]['mu_hist_weight']:.0%} 历史 + "
                    f"{1-AC.CLASS_RULES[e['test_class']]['mu_hist_weight']:.0%} 先验，两者不可直接横比）。")
            if e.get("vol_note") == "no_floor_written":
                warnings.append(
                    f"测试标的 {e['ticker']} 按「{AC.CLASS_RULES[e['test_class']]['label']}」本应套用人工波动率下限，"
                    f"但新标的没有登记下限，本次直接用历史波动 σ={e['vol']:.2%}。")
        if test_assets:
            assets = assets + test_assets
            for a in test_assets:
                if a["pool"] == "domestic":
                    warnings.append(
                        f"测试标的 {a['name']} 按境内池约束参与优化 (what-if 口径, "
                        f"不经汇丰在售校验, 也不出现在境内基金执行清单)。")
            log_step(
                "test_tickers",
                f"新增 {len(test_assets)} 个测试资产: "
                + ", ".join(a["key"] for a in test_assets),
                log_path=args.log_path)
        else:
            detail = "; ".join(f"{t}: {r}" for t, r in test_skipped) or "无有效标的"
            print(json.dumps(
                {"error": "所有测试标的均不可用", "detail": detail, "warnings": warnings},
                ensure_ascii=False))
            sys.exit(3)

    keys = [a["key"] for a in assets]
    mu = np.array([a["mu"] for a in assets])
    vols = np.array([a["vol"] for a in assets])
    cov = build_broad_cov(
        [{"key": a["key"], "vol": a["vol"]} for a in assets],
        {**BROAD_CORR, **(test_corr_pairs if args.test_tickers else {})},
    )
    dom_idx = [i for i, a in enumerate(assets) if a["pool"] == "domestic"]
    ov_idx = [i for i, a in enumerate(assets) if a["pool"] == "overseas"]

    log_step("stage1_broad", "第一阶段：大类资产最大夏普优化（s.t. 收益 ≥ 目标）", log_path=args.log_path)
    lower, upper = stage1_bounds(assets)
    cap_map = _active_cap_map(assets)
    apply_active_caps(upper, assets, cap_map, dom_idx, dom_w, warnings)
    target = TARGET_RETURN
    # 目标函数（2026-10-13 用户定稿）：两阶段统一为 max 夏普，收益目标只作为**下限**。
    # 若目标高于切点收益，约束绷紧 → 解自然落到"有效前沿上收益=目标"的那点（与 min 方差等价），
    # 所以下面保留 min 方差作为兜底，避免 SLSQP 在约束边界上失败时拿不到解。
    solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf=RF)
    if solved is None:
        solved = solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf=RF)
    if solved is None:
        max_ret = max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w)
        warnings.append(f"{target:.0%} 目标在当前约束下不可行，已回退到最大可行收益 {max_ret:.2%}。")
        target = max_ret - 1e-6
        # Bug D1 修复: 回退后的靶值必须同步回全局 TARGET_RETURN,
        # 否则 stage2 的收益约束仍按原始(不可行)目标求解, 导致 stage2 必然返回 None.
        TARGET_RETURN = target
        solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target,
                                  seed=23, rf=RF)

    if solved is None:
        print(json.dumps({"error": "Optimization infeasible", "warnings": warnings}, ensure_ascii=False, indent=2))
        sys.exit(1)

    w1, ret1, vol1, sharpe1 = solved
    # 记录 stage-1 里被 active 上限压住的标的（稍后合并成一条报警）
    for i, a in enumerate(assets):
        cap = cap_map.get(a.get("key"))
        if cap is not None and w1[i] >= cap - 1e-6:
            _note_cap_hit(a["key"], w1[i], cap)
    log_step("stage1_result", f"第一阶段完成 收益={ret1:.2%} 波动={vol1:.2%} 夏普={sharpe1:.3f}", log_path=args.log_path)
    total_assets = args.total_assets or float(extract["total_value"])

    domestic_selection = select_domestic_funds(w1, assets, hsbc_pool, total_assets)
    log_step("select_domestic", "境内基金选择完成", log_path=args.log_path)

    japan_selection = stage2_japan_optimize(w1, assets, total_assets, cap_map=cap_map)
    gc_selection = stage2_gc_optimize(w1, assets, total_assets, cap_map=cap_map)

    log_step("stage2_internal", "第二阶段：美国权益内部标的优化", log_path=args.log_path)
    internal, stage2_error = stage2_optimize(
        w1,
        extract,
        total_assets,
        assets,
        cov,
        keys,
        vols,
        cap_map=cap_map,
    )
    final = None
    if internal is not None:
        x_internal, ret2, vol2, sharpe2, w2, internal_tickers = internal
        final = {
            "ret": ret2,
            "vol": vol2,
            "sharpe": sharpe2,
            "floor": ret2 - 1.96 * vol2,
        }
    else:
        # Bug D2 修复: stage2 不可行时汇总回退第一阶段统计,
        # 否则 final=None 会让结果文件的组合收益/波动/夏普全部变成 0.
        final = {
            "ret": ret1,
            "vol": vol1,
            "sharpe": sharpe1,
            "floor": ret1 - 1.96 * vol1,
        }
        if stage2_error:
            warnings.append(stage2_error)
        warnings.append("美国权益内部优化不可行，组合统计已回退使用第一阶段结果。")

    target_gap = None
    if target is not None and final is not None:
        target_gap = float(final.get("ret", 0.0)) - float(target)
        if target_gap > 0.002:
            warnings.append(
                f"目标收益 {target:.2%} 只是下限：目标是最大化夏普，实际取到 {final.get('ret', 0.0):.2%}"
                f"（高出 {target_gap:.2%}）。想让目标说了算，把目标设到切点收益以上，或改回 min 方差口径。")

    stage1_detail = {
        "weights": {keys[i]: float(w1[i]) for i in range(len(keys)) if w1[i] > 1e-6},
        "ret": ret1,
        "vol": vol1,
        "sharpe": sharpe1,
        "floor": ret1 - 1.96 * vol1,
    }

    output = {
        "pool": {
            "mode": extract["pool_mode"],
            "domestic_weight": dom_w,
            "overseas_weight": ov_w,
            "domestic_value": float(extract["domestic_value"]),
            "overseas_value": float(extract["overseas_value"]),
            "total_assets_used": total_assets,
        },
        "us_core_aggregate": {
            "mu": core_mu,
            "vol": core_vol,
            "internal_from_file": internal_weights,
        },
        "pipeline": {
            "work_dir": str(work_dir),
            "hsbc": {
                "fetched_at": hsbc_data.get("fetched_at"),
                "total_count": hsbc_data.get("total_count"),
                "open_count": hsbc_data.get("open_count"),
            },
            "availability": {
                "checked_at": availability.get("checked_at"),
                "checked_codes": len(avail_funds),
                "open_count": availability.get("open_count"),
                "hsbc_fallback": availability.get("stats", {}).get("hsbc_fallback"),
                "elapsed_seconds": availability.get("elapsed_seconds"),
            },
            "open_pool": {
                "categories": len(hsbc_pool),
                "representatives": pool_meta["kept"],
                "category_detail": pool_meta["categories"],
            },
        },
        "availability": {
            "source": availability.get("source"),
            "base_url": availability.get("base_url"),
            "checked_at": availability.get("checked_at"),
            "funds": avail_funds,
        },
        "stage1": stage1_detail,
        "stage2": None,
        "domestic_selection": domestic_selection,
        "japan_selection": japan_selection,
        "gc_selection": gc_selection,
        "final": final,
        "target_return": TARGET_RETURN,
        "target_gap": target_gap,
        "objective": "max_sharpe",
        "warnings": warnings,
    }

    if CAP_HITS:
        merged = {}
        for k, w, cap in CAP_HITS:
            merged[k] = max(merged.get(k, 0.0), w)
        warnings.append(
            f"active（个股/主动）类单标的上限 {AC.ACTIVE_CAP:.0%} 生效，以下标的被压到上限: "
            + "、".join(f"{k} {w:.2%}" for k, w in sorted(merged.items(), key=lambda kv: -kv[1])))

    if args.test_tickers:
        output["test"] = {
            "tickers": parse_test_tickers(args.test_tickers),
            "test_class": args.test_class,
            "class_help": AC.CLASS_PARAM_HELP,
            "assets": test_estimates,
            "skipped": [{"ticker": t, "reason": r} for t, r in test_skipped],
            # 测试标的与各大类的相关性: 基准对比要用它把测试标的并入协方差,
            # 否则 Beta/Alpha 会因为权重质量不足 1 而失真 (2026-10-13 修)。
            "corr": [[i, j, float(v)] for (i, j), v in test_corr_pairs.items()],
        }

    if internal is not None:
        x_internal, ret2, vol2, sharpe2, w2, internal_tickers = internal
        internal_total = float(x_internal.sum())
        output["stage2"] = {
            "internal_total_weight": internal_total,
            "internal_weights_total": {t: float(x_internal[i]) for i, t in enumerate(internal_tickers)},
            "internal_weights_normalized": {t: float(x_internal[i]) / internal_total for i, t in enumerate(internal_tickers)},
            "amounts": {t: float(x_internal[i]) * total_assets for i, t in enumerate(internal_tickers)},
            "ret": ret2,
            "vol": vol2,
            "sharpe": sharpe2,
            "floor": ret2 - 1.96 * vol2,
        }
        if stage2_error:
            output["warnings"].append(stage2_error)

    log_step("finalize", f"优化完成 共{len(assets)}大类 夏普={sharpe1:.3f}", log_path=args.log_path)
    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))

    if args.result_path:
        summary = build_result_summary(output, hsbc_pool=hsbc_pool)
        if args.out_path:
            summary["source_detail"] = args.out_path
        with open(args.result_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
