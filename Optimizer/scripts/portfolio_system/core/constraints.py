"""约束装配：上下界 / active 上限表 / 池约束 / 可行性（委托既有实现）。"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..models.data_types import ProblemSpec, SLEEVE_KEYS
from . import errors


def _legacy():
    """延迟导入既有实现（P0 委托；P2 搬迁后本函数消失）。"""
    import optimize_portfolio as OP
    import optimize_common as OC
    from ..models import serialization
    return OP, OC, serialization


def bounds_arrays(problem: ProblemSpec):
    """(lower, upper) ndarray（委托 optimize_common.stage1_bounds）。"""
    _OP, OC, ser = _legacy()
    rows = ser.legacy_asset_rows(problem)
    lower, upper = OC.stage1_bounds(rows)
    return lower, upper


def pool_indices(problem: ProblemSpec) -> Tuple[List[int], List[int]]:
    dom = [i for i, a in enumerate(problem.assets) if a.pool == "domestic"]
    ov = [i for i, a in enumerate(problem.assets) if a.pool != "domestic"]
    return dom, ov


def active_cap_map(problem: ProblemSpec) -> Dict[str, float]:
    """active 类单标的上限表（委托 optimize_portfolio._active_cap_map）。"""
    OP, _OC, ser = _legacy()
    return OP._active_cap_map(ser.legacy_asset_rows(problem))


def apply_active_caps(upper, problem: ProblemSpec, cap_map: Mapping[str, float],
                      warnings_out: List[Any]) -> None:
    """就地套用 active 上限（委托 optimize_portfolio.apply_active_caps）。

    warnings_out 里会被追加既有 _Warn 对象（调用方负责转成 models.Warning）。
    """
    OP, _OC, ser = _legacy()
    rows = ser.legacy_asset_rows(problem)
    dom_idx, _ov = pool_indices(problem)
    OP.apply_active_caps(upper, rows, dict(cap_map), dom_idx,
                         float(problem.pools.domestic_weight), warnings_out)


def respects_bounds(weights, upper, tol: float = 1e-6) -> bool:
    """多起点解必须校验 bounds（委托 optimize_common._respects_bounds）。"""
    _OP, OC, _ser = _legacy()
    return bool(OC._respects_bounds(weights, upper, tol=tol))


def max_feasible_return(problem: ProblemSpec) -> float:
    """组合层最大可行收益（委托 optimize_common.max_feasible_return）。"""
    _OP, OC, _ser = _legacy()
    dom, ov = pool_indices(problem)
    rows = problem.assets
    import numpy as np
    mu = np.array([float(a.mu) for a in rows])
    lower, upper = bounds_arrays(problem)
    return float(OC.max_feasible_return(mu, lower, upper, dom, ov,
                                        float(problem.pools.domestic_weight),
                                        float(problem.pools.overseas_weight)))
