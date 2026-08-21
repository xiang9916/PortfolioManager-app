#!/usr/bin/env python3
"""Shared optimization helpers used by optimize_portfolio.py and sensitivity_analysis.py.

Extracted to eliminate copy-paste duplication of portfolio_stats, feasible_start,
solve_min_var, max_feasible_return, stage1_bounds, and PSD projection logic.
"""

import numpy as np
from scipy.optimize import minimize


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


def feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng):
    """Generate a random feasible starting point satisfying pool-weight constraints."""
    n = len(lower)
    x = np.zeros(n)
    for idx, target in ((dom_idx, ov_w), (ov_idx, ov_w)):
        if not idx:
            continue
        lo = lower[idx]
        hi = upper[idx]
        cap = hi - lo
        rem = target - lo.sum()
        if rem < -1e-9:
            return None
        if cap.sum() <= 0:
            if abs(rem) > 1e-9:
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


def solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, ret_target,
                  n_starts=120, seed=7, rf=0.025):
    """Minimize portfolio variance subject to full-investment, pool, and return constraints.

    Returns (w, ret, vol, sharpe) or None if no feasible solution is found.
    """
    n = len(mu)
    bounds = list(zip(lower, upper))
    cons = [
        {"type": "eq", "fun": lambda w: w.sum() - 1.0},
        {"type": "eq", "fun": lambda w: w[dom_idx].sum() - dom_w},
        {"type": "ineq", "fun": lambda w: mu @ w - ret_target},
    ]
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(n_starts):
        start = feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng)
        if start is None:
            continue
        res = minimize(
            lambda w: float(np.sqrt(w @ cov @ w)),
            start,
            method="SLSQP",
            bounds=bounds,
            constraints=cons,
            options={"maxiter": 2500, "ftol": 1e-14},
        )
        if not res.success:
            continue
        w = res.x
        if abs(w.sum() - 1) > 1e-4 or abs(w[dom_idx].sum() - dom_w) > 1e-4:
            continue
        if mu @ w < ret_target - 1e-4:
            continue
        if np.any(w < -1e-6) or np.any(w > np.array(upper) + 1e-6):
            continue
        vol = float(np.sqrt(w @ cov @ w))
        if best is None or vol < best[3]:
            ret = float(mu @ w)
            sharpe = (ret - rf) / vol if vol > 0 else 0.0
            best = (w, ret, vol, sharpe)
    return best


def max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w,
                         n_starts=80, seed=11):
    """Find the maximum achievable portfolio return under pool constraints."""
    n = len(mu)
    bounds = list(zip(lower, upper))
    cons = [
        {"type": "eq", "fun": lambda w: w.sum() - 1.0},
        {"type": "eq", "fun": lambda w: w[dom_idx].sum() - dom_w},
    ]
    rng = np.random.default_rng(seed)
    best = -1.0
    for _ in range(n_starts):
        start = feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng)
        if start is None:
            continue
        try:
            res = minimize(
                lambda w: -mu @ w,
                start,
                method="SLSQP",
                bounds=bounds,
                constraints=cons,
                options={"maxiter": 2000, "ftol": 1e-14},
            )
            if res.success:
                best = max(best, float(mu @ res.x))
        except Exception:
            continue
    return best
