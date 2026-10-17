#!/usr/bin/env python3
"""求解器/约束硬化（P1，2026-10-17）：bounds 校验、上限可配置、DTO 上界生效。离线、纯虚构。

契约：
  ① `max_feasible_return` 只接受满足 满仓/境内池/bounds 的候选点（它决定回退目标，不能脏）；
  ② `bounds_arrays` 让 `AssetSpec.lower/upper` 生效（默认 0/1 → 与旧行为一致）；
  ③ `active_cap_map` 支持显式 cap（默认 = asset_classes.ACTIVE_CAP）；
  ④ DTO 路径的 `GoalSpec.active_cap` 真正生效（默认 0.10 → 与旧行为一致）。

祖先对照：PM_OPT_SCRIPTS=<祖先>/Optimizer/scripts 且把本文件拷进祖先树 → 必红。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_solver_hardening.py
"""
import dataclasses
import os
import sys

import numpy as np

_SCRIPTS = os.path.abspath(os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
_PKG_ROOT = os.path.dirname(_SCRIPTS)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import asset_classes as AC  # noqa: E402
from portfolio_system.callers.base_runner import problem_from_legacy_rows  # noqa: E402
from portfolio_system.core import constraints as C  # noqa: E402
from portfolio_system.core import solver as S  # noqa: E402
from portfolio_system.core.optimizer import LocalConvexOptimizer  # noqa: E402


class _FakeRes:
    def __init__(self, x, success=True):
        self.x = np.asarray(x, dtype=float)
        self.success = bool(success)


def test_max_feasible_return_rejects_invalid_candidates():
    """① 越界/不守恒的候选点必须丢弃（否则回退目标会被污染）。"""
    mu = np.array([0.03, 0.08])
    lower = np.array([0.0, 0.0])
    upper = np.array([0.4, 1.0])
    saved = S.minimize
    try:
        # 违反 bounds（O1 拿到 1.5）
        S.minimize = lambda *a, **k: _FakeRes([0.4, 1.5])
        assert S.max_feasible_return(mu, lower, upper, [0], [1], 0.4, 0.6, n_starts=2) == -1.0
        # 不满足满仓
        S.minimize = lambda *a, **k: _FakeRes([0.2, 0.2])
        assert S.max_feasible_return(mu, lower, upper, [0], [1], 0.4, 0.6, n_starts=2) == -1.0
        # 合法点：0.4/0.6 → ret = 0.4*0.03 + 0.6*0.08
        S.minimize = lambda *a, **k: _FakeRes([0.4, 0.6])
        got = S.max_feasible_return(mu, lower, upper, [0], [1], 0.4, 0.6, n_starts=2)
        assert abs(got - (0.4 * 0.03 + 0.6 * 0.08)) < 1e-12, got
    finally:
        S.minimize = saved


def test_bounds_arrays_honors_asset_upper_lower():
    """② DTO 上界生效；默认 0/1 与旧行为一致。"""
    rows = [{"key": "D1", "name": "虚构债", "mu": 0.03, "vol": 0.05,
             "pool": "domestic", "test_class": "broad"},
            {"key": "O1", "name": "虚构股", "mu": 0.08, "vol": 0.18,
             "pool": "overseas", "test_class": "broad"}]
    problem = problem_from_legacy_rows(rows, {}, dom_w=0.4, ov_w=0.6, target=0.05, rf=0.02)
    lower, upper = C.bounds_arrays(problem)
    assert list(lower) == [0.0, 0.0], lower
    assert list(upper) == [1.0, 1.0], upper

    problem2 = dataclasses.replace(problem, assets=(
        dataclasses.replace(problem.assets[0], upper=0.35, lower=0.10),
        dataclasses.replace(problem.assets[1], upper=0.80),
    ))
    lower2, upper2 = C.bounds_arrays(problem2)
    assert abs(lower2[0] - 0.10) < 1e-12, lower2
    assert abs(upper2[0] - 0.35) < 1e-12, upper2
    assert abs(upper2[1] - 0.80) < 1e-12, upper2


def test_active_cap_map_supports_explicit_cap():
    """③ 上限值可显式注入；默认仍是 asset_classes.ACTIVE_CAP。"""
    assets = [{"key": "AAPL", "test_class": "active"},
              {"key": "D1", "test_class": "broad"}]
    assert C.active_cap_map(assets) == {"AAPL": AC.ACTIVE_CAP}
    assert C.active_cap_map(assets, cap=0.05) == {"AAPL": 0.05}


def _solve_with_cap(cap):
    rows = [{"key": "AAPL", "name": "虚构个股", "mu": 0.12, "vol": 0.25,
             "pool": "overseas", "test_class": "active"},
            {"key": "D1", "name": "虚构债", "mu": 0.03, "vol": 0.05,
             "pool": "domestic", "test_class": "broad"},
            {"key": "O1", "name": "虚构宽基", "mu": 0.06, "vol": 0.14,
             "pool": "overseas", "test_class": "broad"}]
    problem = problem_from_legacy_rows(rows, {}, dom_w=0.4, ov_w=0.6, target=0.05,
                                       rf=0.02, total_assets=1.0e6,
                                       active_cap=cap)
    problem = dataclasses.replace(problem, stage1_starts=60)
    result = LocalConvexOptimizer().optimize(problem)
    return {k: float(v) for k, v in result.weights.items()}


def test_dto_goal_active_cap_is_effective():
    """④ GoalSpec.active_cap 生效：5% 上限下 AAPL 权重不得 >5%，10% 下不得 >10%。"""
    w5 = _solve_with_cap(0.05)
    assert w5["AAPL"] <= 0.05 + 1e-6, w5
    w10 = _solve_with_cap(0.10)
    assert w10["AAPL"] <= 0.10 + 1e-6, w10


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for name, fn in tests:
        try:
            fn()
            print("  OK", name)
            ok += 1
        except AssertionError as exc:
            print("  FAIL", name)
            print("   ", exc)
    print("%d/%d 通过" % (ok, len(tests)))
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
