"""优化器接口与本地凸求解实现（ADR 0003 §4.2）。

单层问题（无袖）**真正走纯求解路径**：直接调 optimize_common 的 solve_max_sharpe/solve_min_var
并自己算 stats —— 不整段转发 solve_portfolio。
袖路径 P0 允许委托（见 core/sleeves.py），但入参必须显式收敛成 ProblemSpec。
D2：active 上限表已搬进 core/constraints（本模块直接调用，不再延迟 import optimize_portfolio）；
内部池清单由调用方经 `ProblemSpec.extract["internal_pool_keys"]` 显式传入（core 不得 import params）。
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Mapping, Sequence

from ..models import serialization
from ..models.data_types import ProblemSpec, SolveResult, Warning
from . import constraints, errors, linalg, sleeves, solver
from .constants import SLACK_EPS


def _to_warning(raw: Any) -> Warning:
    """既有 _Warn（str 子类，带 .code）→ models.Warning。"""
    return Warning(code=getattr(raw, "code", "other"), message=str(raw),
                   severity="warning")


class PortfolioOptimizer(ABC):
    """优化算法接口：未来换 CVXPY / 二次规划 / 商业求解器只需新增实现类。"""

    @abstractmethod
    def optimize(self, problem: ProblemSpec) -> SolveResult:
        raise NotImplementedError


class LocalConvexOptimizer(PortfolioOptimizer):
    """SciPy SLSQP 多起点实现（委托 optimize_common 的既有内核）。"""

    def __init__(self, rf_default: float = 0.025):
        self.rf_default = float(rf_default)

    # ── 校验：内核不抛裸异常（ADR §6）─────────────────────────────────────────
    def _validate(self, problem: ProblemSpec) -> None:
        if not problem.assets:
            raise errors.InputError("ProblemSpec.assets 为空", source="ProblemSpec")
        for a in problem.assets:
            if not math.isfinite(float(a.mu)):
                raise errors.InputError("mu 不是有限数", key=a.key, value=a.mu,
                                        source="AssetSpec.mu")
            if not math.isfinite(float(a.sigma)) or float(a.sigma) <= 0.0:
                raise errors.InputError("sigma 必须为正的有限数", key=a.key, value=a.sigma,
                                        source="AssetSpec.sigma")
            if float(a.lower) > float(a.upper) + SLACK_EPS:
                raise errors.InputError("lower > upper", key=a.key,
                                        value=(a.lower, a.upper), source="AssetSpec bounds")
        if not math.isfinite(float(problem.goal.target_return)):
            raise errors.InputError("target_return 不是有限数",
                                    value=problem.goal.target_return, source="GoalSpec")

    def optimize(self, problem: ProblemSpec) -> SolveResult:
        self._validate(problem)
        import numpy as np

        keys = list(problem.keys)
        mu = np.array([float(a.mu) for a in problem.assets])
        cov = linalg.build_cov([{"key": a.key, "vol": float(a.sigma)} for a in problem.assets],
                               problem.corr_pairs)
        lower, upper = constraints.bounds_arrays(problem)
        dom_idx, ov_idx = constraints.pool_indices(problem)
        dom_w = float(problem.pools.domestic_weight)
        ov_w = float(problem.pools.overseas_weight)

        raw: List[Any] = []
        # active 上限表：内部池清单（params 的 US/JP/GC_INTERNAL）由调用方经 extract 显式传入
        # —— core 不得 import params（D2）。触顶记录由内核返回、不进内核全局。
        internal_keys = tuple(problem.extract.get("internal_pool_keys") or ())
        asset_rows = serialization.legacy_asset_rows(problem)
        constraints.apply_active_caps(upper, asset_rows,
                                      constraints.active_cap_map(asset_rows, internal_keys),
                                      dom_idx, dom_w, raw)
        warnings: List[Warning] = [_to_warning(w) for w in raw]

        if not problem.is_single_layer:
            # 袖路径：P0 委托既有固定点引擎（入参已显式收敛成 DTO）
            return sleeves.solve_sleeves(problem, warnings=warnings)

        solved, eff_target = solver.solve_stage1(
            mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w,
            float(problem.goal.target_return), float(problem.goal.rf),
            int(problem.stage1_starts), warnings)
        if solved is None:
            raise errors.InfeasibleTarget(
                "阶段一三级回退均失败（目标 %.6f）" % float(problem.goal.target_return),
                value=float(problem.goal.target_return), source="LocalConvexOptimizer")
        w, ret, vol, sharpe = solved
        if not constraints.respects_bounds(w, upper):
            raise errors.SolverFailure("求解器返回越界点", value=list(map(float, w)),
                                       source="LocalConvexOptimizer")
        stats = {"ret": float(ret), "vol": float(vol), "sharpe": float(sharpe),
                 "floor": float(ret) - 1.96 * float(vol)}
        weights = {k: float(v) for k, v in zip(keys, list(w))}
        return SolveResult(weights=weights, stats=stats, warnings=tuple(warnings),
                           sleeves={"params": None, "iterations": 1, "converged": True},
                           selected_round=1, deliverable=True, final_source="delivered",
                           audit={"effective_target": float(eff_target), "single_layer": True,
                                  "domestic_weight": dom_w, "overseas_weight": ov_w})
