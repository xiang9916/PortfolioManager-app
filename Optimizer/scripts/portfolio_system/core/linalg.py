"""线性代数与统计的薄壳：**委托**既有实现，不复制算法（ADR 0003 §4/§7 P0）。

所有既有实现都在函数内延迟导入：这样模块级 AST 里不出现任何 services/I-O 依赖，
纯度守卫可以在不启动 numpy/legacy 模块的情况下检查本文件。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence, Tuple


def project_psd(corr):
    """相关性矩阵 PSD 修复（委托 optimize_common.project_psd）。"""
    from optimize_common import project_psd as _impl
    return _impl(corr)


def higham_psd(matrix, max_iter: int = 200):
    """Higham 最近 PSD 投影（委托 params.higham_psd；P2 会搬进 core）。"""
    from params import higham_psd as _impl
    return _impl(matrix, max_iter=max_iter)


def build_corr_matrix(n: int, tickers: Sequence[str],
                      corr_pairs: Mapping[Tuple[str, str], float], fallback: float = 0.5):
    """相关性矩阵（委托 optimize_common.build_corr_matrix）。"""
    from optimize_common import build_corr_matrix as _impl
    return _impl(n, tickers, corr_pairs, fallback=fallback)


def build_cov(rows: Sequence[Mapping[str, Any]],
              corr_pairs: Mapping[Tuple[str, str], float]):
    """协方差（委托 params.build_broad_cov；rows 需含 key/vol）。"""
    from params import build_broad_cov as _impl
    return _impl(list(rows), dict(corr_pairs))


def portfolio_stats(weights, mu, cov, rf: float = 0.025):
    """(收益, 波动, 夏普)（委托 optimize_common.portfolio_stats）。"""
    from optimize_common import portfolio_stats as _impl
    return _impl(weights, mu, cov, rf=rf)
