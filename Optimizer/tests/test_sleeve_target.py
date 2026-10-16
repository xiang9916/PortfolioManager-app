#!/usr/bin/env python3
"""「袖内目标传导」回归测试（P1 方案 A，不联网、全虚构参数）。

背景：O_JP_EQ / D_GREATER_CN 的袖内优化器过去只做"袖内 max 夏普"、看不到组合级目标，
目标高于袖内切点前沿时只能靠阶段一硬顶（袖被丢弃 / 组合退成角点，交付低于目标）。
本文件断言修复后：
  1. 目标高于切点前沿时下限真的生效：袖拿到篮子、floor_met=True、交付 ≥ 目标；
  2. 目标低于前沿时下限恒松弛：输出与修复前逐字节一致（不出现任何下限键）；
  3. 目标超过袖内可达上限时**不硬凑**：显式提示"袖内收益下限无法建立" + 走回退路径；
  4. 下限不可行时 _optimize_within_pool 不静默返回违约篮子（verify F1 加固）；
  5. 审计字段（sleeve_history[*].sleeve_floors / floor_recomputed）只在下限生效时出现。

虚构池：日本袖 = 低波高夏普标的 + 高收益标的；美股袖最高 μ=16%。
  切点口径下组合可达上限 ≈ 0.0075 + 0.75×0.1463 ≈ 0.1172
  → 目标 0.06/0.08/0.10 属"下限恒松弛"；0.12 以上才需要抬日本袖。

运行（本环境无 pytest，逐文件直跑）：
  Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_target.py
修复前对照（应当至少 1 项失败）：
  PM_OPT_SCRIPTS=tmp/agent-team/prefix/task-13 PYTHONPATH=Optimizer/scripts \
    Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_target.py
"""

import os
import sys

import numpy as np

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402

DOM_W, OV_W, TOTAL, RF = 0.25, 0.75, 1_000_000.0, 0.02
T_FRONTIER = 0.08      # 低于切点可达上限（≈0.1172）
T_LIFT = 0.12          # 高于切点可达上限 → 需要抬日本袖
T_UNREACHABLE = 0.60   # 超过任何袖内上限

US = {"UB1": {"name": "虚构美股1", "mu": 0.06, "vol": 0.10},
      "UB2": {"name": "虚构美股2", "mu": 0.16, "vol": 0.14}}
JP = {"JC1": {"name": "虚构日股1", "mu": 0.06, "vol": 0.04},
      "JE1": {"name": "虚构日股2", "mu": 0.35, "vol": 0.45}}
CORR = {("UB1", "UB2"): 0.3, ("JC1", "JE1"): 0.3}
ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_US_CORE", "name": "虚构美股核心袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_JP_EQ", "name": "虚构日本权益袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
]
EXTRACT = {
    "pool_mode": "fixture", "domestic_weight": DOM_W, "overseas_weight": OV_W,
    "domestic_value": TOTAL * DOM_W, "overseas_value": TOTAL * OV_W,
    "total_value": TOTAL, "rf": RF,
    "us_equity": {"total_value": TOTAL * OV_W,
                  "holdings": [{"ticker": "UB1", "name": "虚构",
                                "value_cny": TOTAL * OV_W, "weight": 1.0}]},
}


class patched_pools:
    """把模块级袖内候选池换成虚构池（用完还原）。"""

    def __enter__(self):
        self.saved = (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        OP.US_INTERNAL, OP.US_INTERNAL_CORR = US, CORR
        OP.JP_INTERNAL, OP.JP_INTERNAL_CORR = JP, CORR
        OP._SLEEVE_POOL_SPECS = {"O_US_CORE": (US, CORR, 19), "O_JP_EQ": (JP, CORR, 31)}
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         saved_specs) = self.saved
        if saved_specs is None:
            del OP._SLEEVE_POOL_SPECS
        else:
            OP._SLEEVE_POOL_SPECS = saved_specs
        return False


def run_solve(target, max_sleeve_iter=4):
    with patched_pools():
        return OP.solve_portfolio([dict(a) for a in ASSETS], {}, dom_w=DOM_W, ov_w=OV_W,
                                  target=target, rf=RF, extract=EXTRACT, total_assets=TOTAL,
                                  warnings=[], log=None, max_sleeve_iter=max_sleeve_iter,
                                  stage1_starts=25, stage2_starts=12)


def _floors_in_history(res):
    return [h.get("sleeve_floors") for h in res["sleeve_history"] if h.get("sleeve_floors")]


def test_high_target_lifts_sleeve_and_meets_floor():
    """目标高于切点前沿：日本袖必须被抬升（下限生效）且交付不低于目标。"""
    res = run_solve(T_LIFT)
    assert res is not None
    jp = res["japan_selection"]
    assert jp is not None, "高目标下日本袖不该被丢弃"
    assert jp.get("floor") is not None, "本轮日本袖必须带袖内下限"
    assert jp.get("floor_met") is True, jp
    assert float(jp["ret"]) >= float(jp["floor"]) - 1e-4, jp
    assert float(res["final"]["ret"]) >= T_LIFT - 1e-4, res["final"]
    assert _floors_in_history(res), res["sleeve_history"]


def test_floor_is_slack_below_frontier():
    """目标低于切点前沿：下限恒松弛 —— 不出现任何下限键（= 与修复前逐字节一致的性质口径）。"""
    res = run_solve(T_FRONTIER)
    assert res is not None
    jp = res["japan_selection"]
    assert jp is None or "floor" not in jp, jp
    assert not _floors_in_history(res), res["sleeve_history"]
    assert float(res["final"]["ret"]) >= T_FRONTIER - 1e-4, res["final"]


def test_unreachable_target_is_reported_not_silent():
    """目标超过袖内可达上限：不硬凑，必须显式提示 + 走回退路径（交付低于目标）。"""
    res = run_solve(T_UNREACHABLE, max_sleeve_iter=3)
    assert res is not None
    joined = " ".join(res["warnings"])
    assert "袖内收益下限无法建立" in joined, res["warnings"]
    assert float(res["final"]["ret"]) < T_UNREACHABLE - 1e-4, res["final"]
    assert "低于目标" in joined, res["warnings"]


def test_infeasible_floor_never_returns_violating_basket():
    """F1 加固：下限不可行时不得静默返回违约篮子 —— 必须返回 None 并写明原因。"""
    warns = []
    with patched_pools():
        got = OP._optimize_within_pool({"JE1": {"name": "虚构", "mu": 0.22, "vol": 0.30}},
                                       {}, RF, 19, floor=0.25, warnings=warns, label="O_JP_EQ")
    assert got is None, got
    assert warns and "下限" in warns[0], warns


def test_floor_audit_only_when_binding():
    """审计字段只在下限生效时出现，且 required_mu 高于切点 μ（加法式键的契约）。"""
    res = run_solve(T_LIFT)
    rec = [h["floor_recomputed"] for h in res["sleeve_history"] if h.get("floor_recomputed")]
    assert rec, res["sleeve_history"]
    info = rec[0].get("O_JP_EQ")
    assert info is not None, rec[0]
    assert set(info) == {"required_mu", "tangency_mu", "uncapped_max"}, info
    assert float(info["required_mu"]) > float(info["tangency_mu"]), info
    assert float(info["uncapped_max"]) >= float(info["required_mu"]), info
    assert res["sleeve_converged"] is True, res["warnings"]


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
