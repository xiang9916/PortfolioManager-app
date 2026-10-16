#!/usr/bin/env python3
"""「回退提示逐轮累积」合并回归测试（P3，不联网、全虚构参数、受控桩）。

背景（2026-10-15 P3）：目标不可行时 _solve_stage1 每轮都追加一条
"NN% 目标在当前约束下不可行，已回退到最大可行收益 XX%"——数字随袖参数阻尼推进逐轮变化，
于是逐轮累积（AGENTS.md §12 记为「已知噪声」，实测最多 ~10 行）。
修复：逐轮提示在 solve_portfolio 里被过滤掉，收尾时合并成**一条**，且必须保留三个信息点：
  ① 交付收益低于目标；② 最大可行收益是多少；③ 迭代预算是否用尽。

本文件用受控桩确定性构造多轮回退（桩往 warn 列表里塞与真实代码同格式的逐轮提示），
断言：回退提示行数 == 1；三个信息点都能在那一行里定位；两种预算状态（用尽 / 未用尽）都如实报告。

运行（本环境无 pytest，逐文件直跑）：
  Optimizer/.venv/bin/python3 Optimizer/tests/test_fallback_prompt.py
修复前对照（同一份测试加载修复前的 optimize_portfolio.py，应当至少 1 项失败）：
  PM_OPT_SCRIPTS=tmp/agent-team/prefix/task-8 PYTHONPATH=Optimizer/scripts \
    Optimizer/.venv/bin/python3 Optimizer/tests/test_fallback_prompt.py
"""

import os
import sys

import numpy as np

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402

# 与真实代码同一段文案（修复前没有这个常量，回落到字面量以便"修复前必失败"仍可复现）
_MARK = getattr(OP, "_STAGE1_FALLBACK_MARK", "目标在当前约束下不可行，已回退到最大可行收益")

DOM_W, OV_W = 0.25, 0.75
TOTAL = 1_000_000.0
RF = 0.02
TARGET = 0.16

FAKE_US = {"UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.10},
           "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.18}}
FAKE_JP = {"JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.12},
           "JD": {"name": "虚构日股D", "mu": 0.08, "vol": 0.15}}
FAKE_CORR = {("UA", "UB"): 0.3, ("JC", "JD"): 0.3}

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
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        OP.US_INTERNAL = FAKE_US
        OP.US_INTERNAL_CORR = FAKE_CORR
        OP.JP_INTERNAL = FAKE_JP
        OP.JP_INTERNAL_CORR = FAKE_CORR
        OP._SLEEVE_POOL_SPECS = {"O_US_CORE": (FAKE_US, FAKE_CORR, 19),
                                 "O_JP_EQ": (FAKE_JP, FAKE_CORR, 31)}
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         saved_specs) = self.saved
        if saved_specs is None:
            del OP._SLEEVE_POOL_SPECS
        else:
            OP._SLEEVE_POOL_SPECS = saved_specs
        return False


def _round(assets_in, fell_back=False, sleeves_resolved=True):
    """受控桩的一轮结果；fell_back=True 时 target < 请求目标（回退轮）。"""
    return {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": np.array([0.25, 0.60, 0.10, 0.05]),
        "stage1_ret": 0.1575, "stage1_vol": 0.3003, "stage1_sharpe": 0.4578,
        "internal": ((np.array([0.0, 1.0]), 0.30, 0.18, 1.5, None, ["UA", "UB"])
                     if sleeves_resolved else None),
        "internal_tickers": (["UA", "UB"] if sleeves_resolved else None),
        "japan_selection": ({"ret": 0.05, "vol": 0.12} if sleeves_resolved else None),
        "gc_selection": None,
        "final": {"ret": 0.1575, "vol": 0.3003, "sharpe": 0.4578,
                  "floor": 0.1575 - 1.96 * 0.3003},
        "target": (0.157499 if fell_back else TARGET),
        "warnings": [],
    }


def _noise(value):
    """与 _solve_stage1 完全同格式的逐轮回退提示（数字逐轮变化 → 修复前会累积）。"""
    return "16% " + _MARK + " %.2f%%。" % value


def make_stub(records, fell_back=True, sleeves_resolved=True):
    def stub(assets_in, corr_pairs=None, *, warn=None, **kw):
        records.append(1)
        if warn is not None and fell_back:
            # 与真实 _solve_stage1 一致：只有回退轮才追加逐轮提示
            warn.append(_noise(0.10 + 0.01 * len(records)))
        return _round(assets_in, fell_back=fell_back, sleeves_resolved=sleeves_resolved)
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


def _fallback_lines(res):
    return [w for w in res["warnings"] if "最大可行收益" in w]


def test_multi_round_fallback_merges_to_one_prompt():
    """多轮回退（预算用尽）：逐轮提示必须被合并，warnings 里只剩这一条。"""
    records = []
    res = run(make_stub(records), max_sleeve_iter=4)
    assert len(records) >= 3, records
    assert len(res["warnings"]) == 1, res["warnings"]
    assert "已用尽" in res["warnings"][0], res["warnings"]


def test_merged_prompt_keeps_three_info_points():
    """合并后的那一条必须同时保留 ①低于目标 ②最大可行收益 ③迭代预算状态。"""
    res = run(make_stub([]), max_sleeve_iter=4)
    lines = _fallback_lines(res)
    assert len(lines) == 1, res["warnings"]
    msg = lines[0]
    assert "低于目标" in msg, msg
    assert "最大可行收益" in msg, msg
    assert "迭代预算" in msg and "已用尽" in msg, msg


def test_non_budget_variant_reports_budget_state():
    """预算**未**用尽而退出时，同样只有一条提示，且必须如实说明未用尽。"""
    res = run(make_stub([], fell_back=True, sleeves_resolved=False), max_sleeve_iter=4)
    lines = _fallback_lines(res)
    assert len(lines) == 1, res["warnings"]
    msg = lines[0]
    assert "未用尽" in msg, msg
    assert "低于目标" in msg and "最大可行收益" in msg, msg
    assert "迭代预算" in msg, msg


def test_no_fallback_no_prompt():
    """反向对照：正常轮（不回退）不得出现任何回退提示。"""
    res = run(make_stub([], fell_back=False), max_sleeve_iter=4)
    assert not _fallback_lines(res), res["warnings"]


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
