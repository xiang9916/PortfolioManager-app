"""约束装配：上下界 / active 上限表 / 池约束 / 可行性。

P2：bounds 与池约束的实现已从 optimize_common 等价搬入本模块（optimize_common 只再导出）。
active 上限表仍在既有脚本（P3 搬），这里以延迟 import 方式委托。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from ..models.data_types import ProblemSpec


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



def _serialization():
    from ..models import serialization
    return serialization


def bounds_arrays(problem: ProblemSpec):
    """(lower, upper) ndarray（ProblemSpec → 既有 bounds 口径）。"""
    return stage1_bounds(_serialization().legacy_asset_rows(problem))


def pool_indices(problem: ProblemSpec) -> Tuple[List[int], List[int]]:
    dom = [i for i, a in enumerate(problem.assets) if a.pool == "domestic"]
    ov = [i for i, a in enumerate(problem.assets) if a.pool != "domestic"]
    return dom, ov


def active_cap_map(problem: ProblemSpec) -> Dict[str, float]:
    """active 类单标的上限表（延迟委托既有 optimize_portfolio；P3 搬入）。"""
    import optimize_portfolio as OP
    return OP._active_cap_map(_serialization().legacy_asset_rows(problem))


def apply_active_caps(upper, problem: ProblemSpec, cap_map: Mapping[str, float],
                      warnings_out: List[Any]) -> None:
    """就地套用 active 上限（延迟委托既有实现；warnings_out 收既有 _Warn 对象）。"""
    import optimize_portfolio as OP
    rows = _serialization().legacy_asset_rows(problem)
    dom_idx, _ov = pool_indices(problem)
    OP.apply_active_caps(upper, rows, dict(cap_map), dom_idx,
                         float(problem.pools.domestic_weight), warnings_out)


def respects_bounds(weights, upper, tol: float = 1e-6) -> bool:
    """多起点解必须校验 bounds（本模块实现）。"""
    return _respects_bounds(weights, upper, tol=tol)


def max_feasible_return_for(problem: ProblemSpec) -> float:
    """ProblemSpec 口径的最大可行收益（数组口径实现在 core/solver.py）。"""
    mu = np.array([float(a.mu) for a in problem.assets])
    lower, upper = bounds_arrays(problem)
    dom, ov = pool_indices(problem)
    from .solver import max_feasible_return
    return float(max_feasible_return(mu, lower, upper, dom, ov,
                                    float(problem.pools.domestic_weight),
                                    float(problem.pools.overseas_weight)))
