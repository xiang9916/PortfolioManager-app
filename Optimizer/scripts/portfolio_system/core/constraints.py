"""约束装配：上下界 / active 上限表 / 池约束 / 可行性。

P2：bounds 与池约束的实现已从 optimize_common 等价搬入本模块（optimize_common 只再导出）。
D2：active 上限表（`active_cap_map` / `apply_active_caps`）从 optimize_portfolio **等价搬入**，
不再延迟 import 上层脚本。核心规则仍只有一份事实来源 = `asset_classes`（纯规则表，允许 import）；
内部池清单（params 的 US/JP/GC_INTERNAL）与触顶记录的下游处理**一律由调用方显式传入** ——
本模块不 import params / optimize_portfolio / market_data / fund_pipeline / test_tickers，
也不写任何模块级全局（触顶记录作为**返回值**交给调用方）。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

import asset_classes as AC

from .constants import BOUNDS_TOL, FEASIBILITY_EPS, POOL_CONFLICT_EPS
from .warnings import CodedWarning
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
        if rem < -FEASIBILITY_EPS:
            return None
        if cap.sum() <= 0:
            if abs(rem) > FEASIBILITY_EPS:
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


def _respects_bounds(w, upper_arr, tol=BOUNDS_TOL):
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


def active_cap_map(assets, internal_pool_keys: Iterable[str] = ()) -> Dict[str, float]:
    """key → 组合权重上限。仅对 class == active 的标的生效。

    必须覆盖**所有**标的：大类槽位（如 O_US_ENERGY=XOM）、美股/日本/大中华内部标的
    （AAPL、NVDA…）以及 test_class=active 的测试标的 —— 漏掉内部标的会让上限形同虚设
    （2026-10-13 实测：曾出现单标的被拿到超过 10% 上限、却没人拦截的情况）。

    `internal_pool_keys`：内部池（params 的 US/JP/GC_INTERNAL）的 key 全集 ——
    core 不得 import params，因此**由调用方显式传入**（optimize_portfolio 传真实池清单；
    DTO 路径从 ProblemSpec.extract 读同名键）。只从大类槽位构建会漏掉内部标的。
    """
    caps: Dict[str, float] = {}

    def consider(key, cls=None):
        if (cls or AC.asset_class(key)) == "active":
            caps[key] = AC.ACTIVE_CAP

    for a in assets or []:
        consider(a.get("key", ""), a.get("test_class"))
    for key in internal_pool_keys or ():
        consider(key)
    return caps


def apply_active_caps(upper, assets, cap_map: Mapping[str, float], dom_idx, dom_w,
                      warnings) -> List[Tuple[str, float, float]]:
    """把 active 类单标的上限写进 stage-1 上界；与池权重约束冲突时放宽到可行并报警。

    可行性条件：每个池的 upper 之和必须 ≥ 该池的目标权重，否则优化必然不可行
    （典型情形：境内池只有一只可用标的，却要求它 ≤ 10%，而境内目标权重 30%）。

    **不写任何模块级全局**：触顶记录以 `[(key, weight, cap), …]` 返回，
    由调用方决定是否并入自己的汇总（optimize_portfolio 据此更新 CAP_HITS）。
    """
    capped = []
    for i, a in enumerate(assets):
        cap = cap_map.get(a.get("key"))
        if cap is not None and upper[i] > cap:
            upper[i] = cap
            capped.append(a["key"])
    if not capped:
        return []
    pool_idx = [i for i, a in enumerate(assets) if a.get("pool") == "domestic"]
    if pool_idx:
        capsum = float(sum(upper[i] for i in pool_idx))
        if capsum < dom_w - POOL_CONFLICT_EPS and capped:
            # 只放宽"被上限压住"的境内成员，补足缺口
            relaxed = [i for i in pool_idx if assets[i].get("key") in cap_map]
            need = dom_w - capsum
            if relaxed:
                add = need / len(relaxed)
                for i in relaxed:
                    upper[i] = min(1.0, upper[i] + add)
                warnings.append(CodedWarning(
                    f"active 类单标的上限 {AC.ACTIVE_CAP:.0%} 会与境内池权重 {dom_w:.2%} 冲突"
                    f"（境内可用标的的合计上界不足），已相应放宽 {len(relaxed)} 个境内标的的上限以保证可行。",
                    "active_cap.pool_conflict"))
    return [(k, float(upper[next(i for i, a in enumerate(assets) if a.get("key") == k)]),
             float(cap_map[k])) for k in capped]


def respects_bounds(weights, upper, tol: float = BOUNDS_TOL) -> bool:
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


def max_return_given_mu(mu, upper, dom_idx, ov_idx, dom_w, ov_w):
    """给定 μ 向量时阶段一可达到的最大组合收益（纯贪心 LP：满仓 + 境内池等式 + 上界）。

    每池内按 μ 从高到低填满该池权重（受各标的上界约束）。某池上界凑不够该池权重 → None。
    比 max_feasible_return 便宜（无 SLSQP），可放进二分里反复调用。

    （P3-b1 从 optimize_portfolio._max_return_given_mu 等价搬入，逐字未改。）
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
