#!/usr/bin/env python3
"""「播种轮不得直接判收敛」+「达标轮按交付波动择优」回归测试（task-50；不联网、全虚构、受控桩）。

背景（task-49 诊断）：回退角点里某只袖权重**恰好 0** → 触发 attempt#2 播种（sleeve_seeded_levels）；
播种值本身可能就自洽 → 下一轮 delta 很小、被旧代码判成收敛 → 迭代再没机会把该袖的 μ 推上去，
锁死在更差的固定点（真机实测：同一目标下注入运行的波动显著高于主运行）。
本文件锁定两条契约：
  ① 触发播种的那一轮**不得直接成为收敛终点**：至少再多推一轮让阻尼推进被检验；
     真的无法再推时不报收敛（走既有的预算/未收敛告警）。
  ② 达标轮之间按**同目标下交付组合波动**择小（完整序：达标 → 缺口 → 交付波动 → 模型-交付差 → 轮次号）。

祖先对照见 _SCRIPTS 注释（必须用 PM_OPT_SCRIPTS；只改 PYTHONPATH 会跑成当前树）。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_seeded_convergence.py
"""

import os
import sys

import numpy as np

# 祖先对照的正确做法（2026-10-17 收口）：
#   PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts PYTHONPATH=<祖先树>/Optimizer/scripts \
#     python3 <本文件>
_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402

DOM_W, OV_W, TOTAL, RF = 0.25, 0.75, 1_000_000.0, 0.02
TARGET = 0.16
FALLBACK_TARGET = 0.1305          # < TARGET → 第 1 轮是回退轮
SEED_LEVEL = 0.20                 # 播种水平（虚构）

FAKE_US = {"UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.10},
           "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.18}}
FAKE_JP = {"JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.12}}
FAKE_GC = {"GC": {"name": "虚构大中华A", "mu": 0.10, "vol": 0.20}}
FAKE_CORR = {("UA", "UB"): 0.3}

FAKE_ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_US_CORE", "name": "虚构美股核心袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_JP_EQ", "name": "虚构日本权益袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "D_GREATER_CN", "name": "虚构大中华袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_BTC", "name": "虚构高收益资产", "mu": 0.20, "vol": 0.40,
     "pool": "overseas", "test_class": "broad"},
]
EXTRACT = {"pool_mode": "fixture", "domestic_weight": DOM_W, "overseas_weight": OV_W,
           "domestic_value": TOTAL * DOM_W, "overseas_value": TOTAL * OV_W,
           "total_value": TOTAL, "rf": RF,
           "us_equity": {"total_value": TOTAL * OV_W,
                         "holdings": [{"ticker": "UA", "name": "虚构",
                                       "value_cny": TOTAL * OV_W, "weight": 1.0}]}}

_US_SINK = {}          # 受控桩：本轮"交付篮子"的 US 袖 (μ,σ)


class patched_pools:
    def __enter__(self):
        self.saved = (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
                      getattr(OP, "GC_INTERNAL", None), getattr(OP, "GC_INTERNAL_CORR", None),
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        OP.US_INTERNAL, OP.US_INTERNAL_CORR = FAKE_US, FAKE_CORR
        OP.JP_INTERNAL, OP.JP_INTERNAL_CORR = FAKE_JP, FAKE_CORR
        OP.GC_INTERNAL, OP.GC_INTERNAL_CORR = FAKE_GC, FAKE_CORR
        OP._SLEEVE_POOL_SPECS = {"O_US_CORE": (FAKE_US, FAKE_CORR, 19),
                                 "O_JP_EQ": (FAKE_JP, FAKE_CORR, 31),
                                 "D_GREATER_CN": (FAKE_GC, FAKE_CORR, 43)}
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         OP.GC_INTERNAL, OP.GC_INTERNAL_CORR, saved_specs) = self.saved
        if saved_specs is None:
            del OP._SLEEVE_POOL_SPECS
        else:
            OP._SLEEVE_POOL_SPECS = saved_specs
        return False


def _assumed_us(assets_in):
    """从"被注入袖参数的资产"读回**本轮假设**的 US 袖 (μ,σ)（桩据此制造 delta=0）。"""
    for a in assets_in:
        if a["key"] == "O_US_CORE":
            return (float(a["mu"]), float(a["vol"]))
    return (0.10, 0.15)


def _round(assets_in, *, weights, target, ret, vol, internal, stage1_vol=None):
    return {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": np.array(weights),
        "stage1_ret": ret, "stage1_vol": (vol if stage1_vol is None else stage1_vol),
        "stage1_sharpe": (ret - RF) / vol,
        "mu": np.array([a["mu"] for a in assets_in]),
        "internal": internal, "internal_tickers": (["UA", "UB"] if internal else None),
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": ret, "vol": vol, "sharpe": (ret - RF) / vol, "floor": ret - 1.96 * vol},
        "target": target,
        "warnings": [],
    }


_US_BASKET = (np.array([0.0, 1.0]), 0.30, 0.18, 1.56, None, ["UA", "UB"])


def _run(stub, max_sleeve_iter=4):
    """受控桩：_solve_once / _sleeve_params_us / _compute_sleeve_floors /
    _seed_levels_for_target / _floor_seed 全部替换为确定性桩。"""
    with patched_pools():
        saved = (OP._solve_once, OP._sleeve_params_us, OP._compute_sleeve_floors,
                 OP._seed_levels_for_target, OP._floor_seed)
        OP._solve_once = stub
        OP._sleeve_params_us = lambda internal, tickers: _US_SINK.get("v")
        OP._compute_sleeve_floors = lambda *a, **k: (
            None, {"skipped": "target unreachable at sleeve max",
                   "tangency_mu": {k2: 0.05 for k2 in ("O_US_CORE", "O_JP_EQ", "D_GREATER_CN")},
                   "uncapped_max": {k2: 0.30 for k2 in ("O_US_CORE", "O_JP_EQ", "D_GREATER_CN")}})
        OP._seed_levels_for_target = lambda solved, tan, target, dom_w, ov_w: {"O_US_CORE": SEED_LEVEL}
        OP._floor_seed = lambda key, level, rf: (float(level), 0.15)
        try:
            return OP.solve_portfolio([dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W,
                                      target=TARGET, rf=RF, extract=EXTRACT, total_assets=TOTAL,
                                      warnings=[], log=None, max_sleeve_iter=max_sleeve_iter,
                                      stage1_starts=40, stage2_starts=20)
        finally:
            (OP._solve_once, OP._sleeve_params_us, OP._compute_sleeve_floors,
             OP._seed_levels_for_target, OP._floor_seed) = saved


def _fallback_round(assets_in):
    """第 1 轮：回退角点（三只袖权重**恰好全 0**）→ 触发 reseed + 播种。"""
    return _round(assets_in, weights=[DOM_W, 0.0, 0.0, 0.0, OV_W],
                  target=FALLBACK_TARGET, ret=0.1575, vol=0.3003, internal=None)


def test_seeded_round_is_not_the_convergence_terminal():
    """① 播种轮之后必须真的再推一轮：轮数 ≥ 3，且交付到同目标下波动更小的固定点。"""
    calls = []

    def stub(assets_in, corr_pairs, **kw):
        calls.append(1)
        n = len(calls)
        if n == 1:
            return _fallback_round(assets_in)
        _US_SINK["v"] = _assumed_us(assets_in)          # delta ≡ 0（播种值自洽）
        vol = 0.22 if n == 2 else 0.17                  # 第 2 轮是"旧代码会停"的差固定点
        return _round(assets_in, weights=[DOM_W, 0.0, 0.0, 0.0, OV_W], target=TARGET,
                      ret=TARGET + 0.01, vol=vol, internal=_US_BASKET)

    res = _run(stub)
    assert res is not None
    hist = res["sleeve_history"]
    assert hist and hist[0].get("sleeve_seeded_levels"), hist[0]
    assert res["sleeve_iterations"] >= 3, res["sleeve_iterations"]   # 播种后至少多推一轮
    assert res["sleeve_converged"] is True, res["sleeve_converged"]
    assert float(res["final"]["vol"]) <= 0.18, res["final"]          # 不得停在 0.22 的差固定点


def test_seeding_without_further_room_does_not_claim_convergence():
    """① 播种之后再推的轮次始终无法满足收敛（delta 大）→ 不得报收敛。"""
    def stub(assets_in, corr_pairs, **kw):
        _SINK["n"] = _SINK.get("n", 0) + 1
        if _SINK["n"] == 1:
            return _fallback_round(assets_in)
        us = _assumed_us(assets_in)
        _US_SINK["v"] = (us[0] + 0.05, us[1])            # delta = 0.05 > tol → 永不收敛
        return _round(assets_in, weights=[DOM_W, 0.0, 0.0, 0.0, OV_W], target=TARGET,
                      ret=TARGET + 0.01, vol=0.17, internal=_US_BASKET)

    _SINK.clear()
    res = _run(stub, max_sleeve_iter=1)
    assert res is not None
    assert res["sleeve_converged"] is False, res["sleeve_converged"]
    codes = [w.get("code") for w in (res.get("warning_items") or [])]
    assert ("target.budget_exhausted" in codes
            or any("未完全收敛" in str(w) or "预算" in str(w) for w in res["warnings"])), (
        codes, res["warnings"])


_SINK = {}


def test_meeting_rounds_pick_the_lowest_delivered_volatility():
    """② 同一达标 + 同一模型自洽度内，按交付波动择小（波动最小的那轮轮次号最大）。"""
    calls = []

    def stub(assets_in, corr_pairs, **kw):
        calls.append(1)
        n = len(calls)
        us = _assumed_us(assets_in)
        _US_SINK["v"] = (us[0] + 0.05, us[1])            # delta 大 → 全程不收敛，走 best 择优
        # 三轮**同样达标、同样模型自洽（gap=0）**，只有交付波动不同：
        # 波动最小的那轮在第 3 轮（轮次号最大）—— 旧序（…, 轮次号）会选第 1 轮。
        vol = {1: 0.25, 2: 0.22, 3: 0.17}[min(n, 3)]
        return _round(assets_in, weights=[DOM_W, 0.0, 0.0, 0.0, OV_W], target=TARGET,
                      ret=TARGET + 0.01, vol=vol, internal=_US_BASKET)

    res = _run(stub, max_sleeve_iter=3)
    assert res is not None
    assert res["sleeve_converged"] is False, res["sleeve_converged"]
    assert float(res["final"]["vol"]) <= 0.18, res["final"]     # 旧序会选 vol=0.22 那轮


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  OK " + name)
        except AssertionError as exc:
            failed += 1
            print("  FAIL " + name + ": " + str(exc)[:300])
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc)[:300])
    print("\n%d/%d 通过" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
