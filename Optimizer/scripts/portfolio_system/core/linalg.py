"""线性代数与统计：实现（P2 从 optimize_common / params 等价搬入）。

契约：纯计算，不 import services、不做 I/O；optimize_common / params 只做再导出。
"""

from __future__ import annotations

import numpy as np


def portfolio_stats(weights, mu, cov, rf=0.025):
    """Return (return, volatility, sharpe) for a weight vector.

    rf is read from the caller's module-level RF at call time (passed explicitly),
    so dynamic overrides (extract_live.json rf field) take effect.
    """
    weights = np.asarray(weights, dtype=float)
    ret = float(weights @ mu)
    vol = float(np.sqrt(weights @ cov @ weights))
    sharpe = (ret - rf) / vol if vol > 0 else 0.0
    return ret, vol, sharpe


def project_psd(corr):
    """Project a symmetric matrix to the nearest PSD correlation matrix.

    Single-pass eigenvalue clip + renormalization (used for internally-constructed
    correlation matrices that are already close to PSD).  For iterative Higham
    projection use params.higham_psd instead.
    """
    a = (corr + corr.T) / 2.0
    vals, vecs = np.linalg.eigh(a)
    vals = np.clip(vals, 0.0, None)
    out = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(out))
    out = out / np.outer(d, d)
    return (out + out.T) / 2.0


def build_corr_matrix(n, tickers, corr_pairs, fallback=0.5):
    """Build an n×n correlation matrix from a dict of (ticker_i, ticker_j) → value.

    Symmetric: looks up both (a, b) and (b, a) before falling back.
    """
    corr = np.eye(n)
    for i, a in enumerate(tickers):
        for j, b in enumerate(tickers):
            if i != j:
                corr[i, j] = corr_pairs.get((a, b), corr_pairs.get((b, a), fallback))
    return corr


def higham_psd(matrix, max_iter=200):
    """Project a symmetric matrix to the nearest PSD correlation matrix."""
    a = (matrix + matrix.T) / 2.0
    x = a.copy()
    y = a.copy()
    for _ in range(max_iter):
        r = x - y
        vals, vecs = np.linalg.eigh(x)
        vals = np.clip(vals, 0.0, None)
        xn = vecs @ np.diag(vals) @ vecs.T
        y = xn + r
        x = y.copy()
    x = (x + x.T) / 2.0
    vals, vecs = np.linalg.eigh(x)
    vals = np.clip(vals, 0.0, None)
    x = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(x))
    corr = x / np.outer(d, d)
    return (corr + corr.T) / 2.0



def build_cov(rows, corr_pairs):
    """协方差：延迟转发 params.build_broad_cov（P2 残留：该函数不在本任务搬迁清单）。

    本函数只是转发，避免 core 与 params 两处各写一份实现；P3/P4 搬走后删除。
    """
    from params import build_broad_cov as _impl
    return _impl(list(rows), dict(corr_pairs))
