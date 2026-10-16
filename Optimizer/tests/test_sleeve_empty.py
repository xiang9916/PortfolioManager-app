#!/usr/bin/env python3
"""「袖全空不得判定收敛」回归测试（不联网、全虚构参数）。

背景（2026-10-15 用户实测发现，详见 optimize_portfolio.SLEEVE_DAMPING 下方的说明）：
目标设到高于"当前持仓篮子模型"能支撑的水平时，第一轮阶段一会判定目标不可行并回退到
最大可行收益 —— 回退解是"各池全押最高收益标的"的角点组合，通常不给袖任何权重，
于是阶段二整段跳过、袖参数"没有变化"（delta=0）→ 循环把角点误判成固定点并交付
（收益低于用户设的目标、波动大幅抬升）。只要角点恰好残留一丝袖权重，阶段二就会跑、
delta 就非 0、循环就能继续 —— 交付结果一度取决于那一丝权重是否恰好为零。

覆盖：
  1. `test_empty_sleeve_fallback_round_must_not_converge`：**确定性复现**病态形态
     （把 _solve_once 换成受控桩：第一轮回退且三袖全空，第二轮回退消失），断言不得在第
     一轮收敛、必须按候选池重新播种后再迭代、且第二轮拿到的确实是播种后的参数。
     这个用例在修复前必然失败。
  2. 真实求解器（虚构袖内池）上的整体不变量：正常路径不出回退轮；目标可行时必须交付
     ≥ 目标；目标真的不可行时必须显式说明而不是安静交付角点。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_empty.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import optimize_portfolio as OP  # noqa: E402

# ---- 虚构参数（与任何真实持仓无关）----
DOM_W, OV_W = 0.25, 0.75          # 刻意避开任何真实拆分比例
TOTAL = 1_000_000.0
RF = 0.02
TARGET_OK = 0.16          # 高于初值模型上限（0.149），但重新播种后可达
TARGET_IMPOSSIBLE = 0.30  # 任何模型下都不可达

# 虚构袖内候选池：各两只
FAKE_US = {
    "UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.10},
    "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.18},
}
FAKE_JP = {
    "JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.12},
    "JD": {"name": "虚构日股D", "mu": 0.08, "vol": 0.15},
}
FAKE_CORR = {("UA", "UB"): 0.3, ("JC", "JD"): 0.3}

# 第一阶段大类：境内一只低波债、境外一只高收益资产 + 两只袖。
# 袖的**初值** μ 故意压得很低（0.02）—— 模拟"当前持仓篮子"远不如袖内候选池的情形。
FAKE_ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_US_CORE", "name": "虚构美股核心袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_JP_EQ", "name": "虚构日本权益袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_BTC", "name": "虚构高收益资产", "mu": 0.20, "vol": 0.40,
     "pool": "overseas", "test_class": "broad"},
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


class patched_pools:
    """把模块级的袖内候选池换成虚构池（用完还原），让本文件完全自包含。

    用 setattr/删除而不是直接赋值：这样在被回退到旧版本（没有 _SLEEVE_POOL_SPECS）时，
    本文件仍然会以**行为断言**失败，而不是抛 AttributeError —— 失败信息才有诊断价值。
    """

    def __enter__(self):
        self.saved = (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        OP.US_INTERNAL = FAKE_US
        OP.US_INTERNAL_CORR = FAKE_CORR
        OP.JP_INTERNAL = FAKE_JP
        OP.JP_INTERNAL_CORR = FAKE_CORR
        OP._SLEEVE_POOL_SPECS = {
            "O_US_CORE": (FAKE_US, FAKE_CORR, 19),
            "O_JP_EQ": (FAKE_JP, FAKE_CORR, 31),
        }
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         saved_specs) = self.saved
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
            max_sleeve_iter=kw.pop("max_sleeve_iter", 5),
            stage1_starts=kw.pop("stage1_starts", 60),
            stage2_starts=kw.pop("stage2_starts", 30), **kw)


# ---------------------------------------------------------------- 1. 确定性复现（核心回归）

KEYS = [a["key"] for a in FAKE_ASSETS]


def _fallback_round(assets_in):
    """受控桩：回退轮 —— 解析出的角点是"境内全押债 + 境外全押高收益"，三袖全空。"""
    return {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": np.array([0.25, 0.0, 0.0, 0.75]),
        "stage1_ret": 0.1575, "stage1_vol": 0.3003, "stage1_sharpe": 0.4578,
        "internal": None, "internal_tickers": None,
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": 0.1575, "vol": 0.3003, "sharpe": 0.4578, "floor": 0.1575 - 1.96 * 0.3003},
        "target": 0.157499,          # < 请求的 0.16 → 说明发生了回退
        "warnings": [],
    }


def _feasible_round(assets_in):
    """受控桩：可行轮 —— 袖被解出（100% 虚构美股B / 虚构日股D），目标不再回退。"""
    return {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": np.array([0.25, 0.60, 0.10, 0.05]),
        "stage1_ret": 0.1710, "stage1_vol": 0.0898, "stage1_sharpe": 1.682,
        "internal": (np.array([0.0, 1.0]), 0.1710, 0.0898, 1.682, None, ["UA", "UB"]),
        "internal_tickers": ["UA", "UB"],
        "japan_selection": {"ret": FAKE_JP["JD"]["mu"], "vol": FAKE_JP["JD"]["vol"]},
        "gc_selection": None,
        "final": {"ret": 0.1710, "vol": 0.0898, "sharpe": 1.682, "floor": 0.1710 - 1.96 * 0.0898},
        "target": 0.16,              # 不再回退
        "warnings": [],
    }


def test_empty_sleeve_fallback_round_must_not_converge():
    """核心回归：回退轮袖全空时，不得在第 1 轮就判定收敛。

    修复前的行为：delta 恒为 0 → 直接 break → 迭代 1 轮、交付收益 0.1575 < 目标 0.16，
    且 history 里没有 reseeded 字段（本用例会以行为断言失败）。
    """
    calls = []          # 每次 _solve_once 收到的袖参数（用于核对是否用了乐观播种值）

    def stub(assets_in, corr_pairs, **kw):
        calls.append({a["key"]: (float(a["mu"]), float(a["vol"]))
                      for a in assets_in if a["key"] in OP.SLEEVE_KEYS})
        return _fallback_round(assets_in) if len(calls) == 1 else _feasible_round(assets_in)

    with patched_pools():
        original = OP._solve_once
        OP._solve_once = stub
        try:
            res = OP.solve_portfolio(
                [dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W, target=TARGET_OK,
                rf=RF, extract=EXTRACT, total_assets=TOTAL, warnings=[], log=None,
                max_sleeve_iter=5, stage1_starts=60, stage2_starts=30)
        finally:
            OP._solve_once = original

    first = res["sleeve_history"][0]
    assert first.get("fallback") is True, first
    assert first.get("reseeded") == ["O_US_CORE", "O_JP_EQ"], f"空袖必须按候选池重新播种：{first}"
    assert res["sleeve_iterations"] >= 2, "回退轮不得直接结束迭代（这就是那个 bug）"
    assert res["final"]["ret"] >= TARGET_OK - 1e-4, res["final"]

    # 第二轮必须真的拿到"乐观播种后的"袖参数（初值都是 0.02），而不是原样继续
    assert len(calls) >= 2, calls
    assert calls[1]["O_US_CORE"][0] > 0.20, f"第二轮的美股袖 μ 仍是旧值：{calls[1]}"
    assert calls[1]["O_JP_EQ"][0] > 0.05, f"第二轮的日本袖 μ 仍是旧值：{calls[1]}"


def test_reseed_gets_a_chance_even_with_tiny_budget():
    """重播种后必须有一轮机会被检验：预算=1 时也要交付 ≥ 目标。

    否则（无额外预算）会丢弃刚算出的重播种，并把**可达**的目标误报成
    "在收敛后的模型下仍不可行、请下调目标"。
    """
    calls = []

    def stub(assets_in, corr_pairs, **kw):
        calls.append(1)
        return _fallback_round(assets_in) if len(calls) == 1 else _feasible_round(assets_in)

    with patched_pools():
        original = OP._solve_once
        OP._solve_once = stub
        try:
            res = OP.solve_portfolio(
                [dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W, target=TARGET_OK,
                rf=RF, extract=EXTRACT, total_assets=TOTAL, warnings=[], log=None,
                max_sleeve_iter=1, stage1_starts=60, stage2_starts=30)
        finally:
            OP._solve_once = original

    assert len(calls) >= 2, f"重播种后没有再迭代一轮（只跑了 {len(calls)} 轮）"
    assert res["final"]["ret"] >= TARGET_OK - 1e-4, res["final"]
    assert not any("仍不可行" in w for w in res["warnings"]), res["warnings"]


def test_unresolved_weighted_sleeve_is_not_treated_as_delivered():
    """阶段一给了袖权重、阶段二却没跑出结果时：final 只是**模型**统计，不得算收敛。

    这种轮次的 model_gap 恒为 0，会伪装成"模型与交付完全一致"而拿到最优评分。
    """
    def stub(assets_in, corr_pairs, **kw):
        r = _feasible_round(assets_in)
        r["internal"] = None                 # 阶段二失败（袖内池为空 / 全部起点失败）
        r["internal_tickers"] = None
        r["stage1_ret"] = r["final"]["ret"]  # 模型统计 = 阶段一统计 → model_gap 恒为 0
        r["stage1_vol"] = r["final"]["vol"]
        # 日本袖返回"与当前假设相同"的参数 → delta 恰好为 0（这正是该轮伪装成固定点的方式）
        r["japan_selection"] = {"ret": 0.02, "vol": 0.10}
        return r

    with patched_pools():
        original = OP._solve_once
        OP._solve_once = stub
        try:
            res = OP.solve_portfolio(
                [dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W, target=TARGET_OK,
                rf=RF, extract=EXTRACT, total_assets=TOTAL, warnings=[], log=None,
                max_sleeve_iter=2, stage1_starts=60, stage2_starts=30)
        finally:
            OP._solve_once = original

    assert res["sleeve_converged"] is False, "交付没发生的一轮不得算收敛"
    joined = " ".join(res["warnings"])
    assert "未完全收敛" in joined, res["warnings"]
    # 措辞必须准确：是"有袖没被解出"（该袖权重按大类模型计价），不是"整轮都没交付"
    assert "没被阶段二解出" in joined, joined
    assert "阶段二没有产出篮子" not in joined, joined


def test_exhausted_budget_must_not_blame_the_target():
    """预算用尽而退出时：不得断言"在收敛后的模型下仍不可行"，也不得建议下调目标。

    这种形态下末轮是"回退 + 袖被解出"、重播种无变化，只是阻尼推进还没来得及被检验 ——
    真实原因可能是迭代预算不足（把 max_sleeve_iter 调大就能达标），不是目标不可行。
    """
    def stub(assets_in, corr_pairs, **kw):
        r = _feasible_round(assets_in)          # 袖都被解出（阶段二有产出）
        r["target"] = 0.157499                  # 但每轮都判目标不可行（回退轮）
        r["stage1_ret"] = r["final"]["ret"] = 0.1575
        r["stage1_vol"] = r["final"]["vol"] = 0.3003
        return r

    with patched_pools():
        original = OP._solve_once
        OP._solve_once = stub
        try:
            res = OP.solve_portfolio(
                [dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W, target=TARGET_OK,
                rf=RF, extract=EXTRACT, total_assets=TOTAL, warnings=[], log=None,
                max_sleeve_iter=1, stage1_starts=60, stage2_starts=30)
        finally:
            OP._solve_once = original

    joined = " ".join(res["warnings"])
    assert "预算" in joined, joined
    assert "请下调目标" not in joined, joined
    assert res["sleeve_converged"] is False


# ---------------------------------------------------------------- 2. 真实求解器上的不变量


def test_premise_initial_model_cannot_reach_target():
    """前提检查（纯算术，与实现无关）：

    初值模型上限 = 0.25×0.03（境内最高 μ）+ 0.75×0.20（境外最高 μ）= 0.1575 < 0.16；
    而把美股袖按"它自己能达到的篮子"（虚构美股B，μ=0.30）算，上限 0.2325 ≥ 0.16。
    """
    assert DOM_W * 0.03 + OV_W * 0.20 < TARGET_OK
    assert DOM_W * 0.03 + OV_W * FAKE_US["UB"]["mu"] >= TARGET_OK


def test_feasible_target_is_honoured():
    """目标在"重新播种后的模型"下可行时，交付收益必须 ≥ 目标。"""
    res = solve(TARGET_OK)
    assert res is not None
    assert res["final"]["ret"] >= TARGET_OK - 1e-4, res["final"]
    # 2026-10-16（P0）：目标可达但阶段一当前假设够不到时，会有回退轮；只要最终交付达标，
    # 组合层兜底复核不介入（低目标/已达标档位与修复前逐字节一致）。回退轮必须被记录。
    assert any(h["fallback"] for h in res["sleeve_history"]), res["sleeve_history"]


def test_impossible_target_is_reported_not_silently_delivered():
    """目标真的不可行时：交付最大可行收益，但必须显式说明"低于目标"。"""
    res = solve(TARGET_IMPOSSIBLE)
    assert res is not None
    assert res["final"]["ret"] < TARGET_IMPOSSIBLE - 1e-4
    # 文案有两支：真不可行（"收敛后的模型下仍不可行"）与预算用尽（"受迭代预算限制"）——
    # 两者都必须显式说明"目标不可行 + 已按最大可行收益交付（低于目标）"。
    hits = [w for w in res["warnings"]
            if "不可行" in w and "低于目标" in w and "最大可行收益" in w]
    assert hits, f"必须显式报告目标不可行，实际提示：{res['warnings']}"


def test_feasible_target_path_has_no_fallback_round():
    """正常路径（目标低于初值模型上限）不受本次修复影响：不得出现回退轮。"""
    res = solve(0.10)
    assert res is not None
    assert res["final"]["ret"] >= 0.10 - 1e-4
    assert not any(h["fallback"] for h in res["sleeve_history"]), res["sleeve_history"]


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
