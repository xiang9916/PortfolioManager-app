"""阶段二与袖内池优化（P3-b1 从 optimize_portfolio **等价搬入**）。

依赖铁律：只依赖 core 内的数学 + **显式入参**（内部池清单 / 内部相关性 / active 上限表 /
告警载体）；不 import params / optimize_portfolio / market_data / fund_pipeline / test_tickers，
也没有任何延迟转发。触顶记录由调用方传入的 `cap_hits_out` 列表承载 —— core 不写模块级全局。
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from .constants import (ALLOC_REMAINDER_EPS, CAP_HIT_EPS, CHECK_SUM_EPS, CONSERVATION_EPS,
                        DELIVERY_EPS, PSD_JITTER_EPS, SLACK_EPS, WEIGHT_ZERO_EPS, ZERO_EPS)
from .constants import SOLVER_EPS as _SOLVER_EPS
from .constraints import _respects_bounds, upper_bounds_array as _upper_array
from .linalg import build_corr_matrix, portfolio_stats, project_psd
from .warnings import CodedWarning as _Warn


# ── 美股内部标的相关性标定（纯数据；P3-b3 从 optimize_portfolio 等价搬入）────────────
# D_HSBC_SP500（标普500 QDII 大类）与美股内部各执行标的的相关性：手工标定。
# 其余固定大类与内部标的之间没有直接的历史序列可算，用「该大类 vs O_US_CORE」的
# 大类级相关性做代理（见 stage2_covariance）——这是近似，不是估计。
SP500_INTERNAL_CORR = {
    "SPY": 0.95, "VTV": 0.85, "SPMO": 0.85, "UNH": 0.75, "GOOG": 0.75, "BUFFER": 0.93,
}
SP500_INTERNAL_CORR_DEFAULT = 0.75


# ── 阶段二：美股内部重排 ──────────────────────────────────────────────────────
def stage2_covariance(fixed_assets, internal_tickers, broad_cov, broad_keys, broad_vols,
                      *, us_internal, us_internal_corr, sp500_internal_corr,
                      sp500_internal_corr_default):
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
        vols[idx] = us_internal[ticker]["vol"]
        mus[idx] = us_internal[ticker]["mu"]

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
                base_ij = sp500_internal_corr.get(ticker, sp500_internal_corr_default)
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
            corr[i1, i2] = corr[i2, i1] = us_internal_corr.get((t1, t2), us_internal_corr.get((t2, t1), 0.5))

    # PSD projection (shared helper).
    corr = project_psd(corr)
    cov = np.outer(vols, vols) * corr
    cov += np.eye(n) * PSD_JITTER_EPS
    return mus, cov, keys


def stage2_optimize(stage1_weights, extract, total_assets, assets, broad_cov, broad_keys, broad_vols,
                    target, rf, cap_map=None, n_starts=80, *,
                    us_internal, us_internal_corr, sp500_internal_corr,
                    sp500_internal_corr_default, cap_hits_out=None):
    """Optimize internal US equity weights with total US core weight fixed and no per-ticker caps."""
    # Always consider the full US internal candidate pool, even for tickers the
    # extract file does not currently hold. Keeping file tickers first preserves
    # current-holding priority in tie cases, but the optimizer may add back
    # UNH/GOOG/VTV etc. if they improve the risk-adjusted result.
    file_tickers = [h["ticker"] for h in extract["us_equity"]["holdings"] if h["ticker"] in us_internal]
    internal_tickers = []
    for ticker in file_tickers + list(us_internal.keys()):
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
    if core_total <= WEIGHT_ZERO_EPS:
        return None, "Stage 1 gave zero weight to US core equity."

    fixed_assets = [a for a in assets if a["key"] != "O_US_CORE"]
    fixed_weights = [
        float(stage1_weights[i])
        for i, a in enumerate(assets)
        if a["key"] != "O_US_CORE"
    ]

    mus, cov, _ = stage2_covariance(fixed_assets, internal_tickers, broad_cov, broad_keys,
                                       broad_vols, us_internal=us_internal,
                                       us_internal_corr=us_internal_corr,
                                       sp500_internal_corr=sp500_internal_corr,
                                       sp500_internal_corr_default=sp500_internal_corr_default)
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
        while remaining > ALLOC_REMAINDER_EPS and guard < 200:
            available = np.maximum(caps - start, 0.0)
            p = rng.random(n_internal) * available
            if p.sum() <= 0:
                break
            add = min(remaining, float(p.sum()))
            start += add * p / p.sum()
            start = np.minimum(start, caps)
            remaining = core_total - start.sum()
            guard += 1
        if abs(start.sum() - core_total) > CONSERVATION_EPS:
            continue
        res = minimize(objective, start, method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 2000, "ftol": 1e-12})
        # SLSQP 未收敛时会返回"当前迭代点"，可能违反 bounds（实测超过单标的上限）——
        # 不要求 res.success（未收敛但可行的点仍可用），但必须显式校验 bounds（2026-10-13 修）。
        x = res.x
        w = total_weights(x)
        if abs(x.sum() - core_total) > CHECK_SUM_EPS:
            continue
        if not _respects_bounds(x, _upper_array(bounds)):
            continue
        if mus @ w < target - DELIVERY_EPS:
            continue
        ret, vol, sharpe = portfolio_stats(w, mus, cov, rf=rf)
        if best is None or sharpe > best[3]:
            best = (x, ret, vol, sharpe, w, internal_tickers)
    if best is not None and cap_map and cap_hits_out is not None:
        for j, t in enumerate(best[5]):
            cap = cap_map.get(t)
            if cap is not None and best[0][j] >= cap - CAP_HIT_EPS:
                cap_hits_out.append((t, float(best[0][j]), float(cap)))
    return best, None

def pool_cap_norm(internal_pool, cap_map, total_w):
    """把组合级单标的上限折算成袖内归一化上限：cap_norm = cap / total_w。

    internal_pool_optimize（实际交付）与 solve_portfolio（算袖内下限该截到哪个上界）
    必须用同一套折算，否则"截断后的下限"未必可交付（2026-10-16 P1）。
    """
    if not cap_map or total_w <= ZERO_EPS:
        return {}
    norm = {}
    for t in internal_pool:
        cap = cap_map.get(t)
        if cap is not None:
            norm[t] = min(1.0, cap / total_w)
    return norm


def pool_max_return(internal_pool, internal_corr, cap_norm=None):
    """袖内"受上限约束的最大可行收益"（Σx=1、各标的不超 cap_norm）。

    线性目标 + 单纯形约束 → 按 μ 从高到低把权重填到各自上限即最优（贪心，确定性）。
    上界之和不足 1（如单标的池 cap_norm<1）→ 返回 None = 该权重下没有可交付篮子。
    """
    tickers = list(internal_pool.keys())
    if not tickers:
        return None
    caps = {t: min(1.0, float((cap_norm or {}).get(t, 1.0))) for t in tickers}
    if sum(caps.values()) < 1.0 - ZERO_EPS:
        return None
    order = sorted(tickers, key=lambda t: (-float(internal_pool[t]["mu"]), t))
    rem, ret = 1.0, 0.0
    for t in order:
        if rem <= SLACK_EPS:
            break
        take = min(rem, caps[t])
        ret += take * float(internal_pool[t]["mu"])
        rem -= take
    return float(ret)


def optimize_within_pool(internal_pool, internal_corr, rf, seed, cap_norm=None, n_starts=60,
                          floor=None, warnings=None, label=""):
    """在给定内部池内做「max 夏普 s.t. 袖内权重和 = 1（+ 可选下限 x·μ ≥ floor）」，返回元组或 None。

    阶段二（internal_pool_optimize）与固定点迭代里的"空袖重新播种"（_sleeve_reseed_params）
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
        if abs(x.sum() - 1.0) > CHECK_SUM_EPS:
            continue
        if not _respects_bounds(x, _upper_array(bounds)):
            continue
        ret = float(x @ mu)
        vol = float(np.sqrt(x @ cov @ x))
        if floor is not None and ret < float(floor) - _SOLVER_EPS:
            continue                      # 闸①：下限违约点不得成为候选
        sharpe = (ret - rf) / vol if vol > 0 else 0.0
        if best is None or sharpe > best[0]:
            best = (sharpe, x, ret, vol)
    if best is None:
        if floor is not None and warnings is not None:
            warnings.append(_Warn(
                f"袖内收益下限不可行：{label or '袖（未命名）'} 需要 μ ≥ {float(floor):.2%}，"
                f"但该袖在自身候选池与单标的上限折算下没有满足下限的篮子；本轮按该袖未解出处理。",
                "sleeve.floor_infeasible"))
        return None
    sharpe, x, ret, vol = best
    # 闸②（F1 加固）：解后断言。理论上闸①已拦住，但绝不允许任何路径静默返回违约篮子。
    if floor is not None and ret < float(floor) - _SOLVER_EPS:
        if warnings is not None:
            warnings.append(_Warn(
                f"袖内收益下限解后校验失败：{label or '袖（未命名）'} 的解 μ={ret:.2%} 低于下限 "
                f"{float(floor):.2%}；已按不可行处理，不交付该篮子。",
                "sleeve.floor_postcheck_failed"))
        return None
    return x, ret, vol, sharpe


def internal_pool_optimize(stage1_weights, assets, internal_pool, internal_corr, broad_key, total_assets,
                           rf, seed=31, cap_map=None, floor=None, warnings=None, cap_hits_out=None):
    """Generic second-level optimization inside a broad equity bucket.

    floor/warnings（P1 方案 A）：组合级目标传导下来的袖内收益下限与告警通道；
    floor=None 时与修复前逐字节一致（低目标档/重新播种路径都走这条）。
    """
    tickers = list(internal_pool.keys())
    if not tickers:
        return None
    if broad_key not in [a["key"] for a in assets]:
        return None
    idx = [i for i, a in enumerate(assets) if a["key"] == broad_key][0]
    total_w = float(stage1_weights[idx])
    if total_w <= WEIGHT_ZERO_EPS:
        return None

    # 桶内权重是"归一化后 × 桶权重"，所以组合上限要折算：cap_norm = cap / total_w
    cap_norm = pool_cap_norm(internal_pool, cap_map, total_w)

    got = optimize_within_pool(internal_pool, internal_corr, rf, seed, cap_norm=cap_norm,
                               floor=floor, warnings=warnings, label=broad_key)
    if got is None:
        return None
    x, ret, vol, sharpe = got
    if cap_norm and cap_hits_out is not None:
        for i, t in enumerate(internal_pool):
            if t in cap_norm and x[i] >= cap_norm[t] - CAP_HIT_EPS:
                cap_hits_out.append((t, float(x[i] * total_w), float(cap_map[t])))
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
        out["floor_met"] = bool(ret >= float(floor) - _SOLVER_EPS)
    return out


def stage2_japan_optimize(stage1_weights, assets, total_assets, internal_pool, internal_corr, rf,
                          cap_map=None, floor=None, warnings=None, cap_hits_out=None):
    """Optimize Japan equity internal factor weights.（floor：P1 袖内收益下限）"""
    return internal_pool_optimize(stage1_weights, assets, internal_pool, internal_corr, "O_JP_EQ",
                                  total_assets, rf, seed=31, cap_map=cap_map,
                                  floor=floor, warnings=warnings, cap_hits_out=cap_hits_out)


def stage2_gc_optimize(stage1_weights, assets, total_assets, internal_pool, internal_corr, rf,
                       cap_map=None, floor=None, warnings=None, cap_hits_out=None):
    """Optimize Greater China equity internal (沪深300/红利低波/主动/混合/指数/港股宽基/高股息/科技)."""
    return internal_pool_optimize(stage1_weights, assets, internal_pool, internal_corr, "D_GREATER_CN",
                                  total_assets, rf, seed=43, cap_map=cap_map,
                                  floor=floor, warnings=warnings, cap_hits_out=cap_hits_out)

