#!/usr/bin/env python3
"""「单袖没有余量 ≠ 整条组合不可行」回归测试（不联网、全虚构参数）。

背景（2026-10-16 用户实测，P0）：注入一个新标的后，组合目标被判成
「袖内收益下限无法建立：目标高于 JP/GC 在阶段一上限口径下能提供的水平」→
本轮被标成回退轮（目标被降低、阶段二失去目标约束）→ 交付塌成角点，新标的缺席。
根因：**袖级**事实（某只袖自身够不到目标 / 假设 μ 低于它能交付的篮子）被当成了
**组合级**不可行。目标是否可达只能在组合层判定：某只袖没余量 → 它按自身可达上限参与即可，
目标由其它腿承担；只有当每只袖都抬到自身上限、组合仍够不到目标时才算真不可行。

覆盖：
  1. test_portfolio_reachable_round_must_not_be_marked_fallback（受控桩；修复前必失败）
  2. test_no_room_sleeve_does_not_block_the_round（真实求解器 + 虚构池；修复前必失败）
  3. test_true_portfolio_infeasibility_still_reports（防止"修过头"：真不可行仍要显式报告）
  4. test_warning_items_carry_stable_codes（加法式告警码；修复前必失败）
  5. test_low_target_path_keeps_legacy_shape（低目标档不受影响）

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_no_room.py
祖先对照见 _SCRIPTS 注释（必须用 PM_OPT_SCRIPTS；只改 PYTHONPATH 会跑成当前树）。
"""

import os
import sys

import numpy as np

# 祖先对照的正确做法（2026-10-17 收口）：
#   PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts PYTHONPATH=<祖先树>/Optimizer/scripts \
#     python3 <本文件>        # 并把本文件拷进祖先树（或直接用祖先树里的副本）
# ⚠️ 只改 PYTHONPATH 是**假的祖先对照**：下面这行优先用 PM_OPT_SCRIPTS，
#    会把「祖先全绿」当成结论（verify 首轮就踩过这个坑）。
_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402

# ---- 虚构参数（与任何真实持仓无关；刻意避开真实拆分比例）----
DOM_W, OV_W = 0.30, 0.70
TOTAL = 1_000_000.0
RF = 0.02
TARGET = 0.18            # 组合层可达：靠"有余量的袖 + 高收益腿"承担
TARGET_LOW = 0.10        # 低于前沿 → 正常路径，不该出现任何 P0 新键
TARGET_IMPOSSIBLE = 0.40  # 所有腿都抬到上限也够不到

# 袖内候选池：US 有余量（低波低收益 + 高收益），JP/GC 各自单标的（切点≈上限，没有余量）
FAKE_US = {"UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.04},
           "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.40}}
FAKE_JP = {"JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.12}}
FAKE_GC = {"GD": {"name": "虚构大中华D", "mu": 0.04, "vol": 0.10}}
FAKE_CORR = {("UA", "UB"): 0.3}

# 第一阶段大类：境内一只低波债 + 境外一只高收益腿 + 一只"注入的 active 新标的"（10% 上限）
FAKE_ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_US_CORE", "name": "虚构美股核心袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_JP_EQ", "name": "虚构日本权益袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "D_GREATER_CN", "name": "虚构大中华袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_HIGH", "name": "虚构高收益腿", "mu": 0.20, "vol": 0.40,
     "pool": "overseas", "test_class": "broad"},
    {"key": "T_NEW", "name": "虚构注入标的", "mu": 0.25, "vol": 0.30,
     "pool": "overseas", "test_class": "active"},
]

EXTRACT = {
    "pool_mode": "fixture",
    "domestic_weight": DOM_W,
    "overseas_weight": OV_W,
    "domestic_value": TOTAL * DOM_W,
    "overseas_value": TOTAL * OV_W,
    "total_value": TOTAL,
    "rf": RF,
    "us_equity": {"total_value": TOTAL * OV_W,
                  "holdings": [{"ticker": "UA", "name": "虚构", "value_cny": TOTAL * OV_W,
                                "weight": 1.0}]},
}


_ALL_SPECS = ("O_US_CORE", "O_JP_EQ", "D_GREATER_CN")


class patched_pools:
    """把模块级袖内候选池换成虚构池（用完还原），让本文件完全自包含。

    specs_keys 可只挂一部分袖：模拟"某只袖的候选池在当前运行里用不上"的组合 ——
    此时袖内下限机制无袖可建，旧逻辑就会整轮降级（这正是本用例要复现的路径）。
    """

    def __init__(self, specs_keys=_ALL_SPECS):
        self.specs_keys = tuple(specs_keys)

    def __enter__(self):
        self.saved = (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
                      getattr(OP, "GC_INTERNAL", None), getattr(OP, "GC_INTERNAL_CORR", None),
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        OP.US_INTERNAL, OP.US_INTERNAL_CORR = FAKE_US, FAKE_CORR
        OP.JP_INTERNAL, OP.JP_INTERNAL_CORR = FAKE_JP, FAKE_CORR
        OP.GC_INTERNAL, OP.GC_INTERNAL_CORR = FAKE_GC, FAKE_CORR
        pool_of = {"O_US_CORE": (FAKE_US, FAKE_CORR, 19),
                   "O_JP_EQ": (FAKE_JP, FAKE_CORR, 31),
                   "D_GREATER_CN": (FAKE_GC, FAKE_CORR, 43)}
        OP._SLEEVE_POOL_SPECS = {k: pool_of[k] for k in self.specs_keys}
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         OP.GC_INTERNAL, OP.GC_INTERNAL_CORR, saved_specs) = self.saved
        if saved_specs is None:
            del OP._SLEEVE_POOL_SPECS
        else:
            OP._SLEEVE_POOL_SPECS = saved_specs
        return False


def solve(target, **kw):
    with patched_pools():
        return OP.solve_portfolio(
            [dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W, target=target, rf=RF,
            extract=EXTRACT, total_assets=TOTAL, warnings=[], log=None,
            max_sleeve_iter=kw.pop("max_sleeve_iter", 6),
            stage1_starts=kw.pop("stage1_starts", 60),
            stage2_starts=kw.pop("stage2_starts", 30), **kw)


# ---------------------------------------------------------------- 受控桩

def _model_fields(assets_in):
    """受控桩也要带阶段一模型字段（生产路径的 _solve_once 一定带）——
    组合层复核要用 mu / upper / dom_idx / ov_idx 重算可达上限。"""
    n = len(assets_in)
    upper = np.ones(n)
    for i, a in enumerate(assets_in):
        if a.get("test_class") == "active":       # 单标的上限 10%（与生产口径一致）
            upper[i] = 0.10
    return {"mu": np.array([a["mu"] for a in assets_in]),
            "lower": np.zeros(n), "upper": upper,
            "dom_idx": [0], "ov_idx": list(range(1, n))}


def _fallback_round(assets_in, requested):
    """回退轮：角点组合（各池全押最高收益腿），袖权重全 0，目标被降低。"""
    lowers = requested * 0.83
    out = {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": np.array([DOM_W, 0.0, 0.0, 0.0, OV_W, 0.0]),
        "stage1_ret": lowers, "stage1_vol": 0.30, "stage1_sharpe": lowers / 0.30,
        "internal": None, "internal_tickers": None,
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": lowers, "vol": 0.30, "sharpe": lowers / 0.30,
                  "floor": lowers - 1.96 * 0.30},
        "target": lowers,                 # < 请求目标 → 本轮是回退轮
        "warnings": [],
    }
    out.update(_model_fields(assets_in))
    return out


def _feasible_round(assets_in, requested):
    """可达轮：袖被解出（美股核心袖 100% 虚构美股B），目标就是请求目标。"""
    out = {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": np.array([DOM_W, OV_W, 0.0, 0.0, 0.0, 0.0]),
        "stage1_ret": requested + 0.004, "stage1_vol": 0.16, "stage1_sharpe": 1.15,
        "internal": (np.array([0.0, 1.0]), requested + 0.004, 0.16, 1.15, None, ["UA", "UB"]),
        "internal_tickers": ["UA", "UB"],
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": requested + 0.004, "vol": 0.16, "sharpe": 1.15,
                  "floor": requested + 0.004 - 1.96 * 0.16},
        "target": requested,             # 没有回退
        "warnings": [],
    }
    out.update(_model_fields(assets_in))
    return out


def _us_mu(assets_in):
    for a in assets_in:
        if a["key"] == "O_US_CORE":
            return float(a["mu"])
    return None


def _ceiling(assets_in):
    """组合层可达上限（与生产 _solve_stage1 的判据同源）。"""
    f = _model_fields(assets_in)
    return OP._max_return_given_mu(f["mu"], f["upper"], f["dom_idx"], f["ov_idx"], DOM_W, OV_W)


def _faithful_stub(calls):
    def stub(assets_in, corr_pairs, **kw):
        requested = float(kw.get("target", 0.0))
        calls.append({"us_mu": _us_mu(assets_in), "target": requested})
        if _ceiling(assets_in) >= requested - 1e-9:
            return _feasible_round(assets_in, requested)
        return _fallback_round(assets_in, requested)
    return stub


def test_portfolio_reachable_round_must_not_be_marked_fallback():
    """组合层可达时，本轮不得被标成回退轮（修复前：第 1 轮就是回退轮，靠回退轮播种才救回来）。

    受控桩忠实模拟阶段一：组合层可达上限 ≥ 请求目标才交付，否则回退（回退角点）。
    本档只挂美股核心袖（袖内下限机制无袖可建、旧播种路径也用不上）：
    修复前 → 全程回退、交付低于目标（行为断言失败）；
    修复后 → 收尾做组合层兜底复核，把袖抬到"组合刚好够到目标"的水平并按请求目标重解 → 交付达标。
    """
    calls = []
    stub = _faithful_stub(calls)

    with patched_pools(specs_keys=("O_US_CORE",)):
        original = OP._solve_once
        OP._solve_once = stub
        try:
            res = OP.solve_portfolio(
                [dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W, target=TARGET,
                rf=RF, extract=EXTRACT, total_assets=TOTAL, warnings=[], log=None,
                max_sleeve_iter=6, stage1_starts=60, stage2_starts=30)
        finally:
            OP._solve_once = original

    assert res is not None
    # 交付不得因"某只袖没余量"而降级：必须 ≥ 目标
    assert float(res["final"]["ret"]) >= TARGET - 1e-4, res["final"]
    # 审计：组合层兜底复核确实抬升过袖（抬升水平"刚好够到目标"，不是该袖上限）
    lifted = [h for h in res["sleeve_history"] if h.get("sleeve_levels")]
    assert lifted, res["sleeve_history"]
    assert lifted[0].get("fallback") is False, lifted[0]
    assert 0.15 <= float(lifted[0]["sleeve_levels"]["O_US_CORE"]) < 0.30, lifted[0]
    # 阶段二拿到的必须是**请求目标**（不是被降低后的目标）
    assert any(abs(c["target"] - TARGET) < 1e-9 and (c["us_mu"] or 0) >= 0.19 for c in calls), calls



# ---------------------------------------------------------------- 真实求解器（虚构池）

def _codes(res):
    return [w.get("code") for w in (res.get("warning_items") or [])]


def test_no_room_sleeve_does_not_block_the_round():
    """JP/GC 都没有余量，但组合层可达（有余量的袖 + 高收益腿承担）→ 本轮不得降级成回退轮。"""
    res = solve(TARGET)
    assert res is not None
    assert float(res["final"]["ret"]) >= TARGET - 1e-4, res["final"]
    # 不得把它判成"组合级不可行"
    codes = _codes(res)
    assert "target.sleeve_unreachable" not in codes, codes
    assert "target.infeasible" not in codes, codes
    assert "target.budget_exhausted" not in codes, codes


def test_true_portfolio_infeasibility_still_reports():
    """防止"修过头"：每只袖都抬到自身上限、组合仍够不到目标 → 仍要显式报告、交付低于目标。"""
    res = solve(TARGET_IMPOSSIBLE, max_sleeve_iter=3)
    assert res is not None
    assert res["sleeve_converged"] is False, res["sleeve_history"]
    assert float(res["final"]["ret"]) < TARGET_IMPOSSIBLE - 1e-4, res["final"]
    assert any("不可行" in w for w in res["warnings"]), res["warnings"]


def test_warning_items_carry_stable_codes():
    """加法式告警码：warning_items 每条带稳定 code，message 与 warnings 逐条一致。"""
    res = solve(TARGET_IMPOSSIBLE, max_sleeve_iter=3)
    items = res.get("warning_items")
    assert isinstance(items, list) and items, items
    for it in items:
        assert set(it) == {"code", "message"}, it
        assert isinstance(it["code"], str) and it["code"], it
    assert [it["message"] for it in items] == list(res["warnings"]), items
    assert any(it["code"].startswith("target.") for it in items), items
    assert all(c in getattr(OP, "WARNING_CODES", {}) for c in (it["code"] for it in items)), items


def test_low_target_path_keeps_legacy_shape():
    """低目标档（前沿以内）：不出现任何 P0 新键/新码，交付仍达标。"""
    res = solve(TARGET_LOW)
    assert res is not None
    assert not any(h.get("sleeve_levels") for h in res["sleeve_history"]), res["sleeve_history"]
    assert not any(c.startswith("target.") for c in _codes(res)), _codes(res)
    assert float(res["final"]["ret"]) >= TARGET_LOW - 1e-4, res["final"]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
        else:
            ok += 1
            print(f"  OK {t.__name__}")
    print(f"{ok}/{len(tests)} 通过")
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())

