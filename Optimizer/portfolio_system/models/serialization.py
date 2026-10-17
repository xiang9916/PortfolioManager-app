"""DTO 与既有 JSON 键名的映射（键名唯一来源；ADR 0003 §4.1 与 R4）。

本模块只做「形状」转换，不含任何数值口径。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .data_types import ProblemSpec, SolveResult, Warning

#: 结果 JSON 的既有键名（字节级契约，不许改）
RESULT_KEYS: Tuple[str, ...] = ("weights", "final", "warnings", "warning_items",
                                "deliverable", "final_source", "sleeve")
STATS_KEYS: Tuple[str, ...] = ("ret", "vol", "sharpe", "floor")
WARNING_ITEM_KEYS: Tuple[str, ...] = ("code", "message")


def warning_item(warning: Warning) -> Dict[str, str]:
    """单条告警 → 既有 warning_items 元素（只含 code/message）。"""
    return {"code": warning.code, "message": warning.message}


def warning_items(warnings: Iterable[Warning]) -> List[Dict[str, str]]:
    return [warning_item(w) for w in warnings]


def warning_messages(warnings: Iterable[Warning]) -> List[str]:
    """既有 warnings（字符串数组）视图 — 保持字符串数组不变。"""
    return [w.message for w in warnings]


def legacy_asset_rows(problem: ProblemSpec) -> List[Dict[str, Any]]:
    """ProblemSpec → 既有求解器吃的 asset 行（键名与 optimize_portfolio 一致）。"""
    rows = []
    for a in problem.assets:
        extra = dict(a.extra)
        rows.append({
            "key": a.key,
            "name": a.name,
            "mu": float(a.mu),
            "vol": float(a.sigma),
            "pool": a.pool,
            "test_class": str(extra.get("test_class", a.asset_class)),
            "market": a.market or str(extra.get("market", "")),
            "upper": float(a.upper),
            "lower": float(a.lower),
        })
    return rows


def corr_pairs_to_payload(pairs: Mapping[Tuple[str, str], float]) -> List[List[Any]]:
    """{('A','B'): r} → [['A','B',r]]（corr_pairs 的既有载荷形状）。"""
    return [[str(a), str(b), float(r)] for (a, b), r in pairs.items()]


def corr_pairs_from_payload(payload: Sequence[Sequence[Any]]) -> Dict[Tuple[str, str], float]:
    return {(str(row[0]), str(row[1])): float(row[2]) for row in payload}


def result_payload(result: SolveResult) -> Dict[str, Any]:
    """SolveResult → 既有结果键名（用于等价性逐字段比较）。"""
    return {
        "weights": {str(k): float(v) for k, v in result.weights.items()},
        "final": {k: float(result.stats[k]) for k in STATS_KEYS if k in result.stats},
        "warnings": warning_messages(result.warnings),
        "warning_items": warning_items(result.warnings),
        "deliverable": bool(result.deliverable),
        "final_source": str(result.final_source),
        "sleeve": dict(result.sleeves),
    }


def payload_from_legacy(solved: Mapping[str, Any], warnings: Sequence[Any]) -> Dict[str, Any]:
    """既有 solve_portfolio 结果 → 同一份键名（等价性门用；容忍缺键）。"""
    keys = list(solved.get("keys") or [])
    w1 = solved.get("stage1_weights")
    weights = {}
    if w1 is not None and keys:
        weights = {str(k): float(v) for k, v in zip(keys, list(w1))}
    final = dict(solved.get("final") or {})
    return {
        "weights": weights,
        "final": {k: float(final[k]) for k in STATS_KEYS if k in final},
        "warnings": [str(w) for w in warnings],
        "warning_items": [{"code": getattr(w, "code", "other"), "message": str(w)} for w in warnings],
        "deliverable": bool(solved.get("deliverable", True)),
        "final_source": str(solved.get("final_source", "delivered")),
        "sleeve": {
            "params": solved.get("sleeve_params"),
            "iterations": solved.get("sleeve_iterations"),
            "converged": solved.get("sleeve_converged"),
            "history": solved.get("sleeve_history"),
        },
    }


def minimal_extract(problem: ProblemSpec) -> Dict[str, Any]:
    """从 ProblemSpec 里显式构造既有求解器需要的最小 extract（不读任何模块全局）。"""
    pools = problem.pools
    total = float(problem.total_assets or 1.0)
    payload = dict(problem.extract or {})
    payload.setdefault("domestic_weight", float(pools.domestic_weight))
    payload.setdefault("overseas_weight", float(pools.overseas_weight))
    payload.setdefault("domestic_value", total * float(pools.domestic_weight))
    payload.setdefault("overseas_value", total * float(pools.overseas_weight))
    payload.setdefault("total_value", total)
    payload.setdefault("rf", float(problem.goal.rf))
    return payload
