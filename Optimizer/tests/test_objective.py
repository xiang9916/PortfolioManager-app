#!/usr/bin/env python3
"""目标函数单测：两阶段统一为 max 夏普后的性质（用真实大类参数，不联网）。"""

import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from params import BROAD_ASSETS, BROAD_CORR, RF, build_broad_cov      # noqa: E402
from optimize_common import solve_min_var, solve_max_sharpe, stage1_bounds  # noqa: E402

# 虚构的池拆分：任何有效比例都能跑这个测试；**不要**把真实组合的境内/境外比例写进来
DOM_W, OV_W = 0.30, 0.70
assets = [a for a in BROAD_ASSETS if a["mu"] is not None]
mu = np.array([a["mu"] for a in assets])
vols = np.array([a["vol"] for a in assets])
cov = build_broad_cov([{"key": a["key"], "vol": a["vol"]} for a in assets], BROAD_CORR)
dom = [i for i, a in enumerate(assets) if a["pool"] == "domestic"]
ov = [i for i, a in enumerate(assets) if a["pool"] == "overseas"]
lo, hi = stage1_bounds(assets)


def _solve(T):
    ms = solve_max_sharpe(mu, cov, lo, hi, dom, ov, DOM_W, OV_W, T, rf=RF)
    mv = solve_min_var(mu, cov, lo, hi, dom, ov, DOM_W, OV_W, T, rf=RF)
    return ms, mv


def test_max_sharpe_beats_or_ties_min_var():
    """同一目标收益下，max 夏普的夏普不可能低于 min 方差。"""
    for T in (0.04, 0.06, 0.08, 0.10, 0.12):
        ms, mv = _solve(T)
        assert ms is not None and mv is not None, f"T={T} 求解失败"
        assert ms[3] >= mv[3] - 1e-6, f"T={T}: max夏普 {ms[3]:.4f} < min方差 {mv[3]:.4f}"


def test_solutions_coincide_above_tangency():
    """目标高于切点收益时，两个目标给同一个解（约束绷紧）。"""
    tang = solve_max_sharpe(mu, cov, lo, hi, dom, ov, DOM_W, OV_W, -1.0, rf=RF)
    T = max(0.10, tang[1] + 0.01)
    ms, mv = _solve(T)
    assert ms is not None and mv is not None
    assert abs(ms[1] - mv[1]) < 1e-3 and abs(ms[2] - mv[2]) < 1e-3, (ms[:3], mv[:3])
    assert abs(np.abs(ms[0] - mv[0]).sum()) < 5e-2


def test_target_is_only_a_floor():
    """目标低于切点收益时，max 夏普给的是切点组合，与目标无关。"""
    tang = solve_max_sharpe(mu, cov, lo, hi, dom, ov, DOM_W, OV_W, -1.0, rf=RF)
    for T in (0.01, 0.03, max(0.001, tang[1] - 0.02)):
        ms, _ = _solve(T)
        assert ms is not None
        assert ms[1] >= T - 1e-6                       # 满足下限
        assert abs(ms[3] - tang[3]) < 1e-3, (T, ms[3], tang[3])   # 仍是切点


def test_constraints_and_bounds_hold():
    ms, _ = _solve(0.10)
    w = ms[0]
    assert abs(w.sum() - 1) < 1e-4
    assert abs(w[dom].sum() - DOM_W) < 1e-4
    assert np.all(w >= -1e-6) and np.all(w <= 1 + 1e-6)
    assert mu @ w >= 0.10 - 1e-4


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  ✗ {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
