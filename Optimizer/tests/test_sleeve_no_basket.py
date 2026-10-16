#!/usr/bin/env python3
"""「袖无篮子时不得把阶段一模型统计当成交付结果」回归测试（P0，不联网、全虚构参数）。

背景（2026-10-15 P0）：阶段二给不出篮子时（袖内池为空 / 全部起点失败），_solve_once
会把 final 退化为阶段一统计（避免结果文件全 0，历史 Bug D2）。若该袖在阶段一**有权重**，
这份 final 就是**模型**统计、不是可交付组合：active 类单标的 10% 上限只在阶段二内部生效，
模型口径可能高估收益。修复前只有一句软提示（"收益/波动不完全来自交付组合"），
模型统计仍被当成交付结果。本文件断言：这类结果必须被**显式标注为不可交付**，点名每只袖
并给出可诊断原因（"袖内候选池为空" vs "全部起点失败"），且永远不得算收敛。

覆盖两条失败路径（受控桩确定性复现）：
  1. 袖内候选池为空（没有可执行标的）；
  2. 袖内有候选标的，但阶段二全部起点失败（解不出可行篮子）。
外加"袖在阶段一就是 0 权重"的反向对照，防止对正常结果误报。

运行（本环境无 pytest，逐文件直跑）：
  Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_no_basket.py
修复前对照（同一份测试加载修复前的 optimize_portfolio.py，应当至少 1 项失败）：
  PM_OPT_SCRIPTS=tmp/agent-team/prefix/task-6 PYTHONPATH=Optimizer/scripts \
    Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_no_basket.py
"""

import os
import sys

import numpy as np

# 默认加载本仓库的优化器；PM_OPT_SCRIPTS 指向"修复前副本"目录时做差分验证。
_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402

# ---- 虚构参数（与任何真实持仓无关）----
DOM_W, OV_W = 0.25, 0.75
TOTAL = 1_000_000.0
RF = 0.02
TARGET_OK = 0.16

FAKE_US = {
    "UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.10},
    "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.18},
}
FAKE_JP = {
    "JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.12},
    "JD": {"name": "虚构日股D", "mu": 0.08, "vol": 0.15},
}
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

# 两条失败路径的候选池形态（都是虚构池）
POOLS_EMPTY = {"O_US_CORE": ({}, {}, 19), "O_JP_EQ": ({}, {}, 31)}
POOLS_FAILING = {"O_US_CORE": (FAKE_US, FAKE_CORR, 19), "O_JP_EQ": (FAKE_JP, FAKE_CORR, 31)}


class patched_pools:
    """替换模块级袖内候选池（用完还原），让本文件完全自包含、不依赖真实池。"""

    def __init__(self, specs):
        self.specs = specs

    def __enter__(self):
        self.saved = (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        us_pool, us_corr, _ = self.specs.get("O_US_CORE", ({}, {}, 19))
        jp_pool, jp_corr, _ = self.specs.get("O_JP_EQ", ({}, {}, 31))
        OP.US_INTERNAL = us_pool
        OP.US_INTERNAL_CORR = us_corr
        OP.JP_INTERNAL = jp_pool
        OP.JP_INTERNAL_CORR = jp_corr
        OP._SLEEVE_POOL_SPECS = dict(self.specs)
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         saved_specs) = self.saved
        if saved_specs is None:
            del OP._SLEEVE_POOL_SPECS
        else:
            OP._SLEEVE_POOL_SPECS = saved_specs
        return False


def _no_basket_round(assets_in, fell_back=False, weights=(0.25, 0.60, 0.10, 0.05)):
    """受控桩：阶段一有权重，但阶段二三只袖都给不出篮子（internal/japan/gc 全 None）。"""
    r = {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": np.array(list(weights)),
        "stage1_ret": 0.1710, "stage1_vol": 0.0898, "stage1_sharpe": 1.682,
        "internal": None, "internal_tickers": None,
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": 0.1710, "vol": 0.0898, "sharpe": 1.682, "floor": 0.1710 - 1.96 * 0.0898},
        "target": TARGET_OK, "warnings": [],
    }
    if fell_back:
        r["target"] = 0.157499
        r["stage1_ret"] = r["final"]["ret"] = 0.1575
        r["stage1_vol"] = r["final"]["vol"] = 0.3003
        r["stage1_sharpe"] = r["final"]["sharpe"] = 0.4578
        r["final"]["floor"] = 0.1575 - 1.96 * 0.3003
    return r


def run(stub, specs, target=TARGET_OK, max_sleeve_iter=2):
    with patched_pools(specs):
        original = OP._solve_once
        OP._solve_once = stub
        try:
            return OP.solve_portfolio(
                [dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W, target=target,
                rf=RF, extract=EXTRACT, total_assets=TOTAL, warnings=[], log=None,
                max_sleeve_iter=max_sleeve_iter, stage1_starts=60, stage2_starts=30)
        finally:
            OP._solve_once = original


# ------------------------------------------------- 1. 失败路径一：袖内候选池为空

def test_empty_pool_is_marked_undeliverable():
    res = run(lambda a, c=None, **kw: _no_basket_round(a), POOLS_EMPTY)
    joined = " ".join(res["warnings"])
    assert "不可交付" in joined, res["warnings"]
    assert "O_US_CORE" in joined and "O_JP_EQ" in joined, joined
    assert "袖内候选池为空" in joined, joined
    assert res["sleeve_converged"] is False, "有袖没被解出时不得算收敛"


# ------------------------------------------------- 2. 失败路径二：全部起点失败

def test_all_starts_failed_is_marked_undeliverable():
    res = run(lambda a, c=None, **kw: _no_basket_round(a), POOLS_FAILING)
    joined = " ".join(res["warnings"])
    assert "不可交付" in joined, res["warnings"]
    assert "O_US_CORE" in joined and "O_JP_EQ" in joined, joined
    assert "全部起点失败" in joined, joined
    assert "袖内候选池为空" not in joined, joined
    assert res["sleeve_converged"] is False


# ------------------------------------------------- 3. final 不得被当成交付结果

def test_model_stats_are_not_presented_as_delivery():
    res = run(lambda a, c=None, **kw: _no_basket_round(a), POOLS_EMPTY)
    assert res["final"]["ret"] == res["stage1_ret"]
    joined = " ".join(res["warnings"])
    assert "模型统计" in joined and "不可交付" in joined, joined
    assert "请勿据此执行" in joined, joined


# ------------------------------------------------- 4. 回退轮（best 为空）也要标注

def test_fallback_path_is_also_marked_undeliverable():
    res = run(lambda a, c=None, **kw: _no_basket_round(a, fell_back=True),
              POOLS_FAILING, max_sleeve_iter=1)
    joined = " ".join(res["warnings"])
    assert "不可交付" in joined, res["warnings"]
    assert "低于目标" in joined, joined
    assert res["sleeve_converged"] is False


# ------------------------------------------------- 5. 反向对照：0 权重袖不得误报

def test_zero_weight_sleeve_is_not_flagged():
    def stub(a, c=None, **kw):
        return _no_basket_round(a, weights=(0.25, 0.0, 0.0, 0.75))

    res = run(stub, POOLS_EMPTY)
    joined = " ".join(res["warnings"])
    assert res["sleeve_converged"] is True, res["warnings"]
    assert "不可交付" not in joined, joined


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
