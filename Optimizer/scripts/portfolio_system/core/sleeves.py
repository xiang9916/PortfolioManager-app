"""袖内固定点引擎的门面（ADR 0003 §4/§7 P0）。

P0 允许委托既有 solve_portfolio，但**入参必须显式收敛成 ProblemSpec 里的数**：
本模块不读任何模块级全局（TARGET_RETURN / RF / SLEEVE_DAMPING 等一律来自 GoalSpec）。
P2 会把固定点迭代本身搬进 core，届时本文件不再 import optimize_portfolio。
"""

from __future__ import annotations

from typing import Any, List, Mapping, Sequence

from ..models.data_types import ProblemSpec, SolveResult, Warning
from ..models import serialization
from . import errors


def result_from_legacy(out: Mapping[str, Any], warnings: Sequence[Warning]) -> SolveResult:
    """既有 solve_portfolio 结果 → SolveResult（键名映射的唯一来源在 models/serialization）。"""
    keys = list(out.get("keys") or [])
    w1 = out.get("stage1_weights")
    weights = ({str(k): float(v) for k, v in zip(keys, list(w1))}
               if (w1 is not None and keys) else {})
    final = dict(out.get("final") or {})
    return SolveResult(
        weights=weights,
        stats={k: float(final[k]) for k in serialization.STATS_KEYS if k in final},
        warnings=tuple(warnings),
        sleeves={"params": out.get("sleeve_params"),
                 "iterations": out.get("sleeve_iterations"),
                 "converged": out.get("sleeve_converged"),
                 "history": out.get("sleeve_history")},
        selected_round=int(out.get("sleeve_iterations") or 1),
        deliverable=bool(out.get("deliverable", True)),
        final_source=str(out.get("final_source", "delivered")),
        audit={"sleeve_cycle": out.get("sleeve_cycle"),
               "excluded_sleeves": out.get("excluded_sleeves"),
               "weighted_no_basket": out.get("weighted_no_basket")},
    )


def solve_sleeves(problem: ProblemSpec, warnings: Sequence[Warning] | None = None) -> SolveResult:
    """显式 DTO 收敛后委托既有固定点引擎（P0）。"""
    import optimize_portfolio as OP          # 延迟 import：模块级 AST 保持无 I/O 依赖

    rows = serialization.legacy_asset_rows(problem)
    pairs = dict(problem.corr_pairs)
    extract = serialization.minimal_extract(problem)
    raw: List[Any] = []
    out = OP.solve_portfolio(
        rows, pairs,
        dom_w=float(problem.pools.domestic_weight),
        ov_w=float(problem.pools.overseas_weight),
        target=float(problem.goal.target_return),
        rf=float(problem.goal.rf),
        extract=extract,
        total_assets=float(problem.total_assets),
        warnings=raw,
        max_sleeve_iter=int(problem.goal.max_sleeve_iter),
        damping=float(problem.goal.damping),
        stage1_starts=int(problem.stage1_starts),
        stage2_starts=int(problem.stage2_starts),
    )
    if out is None:
        raise errors.SolverFailure("袖内固定点引擎返回 None（不可行）",
                                   value=float(problem.goal.target_return),
                                   source="core.sleeves")
    converted = [Warning(code=getattr(w, "code", "other"), message=str(w)) for w in raw]
    return result_from_legacy(out, list(warnings or []) + converted)
