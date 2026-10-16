#!/usr/bin/env python3
"""「回退角点不得被当成交付」回归测试（P0-CORNER，不联网、全虚构参数、受控桩）。

背景（2026-10-15 用户实测的两分支形态，见 AGENTS.md §12）：
目标高于"当前持仓篮子模型"能支撑的水平时，阶段一会判目标不可行并回退到最大可行收益 ——
回退解是"各池全押最高收益标的"的角点组合，通常不给袖任何权重（或只残留一丝）。
修复前：只要 delta==0（角点里袖参数"没变化"）就被判收敛，于是**收益低于目标的角点**被交付。

本文件用受控桩**确定性地**复现两个分支：
  ① 回退角点里三只袖权重**恰好全为 0**；
  ② 角点里**残留一丝**袖权重（1e-5，> 1e-6 的阶段二早退阈值）。
断言：两分支都**不得在第 1 轮判定收敛**；目标可达时必须继续迭代并交付 ≥ 目标；
      目标确实不可达时必须显式报告（不可行 + 最大可行收益 + 迭代预算是否用尽）。
前提：两分支的权重数组只差一个 1e-5 的项 —— 结论不得依赖那一丝。

运行（本环境无 pytest，逐文件直跑）：
  Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_corner.py
祖先对照（d7afb51 = 868e122 的父提交 = 2026-10-15 修复之前，应当失败）：
  PM_OPT_SCRIPTS=<ancestor>/Optimizer/scripts PYTHONPATH=<ancestor>/Optimizer/scripts \
    Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_corner.py
"""

import os
import sys

import numpy as np

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402

DOM_W, OV_W = 0.25, 0.75
TOTAL = 1_000_000.0
RF = 0.02
TARGET = 0.16              # 可达（第 2 轮桩交付 0.18）
FALLBACK_TARGET = 0.130499  # < TARGET → 回退轮（角点交付 0.1575 < TARGET）
TRACE = 1e-5               # 分支②的"那一丝"袖权重

# 虚构袖内候选池（三只袖各一到两只）
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

EXTRACT = {
    "pool_mode": "fixture",
    "domestic_weight": DOM_W, "overseas_weight": OV_W,
    "domestic_value": TOTAL * DOM_W, "overseas_value": TOTAL * OV_W,
    "total_value": TOTAL, "rf": RF,
    "us_equity": {"total_value": TOTAL * OV_W,
                  "holdings": [{"ticker": "UA", "name": "虚构",
                                "value_cny": TOTAL * OV_W, "weight": 1.0}]},
}


class patched_pools:
    """把模块级袖内候选池换成虚构池（用完还原）。"""

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


def _corner_weights(us=0.0, jp=0.0, gc=0.0):
    """回退角点：境内全押债、境外全押高收益资产，三只袖接近全空。"""
    btc = OV_W - (us + jp + gc)
    return np.array([DOM_W, us, jp, gc, btc])


def corner_round(assets_in, weights, fell_back=True, jp_resolved=False):
    """受控桩的一轮：袖要么全空、要么残留一丝（japan_selection 非空但参数与假设相同 → delta=0）。"""
    r = {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": weights,
        "stage1_ret": 0.1710, "stage1_vol": 0.0898, "stage1_sharpe": 1.682,
        "mu": np.array([a["mu"] for a in assets_in]),
        "internal": None, "internal_tickers": None,
        "japan_selection": ({"ret": 0.02, "vol": 0.10} if jp_resolved else None),
        "gc_selection": None,
        "final": {"ret": 0.1710, "vol": 0.0898, "sharpe": 1.682, "floor": 0.1710 - 1.96 * 0.0898},
        "target": (FALLBACK_TARGET if fell_back else TARGET),
        "warnings": [],
    }
    if fell_back:
        r["stage1_ret"] = r["final"]["ret"] = 0.1575
        r["stage1_vol"] = r["final"]["vol"] = 0.3003
        r["stage1_sharpe"] = r["final"]["sharpe"] = 0.4578
        r["final"]["floor"] = 0.1575 - 1.96 * 0.3003
    return r


def feasible_round(assets_in):
    """第 2 轮起：不回退、袖全空（无权重）、交付 0.18 ≥ TARGET、delta=0 → 应当收敛。"""
    w = _corner_weights()
    return {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": w,
        "stage1_ret": 0.18, "stage1_vol": 0.30, "stage1_sharpe": 0.5333,
        "mu": np.array([a["mu"] for a in assets_in]),
        "internal": None, "internal_tickers": None,
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": 0.18, "vol": 0.30, "sharpe": 0.5333, "floor": 0.18 - 1.96 * 0.30},
        "target": TARGET,
        "warnings": [],
    }


def make_stub(pick_weights, jp_resolved_first=False, feasible_after_first=True):
    state = {"n": 0}

    def stub(assets_in, corr_pairs=None, *, warn=None, **kw):
        state["n"] += 1
        if state["n"] == 1:
            return corner_round(assets_in, pick_weights, fell_back=True,
                                jp_resolved=jp_resolved_first)
        if feasible_after_first:
            return feasible_round(assets_in)
        return corner_round(assets_in, pick_weights, fell_back=True,
                            jp_resolved=jp_resolved_first)
    return stub


def run(stub, max_sleeve_iter=4):
    with patched_pools():
        original = OP._solve_once
        OP._solve_once = stub
        try:
            return OP.solve_portfolio([dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W,
                                      target=TARGET, rf=RF, extract=EXTRACT, total_assets=TOTAL,
                                      warnings=[], log=None, max_sleeve_iter=max_sleeve_iter,
                                      stage1_starts=40, stage2_starts=20)
        finally:
            OP._solve_once = original


def test_premise_two_branches_differ_only_by_a_trace():
    """前提（纯算术）：两分支只差一个 1e-5 的袖权重项。"""
    a = _corner_weights()
    b = _corner_weights(jp=TRACE)
    diff = np.abs(a - b)
    # 只有两项变化：袖的那一丝 + 被它挤掉的现金腿（其余全相等）
    assert int((diff > 0).sum()) <= 2, diff
    assert float(diff.max()) <= TRACE + 1e-12, diff
    assert float(a[2]) == 0.0 and float(b[2]) > 1e-6, (a, b)


def test_corner_all_sleeves_empty_must_not_converge():
    """分支①：三袖权重恰好全为 0 —— delta=0 不得在第 1 轮判收敛。"""
    res = run(make_stub(_corner_weights()))
    assert res is not None
    # 行为断言（祖先对照只看这些；字段缺失不得混进失败原因）
    assert res["sleeve_iterations"] >= 2, res["sleeve_history"]
    assert float(res["final"]["ret"]) >= TARGET - 1e-4, res["final"]
    assert res["sleeve_converged"] is True, res["warnings"]
    first = res["sleeve_history"][0]
    if "fallback" in first:                       # 审计字段只在修复后的代码里存在
        assert first["fallback"] is True, first
        assert float(first["delta"]) == 0.0, first   # 角点里"袖参数没有变化"


def test_corner_with_trace_sleeve_weight_must_not_converge():
    """分支②：角点里残留一丝袖权重（1e-5）—— 结论不得与分支①不同。"""
    res = run(make_stub(_corner_weights(jp=TRACE), jp_resolved_first=True))
    assert res is not None
    assert res["sleeve_iterations"] >= 2, res["sleeve_history"]
    assert float(res["final"]["ret"]) >= TARGET - 1e-4, res["final"]
    assert res["sleeve_converged"] is True, res["warnings"]
    first = res["sleeve_history"][0]
    if "fallback" in first:
        assert first["fallback"] is True, first
        assert float(first["delta"]) == 0.0, first


def test_trace_weight_does_not_change_the_verdict():
    """两分支必须给出同一结论：都不塌成角点、都交付 ≥ 目标。"""
    r1 = run(make_stub(_corner_weights()))
    r2 = run(make_stub(_corner_weights(jp=TRACE), jp_resolved_first=True))
    for label, r in (("empty", r1), ("trace", r2)):
        assert r["sleeve_iterations"] >= 2, (label, r["sleeve_history"])
        assert float(r["final"]["ret"]) >= TARGET - 1e-4, (label, r["final"])
        assert r["sleeve_converged"] is True, (label, r["warnings"])


def test_impossible_corner_is_not_delivered_as_converged():
    """目标确实不可达：不得判收敛，且交付必须**低于**目标（纯行为断言）。

    提示文案的三点（不可行 / 最大可行收益 / 迭代预算）由
    Optimizer/tests/test_fallback_prompt.py 负责 —— 关注点分离：本文件的祖先对照
    只允许出现行为断言，不掺文案比对与字段缺失。
    """
    res = run(make_stub(_corner_weights(), feasible_after_first=False), max_sleeve_iter=2)
    assert res is not None
    assert res["sleeve_converged"] is False, res["warnings"]
    assert float(res["final"]["ret"]) < TARGET - 1e-4, res["final"]
# ---------------------------------------------------------------- 残留形态（attempt#2 正式用例）
# 2026-10-16 残留：回退角点把**所有**袖压到 0 权重时，旧逻辑只按"袖自己的切点篮子"重新播种；
# 而切点 μ 可能远低于 stage2 能交付的篮子 → 阶段二永远没机会跑，目标被误判成"确实不可行"。
# 以下虚构池刻意做成"US 袖切点低、最高收益篮子高；JP/GC 单标的（切点≈上限，没有余量）"。

LIFT_US = {"UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.04},
           "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.40}}
LIFT_JP = {"JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.12}}
LIFT_GC = {"GCA": {"name": "虚构大中华A", "mu": 0.04, "vol": 0.10}}
LIFT_CORR = {("UA", "UB"): 0.2}
TARGET_LIFT = 0.18          # > 切点口径可达上限（≈0.1575）→ 触发门槛成立
FALLBACK_LIFT = 0.157499


class patched_lift:
    """把三只袖的候选池换成"切点低 / 上限高"的虚构池。"""

    def __enter__(self):
        self.saved = (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
                      getattr(OP, "GC_INTERNAL", None), getattr(OP, "GC_INTERNAL_CORR", None),
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        OP.US_INTERNAL, OP.US_INTERNAL_CORR = LIFT_US, LIFT_CORR
        OP.JP_INTERNAL, OP.JP_INTERNAL_CORR = LIFT_JP, LIFT_CORR
        OP.GC_INTERNAL, OP.GC_INTERNAL_CORR = LIFT_GC, LIFT_CORR
        OP._SLEEVE_POOL_SPECS = {"O_US_CORE": (LIFT_US, LIFT_CORR, 19),
                                 "O_JP_EQ": (LIFT_JP, LIFT_CORR, 31),
                                 "D_GREATER_CN": (LIFT_GC, LIFT_CORR, 43)}
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         OP.GC_INTERNAL, OP.GC_INTERNAL_CORR, saved_specs) = self.saved
        if saved_specs is None:
            del OP._SLEEVE_POOL_SPECS
        else:
            OP._SLEEVE_POOL_SPECS = saved_specs
        return False


def _lift_round(assets_in, ret, vol, target):
    return {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": np.array([DOM_W, 0.0, 0.0, 0.0, OV_W]),
        "stage1_ret": ret, "stage1_vol": vol, "stage1_sharpe": ret / max(vol, 1e-9),
        "mu": np.array([a["mu"] for a in assets_in]),
        # 让"袖内下限 / 乐观播种"这条路径可达（生产路径的 _solve_once 一定带这些字段）
        "lower": np.zeros(len(assets_in)), "upper": np.ones(len(assets_in)),
        "dom_idx": [0], "ov_idx": [1, 2, 3, 4],
        "internal": None, "internal_tickers": None,
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": ret, "vol": vol, "sharpe": ret / max(vol, 1e-9),
                  "floor": ret - 1.96 * vol},
        "target": target,
        "warnings": [],
    }


def _lift_stub(assets_in, corr_pairs=None, *, warn=None, **kw):
    """桩：只有 US 袖的假设 μ 被抬到 ≥ 0.15 时，下一轮才交付达标；否则一直返回回退角点。"""
    us = [float(a["mu"]) for a in assets_in if a["key"] == "O_US_CORE"]
    if us and us[0] >= 0.15:
        return _lift_round(assets_in, 0.19, 0.20, TARGET_LIFT)
    return _lift_round(assets_in, 0.1575, 0.3003, FALLBACK_LIFT)


def run_lift(stub, max_sleeve_iter=4):
    with patched_lift():
        original = OP._solve_once
        OP._solve_once = stub
        try:
            return OP.solve_portfolio([dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W,
                                      target=TARGET_LIFT, rf=RF, extract=EXTRACT,
                                      total_assets=TOTAL, warnings=[], log=None,
                                      max_sleeve_iter=max_sleeve_iter,
                                      stage1_starts=40, stage2_starts=20)
        finally:
            OP._solve_once = original


def test_zero_weight_sleeve_is_seeded_at_the_portfolio_needed_level():
    """0 权重 + 未解出的袖必须按"组合需要的水平"播种；否则目标被误判不可行（角点交付）。

    顺带守住加法式契约：未触发的档位（门槛不成立）里不出现 sleeve_seeded_levels。
    """
    res = run_lift(_lift_stub)
    assert res is not None
    # 抬升的审计键有两种落点（同一契约）：组合层复核抬升 → sleeve_levels；
    # 回退轮里的乐观播种 → sleeve_seeded_levels。
    seeded = [h.get("sleeve_levels") or h.get("sleeve_seeded_levels")
              for h in res["sleeve_history"]
              if h.get("sleeve_levels") or h.get("sleeve_seeded_levels")]
    assert seeded, res["sleeve_history"]
    assert "O_US_CORE" in seeded[0], seeded[0]
    # 播种水平必须"刚好够到目标"（不是该袖的上限 0.30）
    assert 0.15 <= float(seeded[0]["O_US_CORE"]) < 0.30, seeded[0]
    assert res["sleeve_iterations"] >= 2, res["sleeve_history"]
    assert float(res["final"]["ret"]) >= TARGET_LIFT - 1e-4, res["final"]
    assert res["sleeve_converged"] is True, res["warnings"]
    plain = run(make_stub(_corner_weights()))        # 低目标档：不触发
    assert plain is not None
    assert not any(h.get("sleeve_seeded_levels") or h.get("sleeve_levels")
                   for h in plain["sleeve_history"]), plain["sleeve_history"]


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  OK " + name)
        except AssertionError as exc:
            failed += 1
            print("  FAIL " + name + ": " + str(exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
    print("\n%d/%d 通过" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
