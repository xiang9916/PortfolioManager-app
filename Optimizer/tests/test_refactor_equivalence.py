#!/usr/bin/env python3
"""等价性门（task-52 / ADR 0003 §3 R7、§7 P1）。

两条断言：
  ① 旧路径 solve_portfolio(...) 与新路径 LocalConvexOptimizer/PresetRunner 在**同一受控桩输入**下
     逐字段相同（json.dumps(sort_keys=True) 相等）；
  ② 单层问题：LocalConvexOptimizer 与 optimize_common.solve_max_sharpe **数学等价**（逐字段相同）——
     证明单层路径真的走了纯求解，而不是整段转发 solve_portfolio。
受控桩数据全部虚构，不联网。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_refactor_equivalence.py
祖先对照：PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts（本文件不硬写 ../scripts）
"""

import json
import os
import sys

import numpy as np

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_common as OC                      # noqa: E402
import optimize_portfolio as OP                   # noqa: E402
import params as P                                # noqa: E402
from portfolio_system.core import engine as _engine  # noqa: E402
from portfolio_system.core.sleeve_params import SleeveUniverse  # noqa: E402
from portfolio_system.callers.base_runner import RunRequest, problem_from_legacy_rows  # noqa: E402
from portfolio_system.callers.preset_runner import PresetRunner  # noqa: E402
from portfolio_system.core.optimizer import LocalConvexOptimizer  # noqa: E402
from portfolio_system.models.serialization import payload_from_legacy, result_payload  # noqa: E402

DOM_W, OV_W, TOTAL, RF = 0.25, 0.75, 1_000_000.0, 0.02
TARGET = 0.16
#: 虚构资产：两只袖 + 一只境内债 + 一只高收益腿
ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_US_CORE", "name": "虚构美股袖", "mu": 0.06, "vol": 0.12,
     "pool": "overseas", "test_class": "broad"},
    {"key": "D_GREATER_CN", "name": "虚构大中华袖", "mu": 0.05, "vol": 0.14,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_BTC", "name": "虚构高收益腿", "mu": 0.18, "vol": 0.35,
     "pool": "overseas", "test_class": "broad"},
]
CORR = {("O_US_CORE", "O_BTC"): 0.2}
#: P3-b2：core 两层引擎要的"外部数据表"由装配层显式传参（core 不 import params）。
UNIVERSE = SleeveUniverse(
    us_internal=P.US_INTERNAL, us_internal_corr=P.US_INTERNAL_CORR,
    jp_internal=P.JP_INTERNAL, jp_internal_corr=P.JP_INTERNAL_CORR,
    gc_internal=P.GC_INTERNAL, gc_internal_corr=P.GC_INTERNAL_CORR,
    sp500_internal_corr=OP.SP500_INTERNAL_CORR,
    sp500_internal_corr_default=OP.SP500_INTERNAL_CORR_DEFAULT,
    pool_specs=OP._SLEEVE_POOL_SPECS,
    internal_pool_keys=tuple(P.US_INTERNAL) + tuple(P.JP_INTERNAL) + tuple(P.GC_INTERNAL),
)
EXTRACT = {"pool_mode": "fixture", "domestic_weight": DOM_W, "overseas_weight": OV_W,
           "domestic_value": TOTAL * DOM_W, "overseas_value": TOTAL * OV_W,
           "total_value": TOTAL, "rf": RF,
           "us_equity": {"total_value": TOTAL * OV_W,
                         "holdings": [{"ticker": "UA", "name": "虚构",
                                       "value_cny": TOTAL * OV_W, "weight": 1.0}]}}


def _stub_round(assets_in, corr_pairs, **kw):
    """受控桩：把**调用参数**编码进结果，这样任何装配走样（目标/rf/池权重）都会露出来。"""
    n = len(assets_in)
    w = np.array([0.25] + [0.0] * (n - 2) + [0.75])
    ret = float(kw["target"]) + float(kw["rf"]) / 10.0
    vol = 0.10 + float(kw["dom_w"]) / 100.0
    warns = kw.get("warn")
    if warns is not None:
        warns.append(OP._Warn("虚构桩告警（编码 rf=%.6f）" % float(kw["rf"]), "other"))
    return {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": w,
        "stage1_ret": ret, "stage1_vol": vol, "stage1_ret_hint": ret,
        "stage1_sharpe": (ret - RF) / vol,
        "mu": np.array([a["mu"] for a in assets_in]),
        "internal": None, "internal_tickers": None,
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": ret, "vol": vol, "sharpe": (ret - RF) / vol,
                  "floor": ret - 1.96 * vol},
        "target": float(kw["target"]),
        "warnings": list(warns or []),
    }


def _with_stub(fn):
    # P3-b2：实现已搬进 core/engine.py —— 两条路径都要打桩，否则新路径会跑真求解器：
    #   ① OP._solve_once  → 旧薄壳路径（OP.solve_portfolio 的 hooks）
    #   ② engine.solve_once → DTO 路径（core solve_portfolio 的默认 hooks，调用时解析模块名）
    original = OP._solve_once
    original_core = _engine.solve_once
    OP._solve_once = _stub_round
    _engine.solve_once = lambda assets_in, corr_pairs, **kw: _stub_round(assets_in, corr_pairs, **kw)
    try:
        return fn()
    finally:
        OP._solve_once = original
        _engine.solve_once = original_core


def _old_path():
    warnings = []
    out = OP.solve_portfolio([dict(a) for a in ASSETS], dict(CORR),
                             dom_w=DOM_W, ov_w=OV_W, target=TARGET, rf=RF,
                             extract=dict(EXTRACT), total_assets=TOTAL,
                             warnings=warnings, log=None, max_sleeve_iter=4,
                             stage1_starts=40, stage2_starts=20)
    return out, warnings


def _new_path():
    problem = problem_from_legacy_rows([dict(a) for a in ASSETS], dict(CORR),
                                       dom_w=DOM_W, ov_w=OV_W, target=TARGET, rf=RF,
                                       total_assets=TOTAL, extract=dict(EXTRACT),
                                       max_sleeve_iter=4, stage1_starts=40, stage2_starts=20,
                                       sleeve_universe=UNIVERSE)
    runner = PresetRunner(LocalConvexOptimizer())
    request = RunRequest(assets=problem.assets, target_return=TARGET, rf=RF,
                         domestic_weight=DOM_W, overseas_weight=OV_W,
                         corr_pairs=dict(CORR), total_assets=TOTAL, extract=dict(EXTRACT),
                         max_sleeve_iter=4, stage1_starts=40, stage2_starts=20,
                         sleeve_universe=UNIVERSE)
    return runner.run(request), problem


def test_old_and_new_paths_agree_field_by_field():
    """① 旧路径 vs 新路径（含袖 → P0 委托同一引擎）逐字段相同。"""
    old, old_warnings = _with_stub(_old_path)
    new_outcome, _problem = _with_stub(_new_path)
    a = payload_from_legacy(old, old_warnings)
    b = result_payload(new_outcome.result)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), \
        "旧/新不一致\nold=%s\nnew=%s" % (json.dumps(a, sort_keys=True, ensure_ascii=False),
                                          json.dumps(b, sort_keys=True, ensure_ascii=False))
    # 桩把 rf 编码进告警文本 → 装配必须逐字带上（防止 rf 被默认值吞掉）
    assert any(("rf=%.6f" % RF) in m for m in b["warnings"]), b["warnings"]


def test_single_layer_matches_solve_max_sharpe():
    """② 单层问题：LocalConvexOptimizer 必须与 solve_max_sharpe 数学等价（真纯求解路径）。"""
    rows = [
        {"key": "A", "name": "虚构A", "mu": 0.05, "vol": 0.10,
         "pool": "domestic", "test_class": "broad"},
        {"key": "B", "name": "虚构B", "mu": 0.09, "vol": 0.18,
         "pool": "overseas", "test_class": "broad"},
        {"key": "C", "name": "虚构C", "mu": 0.12, "vol": 0.25,
         "pool": "overseas", "test_class": "broad"},
    ]
    corr = {("A", "B"): 0.1, ("B", "C"): 0.3, ("A", "C"): 0.05}
    problem = problem_from_legacy_rows(rows, corr, dom_w=0.30, ov_w=0.70, target=0.08,
                                       rf=0.02, total_assets=1000.0)
    assert problem.is_single_layer, problem.sleeve_keys
    result = LocalConvexOptimizer().optimize(problem)

    keys = [r["key"] for r in rows]
    mu = np.array([r["mu"] for r in rows])
    from portfolio_system.core import linalg as L
    cov = L.build_cov([{'key': r['key'], 'vol': r['vol']} for r in rows], corr)
    lower, upper = OC.stage1_bounds(rows)
    dom = [0]
    ov = [1, 2]
    x, ret, vol, sharpe = OC.solve_max_sharpe(mu, cov, lower, upper, dom, ov, 0.30, 0.70,
                                              0.08, n_starts=problem.stage1_starts, rf=0.02)
    expected_weights = {k: float(v) for k, v in zip(keys, list(x))}
    expected_stats = {"ret": float(ret), "vol": float(vol), "sharpe": float(sharpe),
                      "floor": float(ret) - 1.96 * float(vol)}
    assert json.dumps({k: round(v, 12) for k, v in result.weights.items()}, sort_keys=True) == \
        json.dumps({k: round(v, 12) for k, v in expected_weights.items()}, sort_keys=True), \
        (result.weights, expected_weights)
    assert json.dumps({k: round(v, 12) for k, v in result.stats.items()}, sort_keys=True) == \
        json.dumps({k: round(v, 12) for k, v in expected_stats.items()}, sort_keys=True), \
        (result.stats, expected_stats)


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  OK " + name)
        except AssertionError as exc:
            failed += 1
            print("  FAIL " + name + ": " + str(exc)[:400])
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc)[:400])
    print("\n%d/%d 通过" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
