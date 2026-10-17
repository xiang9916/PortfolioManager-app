"""袖参数 / 切点 / 阻尼 / 重新播种（P3-b1 从 optimize_portfolio **等价搬入**）。

依赖铁律：只依赖 core 内已有实现 + **显式入参**（内部池清单 —— 用 `pool_specs` 传入，
即 optimize_portfolio 的 _SLEEVE_POOL_SPECS 同构映射）。
不 import params / optimize_portfolio / market_data / fund_pipeline / test_tickers，无延迟转发。
"""

from __future__ import annotations

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

