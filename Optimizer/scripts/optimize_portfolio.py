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
    build_open_pool_from_status,
    us_core_params,
)
from optimize_common import (
    portfolio_stats,
    solve_min_var,
    solve_max_sharpe,
    max_feasible_return,
    stage1_bounds,
    project_psd,
    build_corr_matrix,
    upper_bounds_array as _upper_array,   # 名字保留 _upper_array：test_optimizer_caps 直接引用
    _respects_bounds,
)
from fund_pipeline import (
    ensure_pipeline,
    ensure_work_dir,
    build_investable_assets,
    live_anchors_from_extract,
)
from test_tickers import parse_test_tickers, resolve_test_assets
import asset_classes as AC



# 「个股/主动」类单标的组合权重上限（asset_classes.ACTIVE_CAP = 10%）触发记录，
# 最后合并成一条报警，避免每次跑都刷屏。
CAP_HITS = []

# D_HSBC_SP500（标普500 QDII 大类）与美股内部各执行标的的相关性：手工标定。
# 其余固定大类与内部标的之间没有直接的历史序列可算，用「该大类 vs O_US_CORE」的
# 大类级相关性做代理（见 stage2_covariance）——这是近似，不是估计。
SP500_INTERNAL_CORR = {
    "SPY": 0.95, "VTV": 0.85, "SPMO": 0.85, "UNH": 0.75, "GOOG": 0.75, "BUFFER": 0.60,
}
SP500_INTERNAL_CORR_DEFAULT = 0.75


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
# 可投大类构建 (build_investable_assets) 也在那里, 与 sensitivity_analysis.py 共用同一口径.


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
                base_ij = SP500_INTERNAL_CORR.get(ticker, SP500_INTERNAL_CORR_DEFAULT)
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
                    cap_map=None, target=None, rf=None, n_starts=80):
    """Optimize internal US equity weights with total US core weight fixed and no per-ticker caps."""
    target = TARGET_RETURN if target is None else target
    rf = RF if rf is None else rf
    # Always consider the full US internal candidate pool, even for tickers the
    # extract file does not currently hold. Keeping file tickers first preserves
    # current-holding priority in tie cases, but the optimizer may add back
    # UNH/GOOG/VTV etc. if they improve the risk-adjusted result.
    file_tickers = [h["ticker"] for h in extract["us_equity"]["holdings"] if h["ticker"] in US_INTERNAL]
    internal_tickers = []
    for ticker in file_tickers + list(US_INTERNAL.keys()):
        if ticker not in internal_tickers:
            internal_tickers.append(ticker)
    if not internal_tickers:
        return None, "No supported US internal holdings found."
    if "O_US_CORE" not in broad_keys:
        # 2026-10-15 P2：过去这里由 broad_keys.index 抛裸 ValueError（无上下文）→ 整次求解崩掉。
        # 本轮大类资产里根本没有美股核心袖时阶段二无从下手：返回可诊断原因，由 _solve_once
        # 记进 warnings，让上层显式看到"美国权益内部优化不可行"，而不是崩栈。
        return None, ("阶段二无法运行：本轮大类资产里没有 O_US_CORE"
                      f"（实际大类：{'、'.join(broad_keys)}）；"
                      "美股袖没有可优化的内部篮子。")

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
        _, _, sharpe = portfolio_stats(w, mus, cov, rf=rf)
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
        {"type": "ineq", "fun": lambda x: mus @ total_weights(x) - target},
    ]
    rng = np.random.default_rng(19)
    best = None
    for _ in range(n_starts):
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
        if not _respects_bounds(x, _upper_array(bounds)):
            continue
        if mus @ w < target - 1e-4:
            continue
        ret, vol, sharpe = portfolio_stats(w, mus, cov, rf=rf)
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


# 袖内收益下限的绝对容差（P1）：SLSQP 末位噪声 + 离散选标。
SLEEVE_FLOOR_EPS = 1e-4


def _pool_cap_norm(internal_pool, cap_map, total_w):
    """把组合级单标的上限折算成袖内归一化上限：cap_norm = cap / total_w。

    _internal_pool_optimize（实际交付）与 solve_portfolio（算袖内下限该截到哪个上界）
    必须用同一套折算，否则"截断后的下限"未必可交付（2026-10-16 P1）。
    """
    if not cap_map or total_w <= 1e-9:
        return {}
    norm = {}
    for t in internal_pool:
        cap = cap_map.get(t)
        if cap is not None:
            norm[t] = min(1.0, cap / total_w)
    return norm


def _pool_max_return(internal_pool, internal_corr, cap_norm=None):
    """袖内"受上限约束的最大可行收益"（Σx=1、各标的不超 cap_norm）。

    线性目标 + 单纯形约束 → 按 μ 从高到低把权重填到各自上限即最优（贪心，确定性）。
    上界之和不足 1（如单标的池 cap_norm<1）→ 返回 None = 该权重下没有可交付篮子。
    """
    tickers = list(internal_pool.keys())
    if not tickers:
        return None
    caps = {t: min(1.0, float((cap_norm or {}).get(t, 1.0))) for t in tickers}
    if sum(caps.values()) < 1.0 - 1e-9:
        return None
    order = sorted(tickers, key=lambda t: (-float(internal_pool[t]["mu"]), t))
    rem, ret = 1.0, 0.0
    for t in order:
        if rem <= 1e-12:
            break
        take = min(rem, caps[t])
        ret += take * float(internal_pool[t]["mu"])
        rem -= take
    return float(ret)


def _optimize_within_pool(internal_pool, internal_corr, rf, seed, cap_norm=None, n_starts=60,
                          floor=None, warnings=None, label=""):
    """在给定内部池内做「max 夏普 s.t. 袖内权重和 = 1（+ 可选下限 x·μ ≥ floor）」，返回元组或 None。

    阶段二（_internal_pool_optimize）与固定点迭代里的"空袖重新播种"（_sleeve_reseed_params）
    共用这一份实现：前者传归一化上限（组合上限 ÷ 袖权重），后者不设上限
    —— 播种要回答的是"这只袖自己能达到什么"，不该被它当前的组合权重限制。

    floor（P1 方案 A）：组合级目标传导下来的袖内收益下限。SLSQP 只保证"迭代点"在约束上，
    所以这里两道闸（verify F1 加固）：① 循环内先筛 x·μ ≥ floor−ε，违约点不进候选；
    ② 循环后再断言一次。任何一道不通过都**不静默**：返回 None（该袖本轮没有可交付篮子）
    并把可诊断原因写进 warnings，由上层走显式不可交付/降级，而不是交付一个违约点。
    """
    tickers = list(internal_pool.keys())
    if not tickers:
        return None
    n = len(tickers)
    mu = np.array([internal_pool[t]["mu"] for t in tickers])
    vols = np.array([internal_pool[t]["vol"] for t in tickers])
    corr = project_psd(build_corr_matrix(n, tickers, internal_corr))
    cov = np.outer(vols, vols) * corr

    def obj(w):
        ret = float(w @ mu)
        vol = float(np.sqrt(w @ cov @ w))
        return -((ret - rf) / vol if vol > 0 else 0.0)

    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    if floor is not None:
        cons.append({"type": "ineq", "fun": lambda w: float(w @ mu) - float(floor)})
    bounds = [(0.0, 1.0)] * n
    for i, t in enumerate(tickers):
        cap = (cap_norm or {}).get(t)
        if cap is not None:
            bounds[i] = (0.0, min(1.0, float(cap)))

    rng = np.random.default_rng(seed)
    best = None
    for _ in range(n_starts):
        start = rng.random(n)
        start /= start.sum()
        res = minimize(obj, start, method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 1500, "ftol": 1e-12})
        x = res.x
        if abs(x.sum() - 1.0) > 1e-4:
            continue
        if not _respects_bounds(x, _upper_array(bounds)):
            continue
        ret = float(x @ mu)
        vol = float(np.sqrt(x @ cov @ x))
        if floor is not None and ret < float(floor) - SLEEVE_FLOOR_EPS:
            continue                      # 闸①：下限违约点不得成为候选
        sharpe = (ret - rf) / vol if vol > 0 else 0.0
        if best is None or sharpe > best[0]:
            best = (sharpe, x, ret, vol)
    if best is None:
        if floor is not None and warnings is not None:
            warnings.append(
                f"袖内收益下限不可行：{label or '袖（未命名）'} 需要 μ ≥ {float(floor):.2%}，"
                f"但该袖在自身候选池与单标的上限折算下没有满足下限的篮子；本轮按该袖未解出处理。")
        return None
    sharpe, x, ret, vol = best
    # 闸②（F1 加固）：解后断言。理论上闸①已拦住，但绝不允许任何路径静默返回违约篮子。
    if floor is not None and ret < float(floor) - SLEEVE_FLOOR_EPS:
        if warnings is not None:
            warnings.append(
                f"袖内收益下限解后校验失败：{label or '袖（未命名）'} 的解 μ={ret:.2%} 低于下限 "
                f"{float(floor):.2%}；已按不可行处理，不交付该篮子。")
        return None
    return x, ret, vol, sharpe


def _internal_pool_optimize(stage1_weights, assets, internal_pool, internal_corr, broad_key, total_assets,
                            seed=31, cap_map=None, rf=None, floor=None, warnings=None):
    """Generic second-level optimization inside a broad equity bucket.

    floor/warnings（P1 方案 A）：组合级目标传导下来的袖内收益下限与告警通道；
    floor=None 时与修复前逐字节一致（低目标档/重新播种路径都走这条）。
    """
    rf = RF if rf is None else rf
    tickers = list(internal_pool.keys())
    if not tickers:
        return None
    if broad_key not in [a["key"] for a in assets]:
        return None
    idx = [i for i, a in enumerate(assets) if a["key"] == broad_key][0]
    total_w = float(stage1_weights[idx])
    if total_w <= 1e-6:
        return None

    # 桶内权重是"归一化后 × 桶权重"，所以组合上限要折算：cap_norm = cap / total_w
    cap_norm = _pool_cap_norm(internal_pool, cap_map, total_w)

    got = _optimize_within_pool(internal_pool, internal_corr, rf, seed, cap_norm=cap_norm,
                                floor=floor, warnings=warnings, label=broad_key)
    if got is None:
        return None
    x, ret, vol, sharpe = got
    if cap_norm:
        for i, t in enumerate(internal_pool):
            if t in cap_norm and x[i] >= cap_norm[t] - 1e-6:
                _note_cap_hit(t, x[i] * total_w, cap_map[t])
    out = {
        "broad_total_weight": total_w,
        "internal_weights_total": {t: float(x[i]) * total_w for i, t in enumerate(tickers)},
        "internal_weights_normalized": {t: float(x[i]) for i, t in enumerate(tickers)},
        "amounts": {t: float(x[i]) * total_w * total_assets for i, t in enumerate(tickers)},
        "ret": ret,
        "vol": vol,
        "sharpe": sharpe,
    }
    if floor is not None:
        # 加法式键：**仅在下限真正生效时**出现 → 未生效的档位（低目标）与修复前逐字节一致。
        out["floor"] = float(floor)
        out["floor_met"] = bool(ret >= float(floor) - SLEEVE_FLOOR_EPS)
    return out


def stage2_japan_optimize(stage1_weights, assets, total_assets, cap_map=None, rf=None,
                          floor=None, warnings=None):
    """Optimize Japan equity internal factor weights.（floor：P1 袖内收益下限）"""
    return _internal_pool_optimize(stage1_weights, assets, JP_INTERNAL, JP_INTERNAL_CORR, "O_JP_EQ",
                                   total_assets, seed=31, cap_map=cap_map, rf=rf,
                                   floor=floor, warnings=warnings)


def stage2_gc_optimize(stage1_weights, assets, total_assets, cap_map=None, rf=None,
                       floor=None, warnings=None):
    """Optimize Greater China equity internal (沪深300/红利低波/主动/混合/指数/港股宽基/高股息/科技)."""
    return _internal_pool_optimize(stage1_weights, assets, GC_INTERNAL, GC_INTERNAL_CORR, "D_GREATER_CN",
                                   total_assets, seed=43, cap_map=cap_map, rf=rf,
                                   floor=floor, warnings=warnings)


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


# ---------------------------------------------------------------- 完整求解（阶段一 + 袖内固定点）
#
# 2026-10-14 修正（用户实测发现）：阶段一过去把「袖」——由内部标的动态组成的大类
# （美股核心 O_US_CORE、日本权益 O_JP_EQ、大中华权益 D_GREATER_CN）——当成"当前持仓那一篮子"
# （O_US_CORE 的 μ/σ 按 live 权重聚合），而阶段二会在袖内**重新选标的**。
# 于是阶段一的模型 ≠ 交付组合：目标落在切点收益以下时，阶段一正好贴着目标交付，阶段二却把
# 交付收益顶出目标一大截；只扫阶段一的敏感性分析因此与结果页对不上（同一目标点两处结论矛盾）。
# 现在把袖内 μ/σ 迭代到固定点：**阶段一假设的袖内参数 = 阶段二实际交付的那一篮子**，
# 于是"目标 ≥ T"这条约束对交付组合同样有效，敏感性扫描也与主结果同源。
SLEEVE_KEYS = ("O_US_CORE", "O_JP_EQ", "D_GREATER_CN")
# P1（2026-10-16）：只有这两只袖走 _internal_pool_optimize —— 需要下限传导的路径。
# O_US_CORE 的 stage2_optimize 本来就带组合级 ret>=target 约束，不接受也不该接受袖内下限。
FLOOR_SLEEVE_KEYS = ("O_JP_EQ", "D_GREATER_CN")
MAX_SLEEVE_ITER = 10
# 收敛判据放在"袖参数"上：单只袖的 μ/σ 变化都小于 0.4 个百分点就认为阶段一与交付组合同源。
# （袖内选标是离散的：谁入选、谁触 10% 上限会跳变，SLSQP 也有末位噪声，判据过严会永远不收敛。）
SLEEVE_TOL = 4e-3
# 袖内选标是"离散"的（谁入选、谁触上限会跳变），朴素迭代容易来回震荡 →
# 用阻尼（新参数 = 旧 + λ×(新−旧)）把步长压小。
SLEEVE_DAMPING = 0.5
#
# 2026-10-15 修正（用户实测发现）：**"袖全空"曾被误判成收敛**。
# 目标设到高于"当前持仓篮子模型"能支撑的水平时，第一轮 `_solve_stage1` 就会判定目标不可行
# 并回退到最大可行收益 —— 回退解是"各池全押最高收益标的"的角点组合，通常不给袖任何权重，
# 于是阶段二整段跳过、袖参数"没有变化"（delta=0）→ 循环宣布已收敛，把这个角点当成交付组合
# （收益低于用户设的目标、波动大幅抬升）。这个 bug 极其隐蔽：只要角点恰好残留一丝袖权重
# （高于阶段二的 1e-6 早退阈值），阶段二就会跑、delta 就非 0、循环就能继续，几轮后袖的 μ
# 被迭代上来、目标重新可行 —— 换句话说，**交付结果一度取决于那一丝权重是否恰好为零**
# （用户"加一个新标的就塌"的实测，就是它把那一丝抹平了）。
# 现在两道闸：① 回退轮不再参与收敛判定，且回退后按各袖自己的候选池重新播种（乐观初值）
# 再迭代一轮；② 每轮回退都记进 history（fallback/reseeded），全程都不可行时显式报告
# "目标在收敛模型下仍不可行、已按最大可行收益交付"。


def _sleeve_params_us(internal, internal_tickers):
    """阶段二选出的美股袖 → (μ, σ)；σ 用袖内相关性矩阵算真实组合波动。"""
    if internal is None or not internal_tickers:
        return None
    x = np.asarray(internal[0], dtype=float)
    total = float(x.sum())
    if total <= 1e-9:
        return None
    w = x / total
    mus = np.array([US_INTERNAL[t]["mu"] for t in internal_tickers])
    vols = np.array([US_INTERNAL[t]["vol"] for t in internal_tickers])
    corr = project_psd(build_corr_matrix(len(internal_tickers), internal_tickers, US_INTERNAL_CORR))
    cov = np.outer(vols, vols) * corr
    return float(w @ mus), float(np.sqrt(w @ cov @ w))


def _inject_sleeve_params(assets, sleeve):
    """把当前袖参数写进大类资产（O_US_CORE 等），返回新列表，不改原表。"""
    out = []
    for a in assets:
        item = dict(a)
        p = sleeve.get(item["key"])
        if p is not None:
            item["mu"], item["vol"] = float(p[0]), float(p[1])
        out.append(item)
    return out


# 袖 → (内部候选池, 内部相关性, 随机种子)：与阶段二同一套池子，供"空袖重新播种"使用。
# 种子沿用阶段二各自的取值（美股 19 / 日本 31 / 大中华 43），保证可复现。
_SLEEVE_POOL_SPECS = {
    "O_US_CORE": (US_INTERNAL, US_INTERNAL_CORR, 19),
    "O_JP_EQ": (JP_INTERNAL, JP_INTERNAL_CORR, 31),
    "D_GREATER_CN": (GC_INTERNAL, GC_INTERNAL_CORR, 43),
}


# 袖 → 该袖"是否被解出"要看的结果字段（阶段二没跑就是 None）。
_SLEEVE_RESOLVED_FIELD = {
    "O_US_CORE": "internal",
    "O_JP_EQ": "japan_selection",
    "D_GREATER_CN": "gc_selection",
}


def _resolved_sleeves(solved):
    """本轮真正被解出的袖：阶段二跑了并给出了篮子。

    袖得 0 权重时阶段二会在 `total_w <= 1e-6` 处早退（见 stage2_optimize /
    _internal_pool_optimize），于是"袖参数没有变化"只代表**没被检验**，不代表自洽 ——
    收敛判据必须区分这两者，否则回退角点会被当成固定点（见 SLEEVE_DAMPING 下方的说明）。
    """
    return {k for k, field in _SLEEVE_RESOLVED_FIELD.items() if solved.get(field) is not None}


def _sleeve_reseed_params(key, rf):
    """某只袖"自己能达到的最优篮子"参数（不设单标的上限、与阶段一给的权重无关）。

    只用作固定点迭代的**播种值**：袖被阶段一判为 0 权重时阶段二不会运行，那一轮对这只袖
    没有任何信息量；给一个乐观初值重播种，下一轮的模型才不会系统性低估它。
    """
    spec = _SLEEVE_POOL_SPECS.get(key)
    if spec is None:
        return None
    pool, corr, seed = spec
    got = _optimize_within_pool(pool, corr, rf, seed)
    if got is None:
        return None
    _, ret, vol, _ = got
    return (float(ret), float(vol))


def _reseed_sleeves(sleeve, solved, rf, tol=1e-5):
    """把本轮"没被解出"的袖按候选池重新播种，返回真正发生变化的 key 列表。

    已在播种值上时不再重复播种（返回空列表 = 没有可改善的余地）。
    """
    resolved = _resolved_sleeves(solved)
    changed = []
    for key in list(sleeve):
        if key in resolved:
            continue
        seed = _sleeve_reseed_params(key, rf)
        if seed is None:
            continue
        old = sleeve[key]
        if abs(seed[0] - old[0]) <= tol and abs(seed[1] - old[1]) <= tol:
            continue
        sleeve[key] = seed
        changed.append(key)
    return changed


def _stage1_weight(solved, key):
    """该轮阶段一给某只袖的权重（袖不在本轮资产里时返回 0）。"""
    keys = solved.get("keys") or []
    if key not in keys:
        return 0.0
    return abs(float(solved["stage1_weights"][keys.index(key)]))


def _unresolved_weighted_sleeves(solved, sleeve):
    """本轮"阶段一给了权重、阶段二却没解出篮子"的袖（顺序与 sleeve 一致）。

    这类袖在交付统计里是按大类的**模型**参数计价的：`_solve_once` 在阶段二给不出篮子时会把
    final 退化为阶段一统计（避免结果文件全 0，历史 Bug D2）。那一刻 final 不是交付组合的
    统计，必须显式标注、不能让模型统计看起来像可交付结果（2026-10-15 P0）。
    """
    return [k for k in sleeve
            if k not in _resolved_sleeves(solved) and _stage1_weight(solved, k) > 1e-6]


def _sleeve_failure_reason(key):
    """袖没被阶段二解出时的可诊断原因：池为空 / 有候选但解不出可行篮子。"""
    spec = _SLEEVE_POOL_SPECS.get(key)
    if spec is None:
        return "候选池未注册"
    pool = spec[0]
    if not pool:
        return "袖内候选池为空（没有可执行标的）"
    return "袖内有候选标的但阶段二未解出可行篮子（全部起点失败）"


def _validate_sleeve_params(sleeve, sleeve_start=None):
    """袖参数（含 sleeve_start 热启动值）必须有限且 σ>0，否则提前抛出可诊断的 ValueError。

    σ=0/NaN 会让 `build_broad_cov` 出现 0/0 → NaN，随后 stage2_covariance 调 project_psd
    时抛 `numpy.linalg.LinAlgError: Eigenvalues did not converge`，消息里没有任何上下文
    （2026-10-15 P2 修）。宁可在入口就把 key / 取值 / 来源说清楚，也不静默改成"看起来正常"的值。
    """
    for key, params in sleeve.items():
        mu, vol = float(params[0]), float(params[1])
        bad = []
        if not np.isfinite(mu):
            bad.append(f"μ={mu!r}")
        if not np.isfinite(vol) or vol <= 0:
            bad.append(f"σ={vol!r}")
        if not bad:
            continue
        source = "sleeve_start" if (sleeve_start or {}).get(key) else "资产参数"
        raise ValueError(
            f"袖参数非法（来源：{source}）：{key} 的 " + "、".join(bad) +
            "。袖参数必须是有限值且 σ>0 —— σ≤0/NaN 会让协方差矩阵出现 0/0（NaN），"
            "随后 project_psd 抛 numpy.linalg.LinAlgError、无法求解；请修正后重试。")


def _undeliverable_warning(solved, sleeve):
    """本轮交付统计其实只是阶段一**模型**统计时，返回显式的"不可交付"提示；否则 None。"""
    stuck = _unresolved_weighted_sleeves(solved, sleeve)
    if not stuck:
        return None
    detail = "；".join(f"{k}（{_sleeve_failure_reason(k)}）" for k in stuck)
    return ("本次交付统计不可交付：" + detail +
            " 在阶段一有权重、阶段二却没给出篮子，这些权重是按大类**模型**参数计价的，"
            "final 里的收益/波动是阶段一模型统计、不是可交付组合的统计（active 单标的 "
            f"{AC.ACTIVE_CAP:.0%} 上限只在阶段二内部生效，模型口径可能高估收益）。"
            "请勿据此执行，请检查该袖的候选池与参数后重跑。")


def _damp_sleeve_params(nxt, sleeve, damping):
    """阻尼推进：新参数 = 旧 + λ×(新−旧)。

    袖内选标是离散的（谁入选、谁触上限会跳变），朴素迭代容易来回震荡，所以把步长压小。
    """
    return {k: (sleeve[k][0] + damping * (v[0] - sleeve[k][0]),
                sleeve[k][1] + damping * (v[1] - sleeve[k][1]))
            for k, v in nxt.items()}


def _sleeve_tangency(key, rf, cache=None):
    """袖"不加下限"的参考参数 (μ,σ) = 它自己的最大夏普篮子（= 修复前的交付口径）。

    同一轮可能被多次询问，用 cache 记住，避免重复跑 SLSQP。
    """
    if cache is not None and key in cache:
        return cache[key]
    got = _sleeve_reseed_params(key, rf)
    if cache is not None:
        cache[key] = got
    return got


def _max_return_given_mu(mu, upper, dom_idx, ov_idx, dom_w, ov_w):
    """给定 μ 向量时阶段一可达到的最大组合收益（纯贪心 LP：满仓 + 境内池等式 + 上界）。

    每池内按 μ 从高到低填满该池权重（受各标的上界约束）。某池上界凑不够该池权重 → None。
    比 max_feasible_return 便宜（无 SLSQP），可放进二分里反复调用。
    """
    mu = np.asarray(mu, dtype=float)

    def pool_max(idx, need):
        if not idx:
            return None if need > 1e-9 else 0.0
        pairs = sorted(((float(mu[i]), float(upper[i])) for i in idx), reverse=True)
        tot, ret = 0.0, 0.0
        for m, cap in pairs:
            if tot >= need - 1e-12:
                break
            take = min(cap, need - tot)
            ret += take * m
            tot += take
        return ret if tot >= need - 1e-9 else None

    d = pool_max(dom_idx, dom_w)
    o = pool_max(ov_idx, ov_w)
    if d is None or o is None:
        return None
    return d + o
def _required_sleeve_mu(solved, tan_mu, target, dom_w, ov_w):
    """二分求"让阶段一可达上限 ≥ 目标"所需的最小**统一**袖内 μ（P1 方案 A）。

    与**权重无关**：直接对阶段一的上界/池约束算可达上限，因此既不受 U1（多袖各自补缺 →
    超交付 + 风险全抬）影响，也不受 U2（cap_norm = 单标的上限 ÷ 袖权重，逐轮漂移）影响 ——
    这里只用阶段一上界数组，它由 dom_w 与 ACTIVE_CAP 决定、与袖权重无关。

    返回 (t, audit)：t = 所需统一 μ；"切点口径下已达标"或"即使每只袖都抬到自身可达上限
    也够不到目标" → (None, audit)，后者交给既有回退/不可行路径显式报告。
    """
    keys = solved.get("keys") or []
    for field in ("mu", "upper", "dom_idx", "ov_idx"):
        if field not in solved or solved.get(field) is None:
            return None, {"skipped": "missing " + field}
    idx = {k: i for i, k in enumerate(keys)}
    use = [k for k in FLOOR_SLEEVE_KEYS if k in idx and tan_mu.get(k) is not None]
    if not use:
        return None, {"skipped": "no floor sleeve"}
    caps = {}
    for k in use:
        spec = _SLEEVE_POOL_SPECS.get(k)
        m = _pool_max_return(spec[0], spec[1], None) if spec else None
        if m is None:
            return None, {"skipped": "sleeve pool infeasible"}
        caps[k] = float(m)

    # 参考向量：所有袖都取"它自己切点篮子"的 μ 与当前假设 μ 的较大者（非袖资产保持原样）。
    # 关键：门槛必须是"前沿口径"的，不能被某一轮偏弱的初始假设 μ 拉低 —— 否则 T 明明在
    # 切点前沿内可达，却会被判成需要抬升，从而破坏"下限恒松弛时与修复前逐字节一致"的不变量。
    vec0 = np.array(solved["mu"], dtype=float)
    for k in SLEEVE_KEYS:
        if k in idx and tan_mu.get(k) is not None:
            vec0[idx[k]] = max(vec0[idx[k]], float(tan_mu[k]))

    def ceiling(t):
        vec = vec0.copy()
        for k in use:
            vec[idx[k]] = max(vec[idx[k]], float(t))
        return _max_return_given_mu(vec, solved["upper"], solved["dom_idx"], solved["ov_idx"],
                                    dom_w, ov_w)

    lo = max(float(tan_mu[k]) for k in use)
    hi = min(caps[k] for k in use)
    c_lo = ceiling(lo)
    if c_lo is not None and c_lo >= float(target) - 1e-9:
        return None, {"skipped": "frontier already reaches target"}
    c_hi = ceiling(hi)
    if hi <= lo + 1e-12 or c_hi is None or c_hi < float(target) - 1e-9:
        return None, {"skipped": "target unreachable at sleeve max",
                      "tangency_mu": {k: round(float(tan_mu[k]), 6) for k in use},
                      "uncapped_max": {k: round(caps[k], 6) for k in use}}
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        c = ceiling(mid)
        if c is not None and c >= float(target) - 1e-9:
            hi = mid
        else:
            lo = mid
        if hi - lo <= 1e-6:
            break
    audit = {k: {"required_mu": round(float(hi), 6), "tangency_mu": round(float(tan_mu[k]), 6),
                 "uncapped_max": round(caps[k], 6)} for k in use}
    return float(hi), audit


def _floor_next_params(solved, key, t, rf):
    """下限袖下一轮的假设参数：优先本轮**已交付**的篮子（它必须满足下限），否则用下限篮子。

    下限是"有意的模型口径"而不是观测值：直接写成下一轮的假设参数、不让它被随后的阻尼按 λ
    拉回旧观测 —— 否则一次抬升要 ~9 轮才爬到位（表现为预算用尽、目标被误报成不可行）。
    """
    field = _SLEEVE_RESOLVED_FIELD.get(key)
    sel = solved.get(field) if field else None
    if sel is not None and sel.get("ret") is not None:
        return (float(sel["ret"]), float(sel["vol"]))
    return _floor_seed(key, t, rf)


def _floor_seed(key, t, rf):
    """下限对应的可执行篮子参数 (μ,σ)：袖内 max 夏普 s.t. μ ≥ t（不设组合上限）。

    用途 = **乐观播种**：本轮没被阶段二解出的下限袖，下一轮阶段一必须"看见"它抬升后的 μ，
    否则目标高于切点时它永远拿不到权重 —— P1 会在最需要的场景失效。
    """
    spec = _SLEEVE_POOL_SPECS.get(key)
    if spec is None:
        return None
    got = _optimize_within_pool(spec[0], spec[1], rf, spec[2], floor=float(t))
    if got is None:
        return None
    _, ret, vol, _ = got
    return (float(ret), float(vol))


def _compute_sleeve_floors(solved, assumed, target, rf, prev_floors, damping, dom_w, ov_w, cache):
    """回退轮之后重算袖内收益下限（P1 方案 A 的反馈项）。

    口径 = _required_sleeve_mu：所有 JP/GC 被要求抬到**同一个 μ**，该 μ 是"阶段一可达上限
    刚好够到目标"的最小值（与权重无关）。用绝对 μ 而不是"假设 μ + ℓ"：后者会让固定点方程
    退化成 μ = μ + ℓ、只有 ℓ=0 是解，迭代必然来回摆。

    返回 (floors, audit)：floors 只含"下限真高于该袖当前假设 μ"的袖 —— 未绑定的档位与修复前
    逐字节一致。为什么不用方案 C（袖前沿 λ 参数化 + 联合求解）：λ 落在两个前沿节点之间时，
    插值出的 (μ,σ) 未必对应可执行篮子 → 老 gap 以"插值误差"回来，且阶段一要整体重构
    （U4；详见 tmp/agent-team/plan/P1-sleeve-objective.md §2/§6）。
    """
    keys = solved.get("keys") or []
    if not keys or solved.get("mu") is None or solved.get("upper") is None:
        # 受控桩（测试替身）可能不带这些字段：缺了就退化为"不设下限"（生产路径一定带）。
        return None, {}
    tan_mu = {}
    for k in SLEEVE_KEYS:                 # 全部袖的切点 μ：门槛口径要用"前沿"，见 _required_sleeve_mu
        if k in assumed:
            got = _sleeve_tangency(k, rf, cache)
            if got is not None:
                tan_mu[k] = float(got[0])
    if not any(k in tan_mu for k in FLOOR_SLEEVE_KEYS):
        return None, {}
    t, audit = _required_sleeve_mu(solved, tan_mu, target, dom_w, ov_w)
    if t is None:
        return None, audit
    if prev_floors:
        # 下限也是反馈通道：按与袖参数同样的 λ 阻尼（避免整块跳变；U2 的稳定化手段之一）。
        prev_t = min(float(v) for v in prev_floors.values()) + SLEEVE_FLOOR_EPS
        t = prev_t + damping * (t - prev_t)
    floors = {}
    for k in FLOOR_SLEEVE_KEYS:
        if k not in tan_mu:
            continue
        cur = max(float(assumed[k][0]), tan_mu[k])
        if t <= cur + SLEEVE_FLOOR_EPS:
            continue          # 恒松弛：不设下限 → 该袖本轮与修复前逐字节一致
        floors[k] = float(t) - SLEEVE_FLOOR_EPS
    if not floors:
        return None, audit
    return floors, audit


# 阶段一"目标不可行 → 回退到最大可行收益"提示的公共标记：生产端（_solve_stage1）与过滤端
# （solve_portfolio）共用同一段文案，避免两处漂移。这类提示的数字随袖参数阻尼推进逐轮变化，
# 修复前会逐轮累积成 ~10 行（AGENTS.md §12 记为"已知噪声"）；solve_portfolio 现在合并成一条。
_STAGE1_FALLBACK_MARK = "目标在当前约束下不可行，已回退到最大可行收益"


def _is_stage1_fallback_prompt(message):
    """是否为阶段一逐轮回退提示（数字随轮次变，必须合并而不是逐条累积）。"""
    return _STAGE1_FALLBACK_MARK in message


def _solve_stage1(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf,
                  stage1_starts, warnings):
    """阶段一求解与回退链：max 夏普 → min 方差（约束绷紧区间两者等价）→ 最大可行收益。

    返回 (解, 生效目标)。三级都失败时返回 (None, target)，由调用方决定整体失败。
    seed=23 的末级回退是历史行为，不要顺手改（会改变回退结果的可复现性）。
    """
    solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target,
                              n_starts=stage1_starts, rf=rf)
    if solved is None:
        solved = solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf=rf)
    if solved is not None:
        return solved, target
    max_ret = max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w)
    warnings.append(f"{target:.0%} {_STAGE1_FALLBACK_MARK} {max_ret:.2%}。")
    eff_target = max_ret - 1e-6
    solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, eff_target,
                              seed=23, n_starts=stage1_starts, rf=rf)
    return solved, (eff_target if solved is not None else target)


def _solve_once(assets, corr_pairs, *, dom_w, ov_w, target, rf, extract, total_assets,
                log=None, stage1_starts=160, stage2_starts=80, warn=None, sleeve_floors=None):
    """一轮完整求解：阶段一（大类，max 夏普 s.t. 收益 ≥ 目标）→ 阶段二（袖内再优化）。

    sleeve_floors（P1 方案 A）：本轮要传给 JP/GC 袖内求解器的收益下限。默认 None
    —— 低目标档（下限恒松弛）与 re-seed 路径都走 None，行为与修复前逐字节一致。
    """
    warnings = warn if warn is not None else []
    keys = [a["key"] for a in assets]
    mu = np.array([a["mu"] for a in assets])
    vols = np.array([a["vol"] for a in assets])
    cov = build_broad_cov([{"key": a["key"], "vol": a["vol"]} for a in assets], corr_pairs)
    dom_idx = [i for i, a in enumerate(assets) if a["pool"] == "domestic"]
    ov_idx = [i for i, a in enumerate(assets) if a["pool"] == "overseas"]

    lower, upper = stage1_bounds(assets)
    cap_map = _active_cap_map(assets)
    apply_active_caps(upper, assets, cap_map, dom_idx, dom_w, warnings)

    solved, eff_target = _solve_stage1(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w,
                                       float(target), rf, stage1_starts, warnings)
    if solved is None:
        return None
    w1, ret1, vol1, sharpe1 = solved
    for i, a in enumerate(assets):
        cap = cap_map.get(a.get("key"))
        if cap is not None and w1[i] >= cap - 1e-6:
            _note_cap_hit(a["key"], w1[i], cap)
    if log:
        log("stage1_result", f"第一阶段完成 收益={ret1:.2%} 波动={vol1:.2%} 夏普={sharpe1:.3f}")

    floors_in = dict(sleeve_floors or {})
    for k in list(floors_in):                 # 0 权重的袖本轮不跑阶段二 → 传下去也不会生效
        if k not in keys or float(w1[keys.index(k)]) <= 1e-6:
            del floors_in[k]
    japan_selection = stage2_japan_optimize(w1, assets, total_assets, cap_map=cap_map, rf=rf,
                                            floor=floors_in.get("O_JP_EQ"), warnings=warnings)
    gc_selection = stage2_gc_optimize(w1, assets, total_assets, cap_map=cap_map, rf=rf,
                                      floor=floors_in.get("D_GREATER_CN"), warnings=warnings)
    internal, stage2_error = stage2_optimize(
        w1, extract, total_assets, assets, cov, keys, vols, cap_map=cap_map,
        target=eff_target, rf=rf, n_starts=stage2_starts)

    if internal is not None:
        x_internal, ret2, vol2, sharpe2, w2, internal_tickers = internal
        final = {"ret": ret2, "vol": vol2, "sharpe": sharpe2, "floor": ret2 - 1.96 * vol2}
    else:
        # stage2 不可行时汇总回退第一阶段统计，否则结果文件会全成 0（Bug D2）
        final = {"ret": ret1, "vol": vol1, "sharpe": sharpe1, "floor": ret1 - 1.96 * vol1}
        if stage2_error:
            warnings.append(stage2_error)
        warnings.append("美国权益内部优化不可行，组合统计已回退使用第一阶段结果。")

    out = {
        "assets": assets, "keys": keys, "mu": mu, "vols": vols, "cov": cov,
        "dom_idx": dom_idx, "ov_idx": ov_idx, "lower": lower, "upper": upper, "cap_map": cap_map,
        "stage1_weights": w1, "stage1_ret": ret1, "stage1_vol": vol1, "stage1_sharpe": sharpe1,
        "japan_selection": japan_selection, "gc_selection": gc_selection,
        "internal": internal, "internal_tickers": (internal[5] if internal is not None else None),
        "stage2_error": stage2_error, "final": final, "target": eff_target, "warnings": warnings,
    }
    if floors_in:
        # 加法式内部键：本轮**实际生效**的袖内下限（未生效时不出现 → 低目标档逐字节一致）。
        out["sleeve_floors"] = {k: float(v) for k, v in floors_in.items()}
    return out


def _seed_levels_for_target(solved, tan_mu, target, dom_w, ov_w):
    """给"回退角点清零了权重、但前沿尚有余量"的袖求播种水平（2026-10-16 落库）。

    背景：回退轮常把袖压到 0 权重；若只按该袖**切点**篮子重新播种，切点 μ 可能明显低于阶段二
    实际能交付的篮子 → 阶段二因 0 权重早退、永不运行 → 目标被误判不可行（交付高波动角点）。

    契约（attempt#1 已回退，见下）：
      1) **逐袖**水平（不是全体统一 μ）→ 不被"可达上限最低"的袖卡死；
      2) 水平 = 让"阶段一可达上限刚好够到目标"的最小值，**不是该袖上限** → 不过冲。
         播种量必须由"组合需要多少"决定：按袖上限播种会让模型里的袖 μ 跳到上限、与阶段二交付
         篮子来回摆，连本来正常的主场景也会塌成多轮回退角点（attempt#1 的教训）；
      3) 只动"可达上限明显高于切点 μ"的袖；无余量的袖完全不碰（不扰动正常档位）。
    返回 {key: level}；若把有余量的袖全部抬满仍够不到目标 → 返回 {}（真不可行，交给显式报告）。
    """
    keys = solved.get("keys") or []
    if not keys or solved.get("mu") is None or solved.get("upper") is None:
        return {}
    idx = {k: i for i, k in enumerate(keys)}
    ref = np.array(solved["mu"], dtype=float)
    caps = {}
    for k in SLEEVE_KEYS:
        if k not in idx or tan_mu.get(k) is None:
            continue
        ref[idx[k]] = max(ref[idx[k]], float(tan_mu[k]))
        spec = _SLEEVE_POOL_SPECS.get(k)
        m = _pool_max_return(spec[0], spec[1], None) if spec else None
        if m is not None and float(m) > float(tan_mu[k]) + SLEEVE_FLOOR_EPS:
            caps[k] = float(m)

    def ceiling(levels):
        vec = ref.copy()
        for k, lv in levels.items():
            vec[idx[k]] = float(lv)
        return _max_return_given_mu(vec, solved["upper"], solved["dom_idx"], solved["ov_idx"],
                                    dom_w, ov_w)

    if not caps:
        return {}
    base = ceiling({})
    if base is not None and base >= float(target) - 1e-9:
        return {}
    levels = {}
    for k in sorted(caps, key=lambda x: -(caps[x] - float(tan_mu[x]))):
        c = ceiling(levels)
        if c is not None and c >= float(target) - 1e-9:
            break
        lo = max(float(ref[idx[k]]), float(tan_mu[k]))
        hi = caps[k]
        if hi <= lo + 1e-12:
            continue
        top = ceiling(dict(levels, **{k: hi}))
        if top is None or top < float(target) - 1e-9:
            levels[k] = hi          # 抬满也不够 → 抬满后继续下一只
            continue
        for _ in range(60):        # 二分：该袖再抬多少刚好够到目标
            mid = 0.5 * (lo + hi)
            got = ceiling(dict(levels, **{k: mid}))
            if got is not None and got >= float(target) - 1e-9:
                hi = mid
            else:
                lo = mid
            if hi - lo <= 1e-6:
                break
        levels[k] = hi
        break
    c = ceiling(levels)
    if c is None or c < float(target) - 1e-9:
        return {}
    return levels


def solve_portfolio(assets, corr_pairs, *, dom_w, ov_w, target, rf, extract, total_assets,
                    warnings=None, log=None, max_sleeve_iter=MAX_SLEEVE_ITER, sleeve_tol=SLEEVE_TOL,
                    stage1_starts=160, stage2_starts=80, sleeve_start=None,
                    damping=SLEEVE_DAMPING):
    """完整求解（袖内参数固定点迭代）。

    返回 None（不可行）或结果字典；袖参数、迭代轮数与是否收敛一并带出，便于审计与提示。

    收敛判据 = 每只袖的 μ/σ 变化 ≤ sleeve_tol，**但回退轮除外**：某一轮若发生"目标不可行 →
    回退到最大可行收益"，该轮解的是另一个问题（目标被降低、袖多半为空），不参与收敛判定，
    并按各袖候选池重新播种后继续迭代（2026-10-15 修，见 SLEEVE_DAMPING 下方的说明）。
    重播种后那一轮会额外获得预算（每只袖最多补一轮），否则预算刚好用尽时会把已经算出的
    重播种丢掉、还会把可达的目标误报成"不可行"。
    同理，"阶段一给了某只袖权重、阶段二却没跑出结果"的轮次也不算收敛：它的 final 只是
    阶段一的模型统计、不是交付组合（那种情况下 model_gap 恒为 0，会伪装成完美一致）。
    若最终仍不可行，则交付最大可行收益解并在 warnings 里显式说明"收益低于目标"。
    """
    warnings = [] if warnings is None else warnings
    sleeve = {}
    for a in assets:
        if a["key"] in SLEEVE_KEYS and a.get("mu") is not None:
            start = (sleeve_start or {}).get(a["key"])
            sleeve[a["key"]] = tuple(start) if start else (float(a["mu"]), float(a["vol"]))
    _validate_sleeve_params(sleeve, sleeve_start)

    iters = max(1, int(max_sleeve_iter))
    history, converged, delta = [], False, 0.0
    best = None          # (分数, 该轮结果, 该轮假设的袖参数)：未收敛时取误差最小的一轮
    fallback_last = None  # 全程都判"目标不可行"时的兜底（收益低于目标，必须显式说明）
    last_assumed = None   # 最后一轮实际使用的袖参数（审计字段不能输出从未被求解过的值）
    pending_reseed = False
    budget_exhausted = False   # 是否因迭代预算用尽而退出（此时"目标不可行"的结论受预算限制）
    floors = None              # P1 袖内收益下限：只在出现回退轮之后才会被建立（低目标档恒为 None）
    tan_cache = {}             # 袖"切点参考参数"缓存（_sleeve_tangency）
    floor_unreachable_notes = {}   # 目标超过袖内可达上限的袖 → (切点 μ, 可达上限 μ)，收尾合并成一条
    # 重播种本身不产生关于固定点的信息，所以"重播种后还欠一轮检验"时要多给预算：
    # 否则 max_sleeve_iter 刚好用完时会把已经算出的重播种丢掉，还会把**可达**的目标
    # 误报成"不可行、请下调目标"。上界固定为 iters + max(1, 袖数)，保证总轮数有界
    # （正常情况下每只袖至多补一轮；阻尼把参数推离播种值后也可能再次补，故只保证上界）。
    max_rounds = iters + max(1, len(sleeve))
    for it in range(max_rounds):
        if it >= iters and not pending_reseed:
            budget_exhausted = True
            break
        pending_reseed = False
        assumed = dict(sleeve)
        last_assumed = assumed
        warn_local = []
        solved = _solve_once(_inject_sleeve_params(assets, sleeve), corr_pairs,
                             dom_w=dom_w, ov_w=ov_w, target=target, rf=rf, extract=extract,
                             total_assets=total_assets, log=log,
                             stage1_starts=stage1_starts, stage2_starts=stage2_starts,
                             warn=warn_local, sleeve_floors=floors)
        if solved is None:
            return None
        for m in warn_local:
            # 逐轮回退提示的数字随袖参数阻尼推进而变，逐条累积最多 ~10 行（AGENTS.md §12 已知噪声）：
            # 全部丢掉，收尾时合并成**一条**提示（三个信息点见下方 fallback 汇总）。
            if _is_stage1_fallback_prompt(m):
                continue
            if m not in warnings:
                warnings.append(m)

        nxt = dict(sleeve)
        us = _sleeve_params_us(solved["internal"], solved["internal_tickers"])
        if us is not None:
            nxt["O_US_CORE"] = us
        for key, sel in (("O_JP_EQ", solved["japan_selection"]),
                         ("D_GREATER_CN", solved["gc_selection"])):
            if key in nxt and sel and sel.get("ret") is not None:
                nxt[key] = (float(sel["ret"]), float(sel["vol"]))
        delta = max((max(abs(nxt[k][0] - sleeve[k][0]), abs(nxt[k][1] - sleeve[k][1]))
                     for k in nxt if k in sleeve), default=0.0)

        # 本轮是否发生了"目标不可行 → 回退到最大可行收益"（见 _solve_stage1）。
        # 回退轮解的是**另一个问题**（目标被悄悄降低），它的解与"袖参数有没有变化"都不能
        # 当作本次目标下的结论：回退角点通常不给袖任何权重 → 阶段二整段跳过 → delta=0
        # 会被误判成收敛（见 SLEEVE_DAMPING 下方的说明）。所以回退轮既不参与收敛判定，
        # 也不参与"最优轮"的挑选。
        fell_back = float(solved["target"]) < float(target) - 1e-9
        entry = {"iter": it + 1, "delta": round(float(delta), 6),
                 "sleeve": {k: [round(float(v[0]), 6), round(float(v[1]), 6)]
                            for k, v in sleeve.items()},
                 "fallback": fell_back}
        if solved.get("sleeve_floors"):
            # 加法式审计键：仅在本轮真的带了袖内下限时出现（未生效的档位与修复前逐字节一致）。
            entry["sleeve_floors"] = {k: round(float(v), 6)
                                      for k, v in sorted(solved["sleeve_floors"].items())}
        if fell_back:
            fallback_last = solved
            entry["reseeded"] = _reseed_sleeves(sleeve, solved, rf)
            new_floors, floor_audit = _compute_sleeve_floors(
                solved, assumed, target, rf, floors, damping, dom_w, ov_w, tan_cache)
            if new_floors:
                floors = new_floors
                entry["floor_recomputed"] = floor_audit
                seeded = []
                for key in sorted(floors):
                    seed = _floor_next_params(solved, key, floors[key], rf)
                    if seed is not None:
                        # 同时写进 sleeve 与 nxt（见 _floor_next_params 的说明）。
                        sleeve[key] = seed
                        nxt[key] = seed
                        seeded.append(key)
                if seeded:
                    entry["floor_seeded"] = seeded
            elif floor_audit.get("skipped") == "target unreachable at sleeve max":
                # 目标超过袖内上限：不设下限（不硬凑），收尾显式报告并走既有回退/不可行路径。
                for k, m in (floor_audit.get("uncapped_max") or {}).items():
                    floor_unreachable_notes[k] = (floor_audit.get("tangency_mu", {}).get(k), m)
                # 回退角点清零袖后的播种（2026-10-16 落库，承接 AGENTS §12"袖全空"那条的残留）：
                # 角点常把三只袖都压到 0 权重，而"只按该袖切点篮子重新播种"不够 —— 切点 μ 可能
                # 明显低于阶段二实际能交付的篮子 → 阶段二因 0 权重早退、永不运行 → 误判不可行。
                # 契约（仅在"目标高于切点前沿"且走到本分支时生效）：
                #   ① 只对**前沿尚有余量**（可达上限明显高于切点 μ）的袖播种，无余量的袖完全不碰；
                #   ② 水平取"**刚好够到目标**的最小值"，逐袖求解（不取该袖上限、也不被可达上限
                #      最低的袖卡死）—— 播种量必须由"组合需要多少"决定；按袖上限播种会与阶段二
                #      交付篮子来回摆，连正常的主场景也会塌成多轮回退角点（attempt#1 的教训）；
                #   ③ 播种后强制多给一轮预算（pending_reseed），否则播种等于白播；
                #   ④ 审计键 sleeve_seeded_levels 为加法式、仅触发时出现（setup(_seed_levels_for_target)）。
                tan_all = {}
                for k in SLEEVE_KEYS:
                    got_tan = _sleeve_tangency(k, rf, tan_cache)
                    if got_tan is not None:
                        tan_all[k] = float(got_tan[0])
                levels = _seed_levels_for_target(solved, tan_all, target, dom_w, ov_w)
                resolved_now = _resolved_sleeves(solved)
                boot = []
                for key in sorted(levels):
                    if key in resolved_now:
                        continue
                    seed = _floor_seed(key, levels[key], rf)
                    if seed is not None:
                        sleeve[key] = seed
                        nxt[key] = seed
                        boot.append(key)
                if boot:
                    entry["sleeve_seeded_levels"] = {k: round(float(levels[k]), 6) for k in boot}
                    pending_reseed = True      # 播种必须换来一轮检验，否则等于白播
            history.append(entry)
            if log:
                log("sleeve_fixpoint",
                    f"袖内参数迭代 {it + 1}/{max_rounds}：目标在本轮模型下不可行（已回退），"
                    f"按候选池重新播种 {len(entry['reseeded'])} 只袖")
            if entry["reseeded"]:
                pending_reseed = True
                continue                      # 空袖已换成乐观初值 → 下一轮重算（欠一轮检验）
            if not (_resolved_sleeves(solved) & set(sleeve)):
                break                         # 袖全空且已在播种值上：目标确实不可行
            # 袖都被解出了（角点残留了权重）→ 这一轮的袖信息仍然有效，照常阻尼推进；
            # 只是不参与收敛判定，避免把"被降低后的目标"当成答案。
        else:
            history.append(entry)
            # 本轮"交付"是否真是交付：阶段一给了某只袖权重、阶段二却没跑出结果
            # （袖内池为空 / 全部起点失败），此时 final 只是阶段一的**模型**统计
            # （见 _solve_once 的 final 回退分支），model_gap 恒为 0，会被当成
            # "模型与交付完全一致"而拿满分 —— 必须降权，且不能算收敛。
            unresolved_weighted = _unresolved_weighted_sleeves(solved, sleeve)
            if log:
                tail = "（已收敛）" if delta <= sleeve_tol and not unresolved_weighted else ""
                log("sleeve_fixpoint", f"袖内参数迭代 {it + 1}/{max_rounds}：最大变化 {delta:.4f}{tail}")
            # 排序键：先排除"交付其实没发生"的轮次，再看"模型与交付组合差多少"
            # （这才是我们要的一致性），最后看是否收敛。
            model_gap = abs(solved["final"]["ret"] - solved["stage1_ret"]) + \
                abs(solved["final"]["vol"] - solved["stage1_vol"])
            # 条件⑤（P1）：袖内下限一旦生效，"阶段一模型达标"不再等于"交付达标" ——
            # 只有**交付组合**收益 ≥ 本轮生效目标（非回退轮即用户目标）才算收敛。
            # 回退轮走的是另一个分支（本来就排除在收敛判定外）→ 不可行路径不会因此永不收敛（U3）。
            floor_active = bool(solved.get("sleeve_floors"))
            delivered_ok = (not floor_active
                            or float(solved["final"]["ret"]) >= float(solved["target"]) - 1e-4)
            score = (1 if unresolved_weighted else 0, round(model_gap, 6),
                     0 if delta <= sleeve_tol else 1, 0 if delivered_ok else 1)
            if best is None or score < best[0]:
                best = (score, solved, assumed)
            if delta <= sleeve_tol and not unresolved_weighted and delivered_ok:
                converged = True
                break

        # 阻尼推进：把"观测到的新参数"按 λ 混进当前值，避免离散选标引起的来回跳
        sleeve = _damp_sleeve_params(nxt, sleeve, damping)

    if not converged:
        if best is not None:
            # 袖内选标是离散的，映射可能不收敛 → 取"阶段一模型 vs 交付组合"误差最小的那一轮，
            # 而不是硬性采用最后一轮（两者都可能不是固定点）。
            # 注意 score 的构成是（交付是否真的发生, 模型与交付的差, 是否收敛），差值在 [1]。
            _, solved, sleeve = best
            stale_note = ("；注意该轮有袖没被阶段二解出，那些权重仍按大类的**模型**参数计价，"
                          "收益/波动不完全来自交付组合"
                          if best[0][0] else "")
            warnings.append(
                f"袖内参数迭代 {len(history)} 轮未完全收敛（最后一轮最大变化 {delta:.4f}）；"
                f"已采用误差最小的一轮（模型与交付组合的收益/波动差 {best[0][1]:.4f}）{stale_note}。")
            note = _undeliverable_warning(solved, sleeve)
            if note:
                warnings.append(note)
        else:
            # 每一轮都判"目标不可行"（重新播种后仍然不可行）→ 交付的就是最大可行收益解，
            # 它的收益**低于**目标。这里必须明说，不能让回退角点看起来像正常结果。
            # 审计字段要用这一轮**真正用过**的袖参数（重播种后的值可能从未被求解过）。
            solved = fallback_last
            sleeve = last_assumed if last_assumed is not None else sleeve
            delivered = float(solved["final"]["ret"])
            # 合并成**一条**提示（逐轮噪声已在上面的循环里丢掉），三个信息点必须都在：
            # ① 交付收益低于目标 ② 最大可行收益是多少 ③ 迭代预算是否用尽。
            if budget_exhausted:
                # 预算用尽 ≠ 目标真的不可行：末轮可能是"回退 + 袖被解出"，只是阻尼推进还没来得及
                # 被检验。此时不能断言"收敛后的模型下不可行"，更不能建议用户下调目标。
                budget_note = (f"迭代预算已用尽（{len(history)} 轮，上限 {max_rounds} 轮）—— "
                               f"目标未必真的不可行，可提高迭代预算后重试，或下调目标。")
            else:
                budget_note = (f"迭代预算未用尽（{len(history)} 轮，上限 {max_rounds} 轮）："
                               f"目标在收敛后的模型下仍不可行，请下调目标至该值以下，"
                               f"或检查相关标的的参数口径。")
            warnings.append(
                f"目标 {float(target):.2%} 不可行：已按最大可行收益 {delivered:.2%} 交付"
                f"（低于目标 {float(target):.2%}）。{budget_note}")
            note = _undeliverable_warning(solved, sleeve)
            if note:
                warnings.append(note)

    if floor_unreachable_notes:
        # 逐轮累积合并成一条（P3 的教训：逐轮提示会刷屏）。
        detail = "；".join(f"{k} 切点 {float(a):.2%} / 可达上限 {float(b):.2%}"
                           for k, (a, b) in sorted(floor_unreachable_notes.items()))
        warnings.append(
            f"袖内收益下限无法建立：目标 {float(target):.2%} 高于 JP/GC 在阶段一上限口径下"
            f"能提供的水平（{detail}）—— 袖内优化器已按可达上限尽力，本轮走既有的回退/不可行路径。")

    solved["warnings"] = warnings       # 合并后的完整提示（_solve_once 只带自己那一轮的）
    solved.update({
        "sleeve_params": {k: [round(float(v[0]), 6), round(float(v[1]), 6)] for k, v in sleeve.items()},
        "sleeve_iterations": len(history),
        "sleeve_converged": converged,
        "sleeve_history": history,
    })
    return solved


def _inject_test_assets(args, assets, work_dir, warnings):
    """新标的测试：临时标的作为新增资产注入第一阶段（在可投性过滤之后，不受其约束）。

    返回 (assets', test_corr_pairs, test_skipped, test_estimates)。
    全部标的不可用时打印错误 JSON 并以退出码 3 结束（与原 main 内联行为一致）。
    """
    test_estimates, test_skipped = [], []
    if not args.test_tickers:
        return assets, None, test_skipped, test_estimates
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
    # 测试标的没有登记先验：broad/strategy 会退化成纯历史，必须显式说明口径不同
    for e in test_estimates:
        if e.get("mu_note") == "history_only":
            warnings.append(
                f"测试标的 {e['ticker']} 标为「{AC.CLASS_RULES[e['test_class']]['label']}」，"
                f"但该类的人工先验只对登记在册的标的有效；测试标的使用纯历史年化 "
                f"μ={e['mu']:.2%}（组合内同类标的的口径是"
                f"{AC.CLASS_RULES[e['test_class']]['mu_hist_weight']:.0%} 历史 + "
                f"{1-AC.CLASS_RULES[e['test_class']]['mu_hist_weight']:.0%} 先验，两者不可直接横比）。")
    if not test_assets:
        detail = "; ".join(f"{t}: {r}" for t, r in test_skipped) or "无有效标的"
        print(json.dumps(
            {"error": "所有测试标的均不可用", "detail": detail, "warnings": warnings},
            ensure_ascii=False))
        sys.exit(3)
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
    return assets, test_corr_pairs, test_skipped, test_estimates


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
                        help="申赎状态校验子进程总超时秒数")
    parser.add_argument("--hsbc-timeout", type=float, default=60.0,
                        help="基金在售列表抓取单请求超时秒数")
    parser.add_argument("--request-timeout", type=float, default=12.0,
                        help="申赎状态单只请求超时秒数")
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

    # RF (无风险利率) 动态注入: App 侧按池比例加权中美10年国债收益率算出 rf,
    # 通过 extract_live.json 的 "rf" 字段传入（params.py 里不写死利率）。
    # RF 是模块级全局, 同 TARGET_RETURN 模式: global 重绑后约束 lambda 生效.
    global RF
    if extract.get("rf") is not None:
        RF = float(extract["rf"])
        log_step("rf", f"无风险利率覆盖为 {RF:.2%} (动态中美10年国债加权)", log_path=args.log_path)
    else:
        log_step("rf", "提取文件未带 rf，本次夏普按 rf=0 计算", log_path=args.log_path)

    # 提取阶段的提示由 `pm-cli extract` 当场打印；**不再**从提取文件里转发到结果页 ——
    # 提取文件是缓存，转发的旧提示会在改口径后长期滞留（2026-10-14 实测：去掉的两条
    # 资金池口径提示仍从本地缓存的 extract_app.json 里冒出来）。
    warnings = []
    CAP_HITS.clear()
    # 参数口径的提示（缺行情用人工兜底、σ 毛刺丢弃、相关性兜底…）一并带给结果页
    warnings.extend(CALIBRATION_WARNINGS)

    # live_anchors: 用户实际持有的境内基金代码 (从 extract_live.json domestic_holdings 提取),
    # 用 classify_domestic_fund(name) 归类, 覆盖 params.py 写死的 fund_code 作为各类锚定.
    # 效果: select_domestic_funds 优先保留用户已持有基金而非推荐换仓到同类最低费率基金.
    live_anchors = live_anchors_from_extract(extract)
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
            {"error": "联网管线失败（基金在售/申赎状态数据源）", "detail": str(exc)},
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
    # 基金数据源的抓取/校验计数只写进运行日志（结果页不再展示管线遥测，2026-10-14 用户定稿）
    stats = availability.get("stats", {})
    log_step("pipeline_counts",
             f"在售 {hsbc_data.get('total_count', '?')} 只 / 开放申购 {hsbc_data.get('open_count', '?')} 只；"
             f"实时校验请求 {stats.get('checked', '?')} 只 / 开放 {availability.get('open_count', '?')} 只 / "
             f"兜底 {stats.get('hsbc_fallback', 0)} 只",
             log_path=args.log_path)
    log_step("build_open_pool",
             f"开放基金池：{len(hsbc_pool)} 个类别、代表基金 {pool_meta['kept']} 只"
             f"（{stats.get('checked', '?')} 只候选实时校验, 耗时 {availability.get('elapsed_seconds', '?')}s）",
             log_path=args.log_path)

    dom_w = float(extract["domestic_weight"])
    ov_w = float(extract["overseas_weight"])

    internal_weights = {h["ticker"]: h["weight"] for h in extract["us_equity"]["holdings"]}
    core_mu, core_vol = us_core_params(internal_weights)

    assets = build_investable_assets(
        hsbc_pool, pool_meta, avail_funds, hsbc_status, warnings,
        core_mu=core_mu, core_vol=core_vol, drop_note="已从第一阶段剔除")
    log_step("build_assets", f"构建大类资产 {len(assets)} 项", log_path=args.log_path)
    if not any(a["pool"] == "domestic" for a in assets):
        print(
            json.dumps(
                {"error": "没有可申购的境内基金", "warnings": warnings},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    # ---- 新标的测试: 临时标的作为新增资产注入第一阶段 (在可投性过滤之后, 不受其约束) ----
    assets, test_corr_pairs, test_skipped, test_estimates = _inject_test_assets(
        args, assets, work_dir, warnings)

    # 缺 μ/σ 的标的（校准文件里没有它、也没有写死值可用）不能进优化：显式剔除并报警，
    # 不静默用 0 顶上（0 波动会直接霸占权重）。σ 已无人工下限，所以这类标的只能缺席。
    unusable = [a["key"] for a in assets if a.get("mu") is None or a.get("vol") is None]
    if unusable:
        warnings.append(
            "以下标的取不到 μ/σ（校准文件里没有、也没有写死值），本次不参与优化: " + "、".join(unusable))
        assets = [a for a in assets if a.get("mu") is not None and a.get("vol") is not None]

    # ---- 完整求解：阶段一（大类）+ 袖内固定点迭代（2026-10-14）----
    # 袖内 μ/σ 迭代到"阶段一假设 == 阶段二交付"的固定点，因此下面报告的 final
    # 就是阶段一那条约束（收益 ≥ 目标）真正作用到的组合。
    total_assets = args.total_assets or float(extract["total_value"])
    solved_all = solve_portfolio(
        assets,
        {**BROAD_CORR, **(test_corr_pairs if args.test_tickers else {})},
        dom_w=dom_w, ov_w=ov_w, target=TARGET_RETURN, rf=RF,
        extract=extract, total_assets=total_assets, warnings=warnings,
        log=lambda step, msg, level="info": log_step(step, msg, level=level, log_path=args.log_path),
    )
    if solved_all is None:
        print(json.dumps({"error": "Optimization infeasible", "warnings": warnings}, ensure_ascii=False, indent=2))
        sys.exit(1)

    assets = solved_all["assets"]
    keys, mu, vols, cov = solved_all["keys"], solved_all["mu"], solved_all["vols"], solved_all["cov"]
    w1 = solved_all["stage1_weights"]
    ret1, vol1, sharpe1 = solved_all["stage1_ret"], solved_all["stage1_vol"], solved_all["stage1_sharpe"]
    target = solved_all["target"]
    TARGET_RETURN = target          # 回退后的靶值要留给后续（历史 Bug D1）
    cap_map = solved_all["cap_map"]
    internal = solved_all["internal"]
    internal_tickers = solved_all["internal_tickers"]
    stage2_error = solved_all["stage2_error"]
    japan_selection = solved_all["japan_selection"]
    gc_selection = solved_all["gc_selection"]
    final = solved_all["final"]
    log_step("stage1_result", f"第一阶段完成 收益={ret1:.2%} 波动={vol1:.2%} 夏普={sharpe1:.3f}",
             log_path=args.log_path)
    log_step("stage2_internal", "第二阶段：美国权益内部标的优化", log_path=args.log_path)

    domestic_selection = select_domestic_funds(w1, assets, hsbc_pool, total_assets)
    log_step("select_domestic", "境内基金选择完成", log_path=args.log_path)

    target_gap = None
    if target is not None and final is not None:
        target_gap = float(final.get("ret", 0.0)) - float(target)
        if target_gap > 0.002:
            warnings.append(
                f"目标收益 {target:.2%} 是收益**下限**（不是锚）：求解目标是最大化夏普，"
                f"实际交付 {final.get('ret', 0.0):.2%}（高出 {target_gap:.2%}）。"
                f"目标低于切点收益时结果不随滑杆变化，把目标提到切点收益以上才会沿前沿上移。")

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
        "sleeve": {
            "params": solved_all["sleeve_params"],
            "iterations": solved_all["sleeve_iterations"],
            "converged": solved_all["sleeve_converged"],
            "history": solved_all["sleeve_history"],
        },
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
