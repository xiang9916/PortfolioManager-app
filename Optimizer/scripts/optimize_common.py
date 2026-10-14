#!/usr/bin/env python3
"""Shared optimization helpers used by optimize_portfolio.py and sensitivity_analysis.py.

Extracted to eliminate copy-paste duplication of portfolio_stats, feasible_start,
solve_min_var, max_feasible_return, stage1_bounds, and PSD projection logic.

2026-10-14 重构（不改变任何求解行为）：
- 两个求解器共用的约束构造（满仓 + 池等式）与 bounds 校验提取成 _pool_constraints /
  upper_bounds_array / _respects_bounds，改一处即两处生效；
- solve_min_var 的最优候选比较由 best[3]（夏普）修正为 best[2]（波动）——原写法把
  "新候选方差 < 已存最优方差" 误写成 "< 已存最优夏普"，等于保留最后一个候选而不是
  方差最小的一个。目标本身是凸的、各起点收敛到同一点，实际结果不受影响，
  但选择逻辑必须与目标一致。
"""
from typing import Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize


def portfolio_stats(weights, mu, cov, rf=0.025):
    """Return (return, volatility, sharpe) for a weight vector.

    rf is read from the caller's module-level RF at call time (passed explicitly),
    so dynamic overrides (extract_live.json rf field) take effect.
    """
    weights = np.asarray(weights, dtype=float)
    ret = float(weights @ mu)
    vol = float(np.sqrt(weights @ cov @ weights))
    sharpe = (ret - rf) / vol if vol > 0 else 0.0
    return ret, vol, sharpe


def project_psd(corr):
    """Project a symmetric matrix to the nearest PSD correlation matrix.

    Single-pass eigenvalue clip + renormalization (used for internally-constructed
    correlation matrices that are already close to PSD).  For iterative Higham
    projection use params.higham_psd instead.
    """
    a = (corr + corr.T) / 2.0
    vals, vecs = np.linalg.eigh(a)
    vals = np.clip(vals, 0.0, None)
    out = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(out))
    out = out / np.outer(d, d)
    return (out + out.T) / 2.0


def build_corr_matrix(n, tickers, corr_pairs, fallback=0.5):
    """Build an n×n correlation matrix from a dict of (ticker_i, ticker_j) → value.

    Symmetric: looks up both (a, b) and (b, a) before falling back.
    """
    corr = np.eye(n)
    for i, a in enumerate(tickers):
        for j, b in enumerate(tickers):
            if i != j:
                corr[i, j] = corr_pairs.get((a, b), corr_pairs.get((b, a), fallback))
    return corr


def feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng):
    """Generate a random feasible starting point satisfying pool-weight constraints.

    两个池各自填到自己的目标 (dom→dom_w, ov→ov_w)。此前这里把境内块也填成了 ov_w,
    于是每个起点都违反 sum(w)=1 与境内池等式 （实测：起点合计与境内权重都会超出约束）,
    SLSQP 每次都要先花迭代把点拉回可行域; 最优解本身没受影响(第一阶段是凸问题),
    但 120 次重启的多样性形同虚设 (2026-10-13 修)。
    """
    n = len(lower)
    x = np.zeros(n)
    for idx, target in ((dom_idx, dom_w), (ov_idx, ov_w)):
        if not idx:
            continue
        lo = lower[idx]
        hi = upper[idx]
        cap = hi - lo
        rem = target - lo.sum()
        if rem < -1e-9:
            return None
        if cap.sum() <= 0:
            if abs(rem) > 1e-9:
                return None
            x[idx] = lo
            continue
        p = rng.random(len(idx)) * cap
        if p.sum() <= 0:
            return None
        x[idx] = lo + rem * p / p.sum()
    return x


def stage1_bounds(assets):
    """Default stage-1 bounds: each asset weight in [0, 1]."""
    n = len(assets)
    return np.zeros(n), np.ones(n)


def upper_bounds_array(bounds):
    """把 scipy 的 bounds 列表转成上界数组（None = 无上界 → np.inf）。"""
    return np.array([np.inf if b[1] is None else float(b[1]) for b in bounds])


def _pool_constraints(dom_idx, dom_w):
    """满仓 + 境内池等式（两个求解器共用；池比例是硬约束，见 AGENTS.md §12）。"""
    return [
        {"type": "eq", "fun": lambda w: w.sum() - 1.0},
        {"type": "eq", "fun": lambda w: w[dom_idx].sum() - dom_w},
    ]


def _respects_bounds(w, upper_arr, tol=1e-6):
    """SLSQP 未收敛时返回的迭代点可能越界（实测过单标的超过上限），必须显式校验。

    下界统一按 0 处理（组合权重不为负）；上界来自 bounds 数组（None 已转 inf）。
    """
    return bool(np.all(w >= -tol) and np.all(w <= upper_arr + tol))


def solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, ret_target,
                  n_starts=120, seed=7, rf=0.025):
    """Minimize portfolio variance subject to full-investment, pool, and return constraints.

    Returns (w, ret, vol, sharpe) or None if no feasible solution is found.
    """
    bounds = list(zip(lower, upper))
    upper_arr = upper_bounds_array(bounds)
    cons = _pool_constraints(dom_idx, dom_w) + [
        {"type": "ineq", "fun": lambda w: mu @ w - ret_target},
    ]
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(n_starts):
        start = feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng)
        if start is None:
            continue
        res = minimize(
            lambda w: float(np.sqrt(w @ cov @ w)),
            start,
            method="SLSQP",
            bounds=bounds,
            constraints=cons,
            options={"maxiter": 2500, "ftol": 1e-14},
        )
        if not res.success:
            continue
        w = res.x
        if abs(w[dom_idx].sum() - dom_w) > 1e-4:
            continue
        if not _respects_bounds(w, upper_arr):
            continue
        if mu @ w < ret_target - 1e-4:
            continue
        vol = float(np.sqrt(w @ cov @ w))
        # 最优候选 = 方差最小者（best[2]）；此前误写 best[3]（夏普），见模块 docstring。
        if best is None or vol < best[2]:
            ret = float(mu @ w)
            sharpe = (ret - rf) / vol if vol > 0 else 0.0
            best = (w, ret, vol, sharpe)
    return best


def solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, ret_target,
                     n_starts=160, seed=13, rf=0.025):
    """最大化夏普比率 s.t. 满仓 / 池权重 / 收益下限（2026-10-13 起两阶段统一用这个目标）。

    夏普是**比值**目标（拟凹而非凸），所以必须多起点搜索，并且**必须显式校验 bounds** ——
    SLSQP 未收敛时返回的迭代点可能越界（实测过单标的超过上限的情况）。

    返回 (w, ret, vol, sharpe) 或 None。
    """
    bounds = list(zip(lower, upper))
    upper_arr = upper_bounds_array(bounds)
    cons = _pool_constraints(dom_idx, dom_w) + [
        {"type": "ineq", "fun": lambda w: mu @ w - ret_target},
    ]

    def neg_sharpe(w):
        vol = float(np.sqrt(w @ cov @ w))
        if vol <= 0:
            return 1e6
        return -float((mu @ w - rf) / vol)

    rng = np.random.default_rng(seed)
    best = None
    for _ in range(n_starts):
        start = feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng)
        if start is None:
            continue
        res = minimize(neg_sharpe, start, method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 2500, "ftol": 1e-12})
        w = res.x
        if abs(w.sum() - 1) > 1e-4 or abs(w[dom_idx].sum() - dom_w) > 1e-4:
            continue
        if not _respects_bounds(w, upper_arr):
            continue
        if mu @ w < ret_target - 1e-4:
            continue
        vol = float(np.sqrt(w @ cov @ w))
        if vol <= 0:
            continue
        ret = float(mu @ w)
        sharpe = (ret - rf) / vol
        if best is None or sharpe > best[3]:
            best = (w, ret, vol, sharpe)
    return best


def max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w,
                        n_starts=80, seed=11):
    """Find the maximum achievable portfolio return under pool constraints."""
    bounds = list(zip(lower, upper))
    cons = _pool_constraints(dom_idx, dom_w)
    rng = np.random.default_rng(seed)
    best = -1.0
    for _ in range(n_starts):
        start = feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng)
        if start is None:
            continue
        try:
            res = minimize(
                lambda w: -mu @ w,
                start,
                method="SLSQP",
                bounds=bounds,
                constraints=cons,
                options={"maxiter": 2000, "ftol": 1e-14},
            )
            if res.success:
                best = max(best, float(mu @ res.x))
        except Exception:
            continue
    return best
