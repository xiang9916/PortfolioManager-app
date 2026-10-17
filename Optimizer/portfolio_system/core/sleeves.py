"""袖内固定点引擎的门面（ADR 0003 §4/§7）。

**P3-b2 起本模块不再有上层依赖**：固定点迭代实现已搬进 core/engine.py，
本文件只做「DTO → 引擎入参」的显式收敛 + 结果回填（不 import optimize_portfolio）。
内部池表（SleeveUniverse）由装配层经 ProblemSpec.extract["sleeve_universe"] 显式传入 ——
core 不 import params，所以缺失时显式报错，绝不静默退化成"没有内部池"的空跑。
"""

from __future__ import annotations

from typing import Any, List, Mapping, Sequence

from ..models.data_types import ProblemSpec, SolveResult, Warning
from ..models import serialization
from . import engine, errors


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


def universe_from_extract(extract) -> Any:
    """从 ProblemSpec.extract 取装配层放进来的 SleeveUniverse（P3-b2）。

    装配职责在 callers（base_runner）；core 不 import params，因此**必须**由上层显式放进
    extract。缺失 → 显式 InputError（不静默跑成"没有内部池"的退化结果）。
    """
    uni = (extract or {}).get("sleeve_universe")
    if uni is None:
        raise errors.InputError(
            "ProblemSpec.extract 缺少 sleeve_universe：内部池表由装配层（callers.base_runner）"
            "显式传入；core 不 import params。",
            source="ProblemSpec.extract")
    return uni


def solve_sleeves(problem: ProblemSpec, warnings: Sequence[Warning] | None = None,
                  hooks: Any = None) -> SolveResult:
    """显式 DTO 收敛后驱动 core 的两层引擎（P3-b2：不再委托 optimize_portfolio）。"""
    universe = universe_from_extract(problem.extract)
    rows = serialization.legacy_asset_rows(problem)
    pairs = dict(problem.corr_pairs)
    extract = serialization.minimal_extract(problem)
    raw: List[Any] = []
    out = engine.solve_portfolio(
        rows, pairs,
        dom_w=float(problem.pools.domestic_weight),
        ov_w=float(problem.pools.overseas_weight),
        target=float(problem.goal.target_return),
        rf=float(problem.goal.rf),
        extract=extract,
        total_assets=float(problem.total_assets),
        universe=universe,
        cap_hits_out=[],
        warnings=raw,
        max_sleeve_iter=int(problem.goal.max_sleeve_iter),
        damping=float(problem.goal.damping),
        stage1_starts=int(problem.stage1_starts),
        stage2_starts=int(problem.stage2_starts),
        hooks=hooks,
    )
    if out is None:
        raise errors.SolverFailure("袖内固定点引擎返回 None（不可行）",
                                   value=float(problem.goal.target_return),
                                   source="core.sleeves")
    converted = [Warning(code=getattr(w, "code", "other"), message=str(w)) for w in raw]
    return result_from_legacy(out, list(warnings or []) + converted)
