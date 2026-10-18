"""求解内核：实现（P2 从 optimize_common 等价搬入）。

只有 4 处约束校验容差由裸字面量换成同值常量 CONSTRAINT_EPS（任务 4 常量单源），
其余逐字保留 —— 多起点 + bounds 校验 + 池约束行为与搬迁前完全一致。
"""

from __future__ import annotations

from typing import Any, List

import numpy as np
from scipy.optimize import minimize

from .constants import CONSTRAINT_EPS, FALLBACK_MARGIN_EPS
from .constraints import _pool_constraints, _respects_bounds, feasible_start, upper_bounds_array


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
        if abs(w[dom_idx].sum() - dom_w) > CONSTRAINT_EPS:
            continue
        if not _respects_bounds(w, upper_arr):
            continue
        if mu @ w < ret_target - CONSTRAINT_EPS:
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
        if abs(w.sum() - 1) > CONSTRAINT_EPS or abs(w[dom_idx].sum() - dom_w) > CONSTRAINT_EPS:
            continue
        if not _respects_bounds(w, upper_arr):
            continue
        if mu @ w < ret_target - CONSTRAINT_EPS:
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
    """Find the maximum achievable portfolio return under pool constraints.

    2026-10-17（P1）：它决定「目标不可行」时的回退目标，因此**必须**校验候选解：
    满仓 / 境内池权重 / bounds 不合格的迭代点一律丢弃（旧版只信 res.success）。
    """
    bounds = list(zip(lower, upper))
    upper_arr = upper_bounds_array(bounds)
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
            if not res.success:
                continue
            w = res.x
            if abs(w.sum() - 1.0) > CONSTRAINT_EPS:
                continue
            if abs(w[dom_idx].sum() - dom_w) > CONSTRAINT_EPS:
                continue
            if not _respects_bounds(w, upper_arr):
                continue
            best = max(best, float(mu @ w))
        except Exception:
            continue
    return best



def solve_stage1(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf,
                 n_starts: int, warnings_out: List[Any]):
    """阶段一三级回退链（与 optimize_portfolio._solve_stage1 同序、同 seed=23 末级）。

    返回 (解, 生效目标)；三级都失败时返回 (None, target)，由调用方决定整体失败。
    """
    solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target,
                              n_starts=n_starts, rf=rf)
    if solved is None:
        solved = solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf=rf)
    if solved is not None:
        return solved, float(target)
    max_ret = float(max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w))
    from ..models.data_types import Warning
    warnings_out.append(Warning(
        code="target.stage1_fallback",
        message="%s 目标不可行，已按最大可行收益 %.2f%% 交付。" % (
            ("%.0f%%" % (100.0 * float(target))), 100.0 * max_ret),
        severity="critical"))
    eff = max_ret - FALLBACK_MARGIN_EPS
    solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, eff,
                              seed=23, n_starts=n_starts, rf=rf)
    return solved, (eff if solved is not None else float(target))
