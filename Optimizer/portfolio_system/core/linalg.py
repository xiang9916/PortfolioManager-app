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
    return project_psd_stats(corr)[0]


def project_psd_stats(corr):
    """project_psd 的**可观测**版本：返回 (matrix, stats)。

    stats = {eigmin_before, eigmin_after, changed_pairs, max_delta}。
    数学行为与 project_psd 完全相同（同一实现，project_psd 只是取第 0 项）。
    """
    a = (corr + corr.T) / 2.0
    vals, vecs = np.linalg.eigh(a)
    vals = np.clip(vals, 0.0, None)
    out = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(out))
    out = out / np.outer(d, d)
    out = (out + out.T) / 2.0
    diff = np.abs(out - a)
    stats = {
        "eigmin_before": round(float(np.linalg.eigvalsh(a).min()), 6),
        "eigmin_after": round(float(np.linalg.eigvalsh(out).min()), 6),
        "changed_pairs": int((diff > 1e-6).sum() // 2),
        "max_delta": round(float(diff.max()), 6),
    }
    return out, stats


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



def psd_diff_stats(before, after):
    """PSD 修复的可观测统计（不改变矩阵本身，只描述 before/after 差异）。"""
    diff = np.abs(np.asarray(after, dtype=float) - np.asarray(before, dtype=float))
    return {
        "eigmin_before": round(float(np.linalg.eigvalsh((before + np.asarray(before).T) / 2.0).min()), 6),
        "eigmin_after": round(float(np.linalg.eigvalsh((after + np.asarray(after).T) / 2.0).min()), 6),
        "changed_pairs": int((diff > 1e-6).sum() // 2),
        "max_delta": round(float(diff.max()), 6),
    }


def build_broad_cov(assets, corr_pairs, missing_out=None, diag_out=None):
    """Build covariance matrix for broad asset keys.

    missing_out / diag_out（2026-10-17，P1 可观测性，均为**加法式、默认 None**）：
    - missing_out：list 时把「本矩阵需要但 corr_pairs 里没有」的 key 对追加进去。
      缺省行为不变（缺失对按 0 相关处理；现金类资产确实近似零相关，非现金对
      应被视为可疑并由调用方决定告警/审计）。
    - diag_out：list 时把 PSD 修复统计（eigmin / 改写对数 / 最大 Δ）追加进去。
    未提供这两个参数时，输出矩阵与修复前逐字节一致。
    """
    keys = [a["key"] for a in assets]
    vols = np.array([a["vol"] for a in assets])
    corr = np.eye(len(keys))
    for (i, j), v in corr_pairs.items():
        if i in keys and j in keys:
            ii, jj = keys.index(i), keys.index(j)
            corr[ii, jj] = corr[jj, ii] = v
    if missing_out is not None:
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if (a, b) not in corr_pairs and (b, a) not in corr_pairs:
                    missing_out.append((a, b))
    if diag_out is not None:
        # 数值路径与默认路径**完全一致**（同一个 higham_psd 调用）；diag 只是旁路统计。
        before = corr.copy()
        corr = higham_psd(corr)
        diag_out.append(psd_diff_stats(before, corr))
    else:
        corr = higham_psd(corr)
    return np.outer(vols, vols) * corr


def build_cov(rows, corr_pairs):
    """协方差：延迟转发 params.build_broad_cov（P2 残留：该函数不在本任务搬迁清单）。

    本函数只是转发，避免 core 与 params 两处各写一份实现；P3/P4 搬走后删除。
    """
    return build_broad_cov(list(rows), dict(corr_pairs))
