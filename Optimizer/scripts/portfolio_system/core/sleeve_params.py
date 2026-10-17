"""袖参数 / 切点 / 阻尼 / 重新播种（P3-b1 从 optimize_portfolio **等价搬入**）。

依赖铁律：只依赖 core 内已有实现 + **显式入参**（内部池清单 —— 用 `pool_specs` 传入，
即 optimize_portfolio 的 _SLEEVE_POOL_SPECS 同构映射）。
不 import params / optimize_portfolio / market_data / fund_pipeline / test_tickers，无延迟转发。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

from .linalg import build_corr_matrix, project_psd
from .stage2 import optimize_within_pool


def sleeve_params_us(internal, internal_tickers, us_internal, us_internal_corr):
    """阶段二选出的美股袖 → (μ, σ)；σ 用袖内相关性矩阵算真实组合波动。"""
    if internal is None or not internal_tickers:
        return None
    x = np.asarray(internal[0], dtype=float)
    total = float(x.sum())
    if total <= 1e-9:
        return None
    w = x / total
    mus = np.array([us_internal[t]["mu"] for t in internal_tickers])
    vols = np.array([us_internal[t]["vol"] for t in internal_tickers])
    corr = project_psd(build_corr_matrix(len(internal_tickers), internal_tickers, us_internal_corr))
    cov = np.outer(vols, vols) * corr
    return float(w @ mus), float(np.sqrt(w @ cov @ w))


def inject_sleeve_params(assets, sleeve):
    """把当前袖参数写进大类资产（O_US_CORE 等），返回新列表，不改原表。"""
    out = []
    for a in assets:
        item = dict(a)
        p = sleeve.get(item["key"])
        if p is not None:
            item["mu"], item["vol"] = float(p[0]), float(p[1])
        out.append(item)
    return out


def sleeve_reseed_params(key, rf, pool_specs):
    """某只袖"自己能达到的最优篮子"参数（不设单标的上限、与阶段一给的权重无关）。

    只用作固定点迭代的**播种值**：袖被阶段一判为 0 权重时阶段二不会运行，那一轮对这只袖
    没有任何信息量；给一个乐观初值重播种，下一轮的模型才不会系统性低估它。
    """
    spec = pool_specs.get(key)
    if spec is None:
        return None
    pool, corr, seed = spec
    got = optimize_within_pool(pool, corr, rf, seed)
    if got is None:
        return None
    _, ret, vol, _ = got
    return (float(ret), float(vol))


def damp_sleeve_params(nxt, sleeve, damping):
    """阻尼推进：新参数 = 旧 + λ×(新−旧)。

    袖内选标是离散的（谁入选、谁触上限会跳变），朴素迭代容易来回震荡，所以把步长压小。
    """
    return {k: (sleeve[k][0] + damping * (v[0] - sleeve[k][0]),
                sleeve[k][1] + damping * (v[1] - sleeve[k][1]))
            for k, v in nxt.items()}


def sleeve_tangency(key, rf, pool_specs, cache=None):
    """袖"不加下限"的参考参数 (μ,σ) = 它自己的最大夏普篮子（= 修复前的交付口径）。

    同一轮可能被多次询问，用 cache 记住，避免重复跑 SLSQP。
    """
    if cache is not None and key in cache:
        return cache[key]
    got = sleeve_reseed_params(key, rf, pool_specs)
    if cache is not None:
        cache[key] = got
    return got

# ── 袖/固定点常量（P3-b2 从 optimize_portfolio 等价搬入；旧位置再导出）────────────
SLEEVE_KEYS = ("O_US_CORE", "O_JP_EQ", "D_GREATER_CN")
#: P1（2026-10-16）：只有这两只袖走 internal_pool_optimize —— 需要下限传导的路径。
FLOOR_SLEEVE_KEYS = ("O_JP_EQ", "D_GREATER_CN")
MAX_SLEEVE_ITER = 10
#: 收敛判据：单只袖的 μ/σ 变化都小于 0.4 个百分点就认为阶段一与交付组合同源。
SLEEVE_TOL = 4e-3
#: 袖内选标是离散的，用阻尼把步长压小。
SLEEVE_DAMPING = 0.5
#: 袖 → 该袖"是否被解出"要看的结果字段（阶段二没跑就是 None）。
SLEEVE_RESOLVED_FIELD = {
    "O_US_CORE": "internal",
    "O_JP_EQ": "japan_selection",
    "D_GREATER_CN": "gc_selection",
}


@dataclass(frozen=True)
class SleeveUniverse:
    """两层流水线所需的**全部**外部数据表（由调用方装配；core 只读、不 import params）。

    `pool_specs`：袖 → (内部候选池, 内部相关性, 随机种子)，与阶段二同一套池子
    （optimize_portfolio._SLEEVE_POOL_SPECS 的同构映射；模块级变量打桩仍兼容）。
    """

    us_internal: Mapping
    us_internal_corr: Mapping
    jp_internal: Mapping
    jp_internal_corr: Mapping
    gc_internal: Mapping
    gc_internal_corr: Mapping
    sp500_internal_corr: Mapping = field(default_factory=dict)
    sp500_internal_corr_default: float = 0.75
    pool_specs: Mapping = field(default_factory=dict)
    internal_pool_keys: Tuple[str, ...] = ()


#: 袖 → 阶段二的随机种子（美股 19 / 日本 31 / 大中华 43；P3-b3 从 optimize_portfolio 搬入）。
#: 只放**种子**，池表仍由调用方显式传入（core 不 import params）。
SLEEVE_SEEDS = {"O_US_CORE": 19, "O_JP_EQ": 31, "D_GREATER_CN": 43}


def build_pool_specs(us_internal, us_internal_corr, jp_internal, jp_internal_corr,
                     gc_internal, gc_internal_corr):
    """袖 → (内部候选池, 内部相关性, 随机种子)：装配层的唯一构造方式。

    与 SleeveUniverse.pool_specs 同构；optimize_portfolio._SLEEVE_POOL_SPECS 与
    sensitivity_analysis._sleeve_universe() 都走这里，避免两处各写一份 19/31/43。
    """
    return {
        "O_US_CORE": (us_internal, us_internal_corr, SLEEVE_SEEDS["O_US_CORE"]),
        "O_JP_EQ": (jp_internal, jp_internal_corr, SLEEVE_SEEDS["O_JP_EQ"]),
        "D_GREATER_CN": (gc_internal, gc_internal_corr, SLEEVE_SEEDS["D_GREATER_CN"]),
    }


@dataclass(frozen=True)
class SleeveHooks:
    """固定点迭代里**由调用方提供**的策略函数（P3-b2）。

    设计意图：既有测试对 optimize_portfolio 的模块级**函数**打桩要尽量继续生效
    （`_solve_once` / `_sleeve_params_us` / `_compute_sleeve_floors` /
    `_seed_levels_for_target` / `_floor_seed`）→ 薄壳在调用时读模块级名字、经本结构注入。
    全部为 None 时由 core 用自带实现（并把 universe.pool_specs 绑定进去）。
    """

    solve_once: Any = None
    sleeve_params_us: Any = None
    compute_sleeve_floors: Any = None
    seed_levels_for_target: Any = None
    floor_seed: Any = None
