#!/usr/bin/env python3
"""协方差可观测性（P1，2026-10-17）：缺失相关对审计 + PSD 修复统计。离线、纯虚构。

契约：
  ① `build_broad_cov(..., missing_out=...)` 只**报告**缺失相关对，矩阵与默认路径逐字节一致；
  ② `psd_diff_stats` 描述 PSD 修复（eigmin 前后 / 改写对数 / 最大 Δ），不改变矩阵；
  ③ `project_psd_stats` 与 `project_psd` 数学行为相同（前者只多返回统计）；
  ④ `engine.solve_once` 把缺失相关对写进加法式 `corr_missing` 审计键（无缺失则不出现）。

祖先对照：PM_OPT_SCRIPTS=<祖先>/Optimizer/scripts 且把本文件拷进祖先树 → 必红（无这些 API）。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_cov_diagnostics.py
"""
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

from portfolio_system.core import engine as ENG  # noqa: E402
from portfolio_system.core.linalg import (build_broad_cov, project_psd,  # noqa: E402
                                          project_psd_stats, psd_diff_stats)
from portfolio_system.core.sleeve_params import SleeveUniverse  # noqa: E402

ASSETS = [{"key": "D1", "vol": 0.05}, {"key": "O1", "vol": 0.15}]


def test_missing_out_reports_but_does_not_change_matrix():
    """① 缺失对可审计；矩阵与不传 missing_out 时逐字节一致。"""
    base = build_broad_cov([dict(a) for a in ASSETS], {("D1", "D1"): 1.0})
    missing = []
    got = build_broad_cov([dict(a) for a in ASSETS], {("D1", "D1"): 1.0},
                          missing_out=missing)
    assert missing == [("D1", "O1")], missing
    assert np.array_equal(base, got), "missing_out 不得改变协方差矩阵"

    missing2 = []
    build_broad_cov([dict(a) for a in ASSETS], {("D1", "O1"): 0.2},
                    missing_out=missing2)
    assert missing2 == [], missing2


def test_psd_diff_stats_detects_and_quantifies():
    """② 非 PSD 矩阵：eigmin_before<0、改写对数>0、max_delta>0；PSD 矩阵无需修复。"""
    bad = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    fixed = project_psd(bad)
    stats = psd_diff_stats(bad, fixed)
    assert stats["eigmin_before"] < 0, stats
    assert stats["eigmin_after"] >= -1e-9, stats
    assert stats["changed_pairs"] > 0, stats
    assert stats["max_delta"] > 0, stats

    good = np.eye(3)
    good[0, 1] = good[1, 0] = 0.2
    stats_ok = psd_diff_stats(good, project_psd(good))
    assert stats_ok["changed_pairs"] == 0, stats_ok
    assert stats_ok["max_delta"] == 0.0, stats_ok


def test_project_psd_stats_matches_project_psd():
    """③ 可观测版本与旧函数数学行为一致（只多返回统计）。"""
    rng = np.random.default_rng(7)
    a = rng.normal(size=(6, 6))
    m = (a + a.T) / 2.0
    np.fill_diagonal(m, 1.0)
    old = project_psd(m)
    new, stats = project_psd_stats(m)
    assert np.array_equal(old, new), "project_psd_stats 的结果必须与 project_psd 相同"
    assert set(stats) == {"eigmin_before", "eigmin_after", "changed_pairs", "max_delta"}


def test_solve_once_records_corr_missing():
    """④ 引擎把缺失相关对写进加法式审计键（纯虚构两资产，无袖）。"""
    universe = SleeveUniverse(us_internal={}, us_internal_corr={}, jp_internal={},
                              jp_internal_corr={}, gc_internal={}, gc_internal_corr={})
    assets = [
        {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05, "pool": "domestic"},
        {"key": "O1", "name": "虚构海外股", "mu": 0.08, "vol": 0.18, "pool": "overseas"},
    ]
    warnings = []
    out = ENG.solve_once(assets, {}, dom_w=0.5, ov_w=0.5, target=0.05, rf=0.02,
                         extract={"us_equity": {"holdings": []}}, total_assets=1.0e6,
                         universe=universe, warn=warnings)
    assert out is not None
    assert out.get("corr_missing") == [["D1", "O1"]], out.get("corr_missing")


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
