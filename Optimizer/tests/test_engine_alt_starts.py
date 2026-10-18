#!/usr/bin/env python3
"""引擎多初值（`solve_portfolio(..., alt_starts=...)`）守卫（2026-10-18 Q2，契约 §2.1）。

背景：敏感性扫描用「上一点热启动」，会锁进更差的「自洽固定点」，且从不与主结果比对 →
曲线低于主结果（Q2）。修法：调用方可以给**确定性**初值（主结果的袖参数 / λ=0.20 种子），
引擎把它们与既有「目标播种分叉」种子一起择优；即使没走过目标播种分叉（alt 场景常见）
也要跑候选，且基路径返回 None 时允许候选交付。

本文件锁定契约（不联网、无随机、受控桩；全部数值均为**虚构**）：
  ① 未传 alt 且未触发 → 只跑一遍、不加 `sleeve_restarts`（一个字节都不多）；
  ② 存在 alt 但不触发 → 仍跑候选；更优的合格候选被选中，`reason="alt_starts"`；
  ③ 择优完全复用 `_restart_eligible` 硬门：不可交付 / 模型-交付差越界的候选不得替换基路径；
  ④ 基路径 None + alt 成功 → 交付候选；被弃（失败）基路径的诊断告警按候选隔离；
  ⑤ 触发 + alt → `reason="both"`，候选顺序 =（既有种子）+ alt；触发无 alt → `reason="seeded_fork"`；
  ⑥ 同输入两次运行逐字节一致；
  ⑦ `sleeve_history[i].zero_weight_sleeves`：阶段一 0 权重的袖（有序）；没有则不出键。
     该加法键**只在新增路径写**（外部热启动或 alt；普通主运行与 trigger-only 重启都不写）。

祖先对照（`PM_OPT_SCRIPTS=<祖先>/Optimizer/scripts` + 把本文件拷进祖先树）必红：
祖先 `solve_portfolio` 没有 `alt_starts` / `reason` / `zero_weight_sleeves`。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_engine_alt_starts.py
"""
import copy
import json
import os
import sys

import numpy as np

_SCRIPTS = os.path.abspath(os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
# P4：包根 = scripts 的父目录（由 PM_OPT_SCRIPTS 派生 → 祖先树同样可用）。
_PKG_ROOT = os.path.dirname(_SCRIPTS)
for _p in (_SCRIPTS, _PKG_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from portfolio_system.core import engine as ENG  # noqa: E402
from portfolio_system.core.sleeve_params import SLEEVE_KEYS, SleeveHooks  # noqa: E402

TARGET = 0.20
RF = 0.02
#: 虚构的既有确定性种子（λ=0.50）与调用方 alt 初值 —— 与任何真实运行无关。
SEED_VEC = {"O_US_CORE": (0.234, 0.111), "O_JP_EQ": (0.05, 0.12),
            "D_GREATER_CN": (0.04, 0.09)}
ALT_VEC = {"O_US_CORE": (0.300, 0.120), "O_JP_EQ": (0.06, 0.13),
           "D_GREATER_CN": (0.05, 0.10)}


class _Universe:
    pool_specs = {k: ({}, {}, 19) for k in SLEEVE_KEYS}


UNIVERSE = _Universe()


def _solved(*, sharpe, vol, ret=TARGET, gap=(0.0, 0.0), seed_levels=None,
            deliverable=True, source="delivered"):
    """构造一个「结果字典」：final / stage1 / 收敛信息 / history 都与真实结构同构。"""
    history = [{"iter": 1, "delta": 0.0, "sleeve": {}, "fallback": True}]
    if seed_levels is not None:
        history[0]["sleeve_seeded_levels"] = dict(seed_levels)
    return {
        "assets": [], "keys": [], "mu": None, "vols": None, "cov": None,
        "stage1_ret": float(ret) + float(gap[0]),
        "stage1_vol": float(vol) + float(gap[1]),
        "sleeve_params": {"O_US_CORE": [0.21, 0.11]},
        "sleeve_iterations": 3, "sleeve_converged": True,
        "sleeve_history": history,
        "final": {"ret": float(ret), "vol": float(vol), "sharpe": float(sharpe),
                  "floor": float(ret) - 1.96 * float(vol)},
        "deliverable": bool(deliverable), "final_source": source,
        "warnings": [], "warning_items": [],
    }


def _call(monkey_once, monkey_seeds=None, *, alt=None, warnings=None, caps=None):
    """替换单初值内核与种子构造器，然后调用公开入口。

    alt 为 None 时**不传** alt_starts 关键字（= 旧调用的字节形态）。
    """
    saved = (ENG._solve_fixpoint_once, ENG.restart_seed_params)
    seeds = monkey_seeds if monkey_seeds is not None else [
        ("lambda=0.50", copy.deepcopy(SEED_VEC))]
    ENG._solve_fixpoint_once = monkey_once
    ENG.restart_seed_params = lambda rf, pool_specs, lambdas: copy.deepcopy(seeds)
    kwargs = {} if alt is None else {"alt_starts": alt}
    try:
        return ENG.solve_portfolio(
            [], {}, dom_w=0.4, ov_w=0.6, target=TARGET, rf=RF, extract={},
            total_assets=1.0, universe=UNIVERSE, warnings=warnings,
            cap_hits_out=caps, **kwargs)
    finally:
        ENG._solve_fixpoint_once, ENG.restart_seed_params = saved


# ──────────────────────────────────────────────────────────────────────────────
def test_no_alt_no_trigger_keeps_old_path_unchanged():
    """① 未传 alt 且未触发：只跑一遍、不加 sleeve_restarts、返回的仍是同一份结果。"""
    calls = []
    stub_result = _solved(sharpe=0.31, vol=0.24)

    def once(assets, corr_pairs, **kw):
        calls.append(dict(kw))
        return stub_result

    out = _call(once)           # alt=None → 与旧调用完全同形（不传 alt_starts）
    assert len(calls) == 1, calls
    assert calls[0].get("sleeve_start") is None, calls
    assert out is stub_result, "未触发路径必须原样返回单初值结果（不复制、不加键）"
    assert "sleeve_restarts" not in out, sorted(out)


def test_alt_candidate_runs_without_trigger_and_is_picked():
    """② 没走过目标播种分叉，但存在 alt → 仍跑候选；更优者被选中 + reason=alt_starts。"""
    calls = []

    def once(assets, corr_pairs, **kw):
        calls.append(kw.get("sleeve_start"))
        if kw.get("sleeve_start") is None:
            return _solved(sharpe=0.31, vol=0.24, gap=(0.0001, 0.0001))
        return _solved(sharpe=0.44, vol=0.13)

    out = _call(once, alt=[("anchor", copy.deepcopy(ALT_VEC))])
    assert len(calls) == 2, calls
    assert calls[0] is None and calls[1] == ALT_VEC, calls
    restarts = out["sleeve_restarts"]
    assert restarts["triggered"] is False, restarts
    assert restarts["reason"] == "alt_starts", restarts
    assert restarts["chosen"] == "anchor", restarts
    assert abs(out["final"]["sharpe"] - 0.44) < 1e-12, out["final"]
    picked = [c for c in restarts["candidates"] if c.get("picked")]
    assert len(picked) == 1 and picked[0]["seed"] == "anchor", picked
    labels = [c["seed"] for c in restarts["candidates"]]
    assert labels == ["base", "anchor"], labels


def test_ineligible_alt_never_replaces_base():
    """③ 硬门：不可交付 / 模型-交付差越界的候选不得替换基路径（即使夏普很高）。"""

    def once_undeliverable(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            return _solved(sharpe=0.31, vol=0.24, gap=(0.0001, 0.0001))
        return _solved(sharpe=0.90, vol=0.10, deliverable=False, source="stage1_model")

    out = _call(once_undeliverable, alt=[("anchor", copy.deepcopy(ALT_VEC))])
    assert out["sleeve_restarts"]["chosen"] == "base", out["sleeve_restarts"]
    assert abs(out["final"]["sharpe"] - 0.31) < 1e-12, out["final"]

    def once_gap(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            return _solved(sharpe=0.31, vol=0.24, gap=(0.0001, 0.0001))
        return _solved(sharpe=0.90, vol=0.10, gap=(0.003, 0.003))   # 0.6pp > 0.5pp

    out2 = _call(once_gap, alt=[("anchor", copy.deepcopy(ALT_VEC))])
    assert out2["sleeve_restarts"]["chosen"] == "base", out2["sleeve_restarts"]


def test_base_none_plus_alt_delivers_candidate():
    """④ 基路径 None（单初值不可行）+ alt 成功 → 交付候选；失败基路径的告警被隔离。"""
    warnings = []

    def once(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            if kw.get("warnings") is not None:
                kw["warnings"].append("基路径失败诊断")
            return None
        return _solved(sharpe=0.44, vol=0.13, ret=TARGET)

    out = _call(once, alt=[("anchor", copy.deepcopy(ALT_VEC))], warnings=warnings)
    assert out is not None, "基路径 None 时合格候选必须能交付（契约 §2.1）"
    assert out["sleeve_restarts"]["chosen"] == "anchor", out["sleeve_restarts"]
    assert out["sleeve_restarts"]["reason"] == "alt_starts", out["sleeve_restarts"]
    assert out["sleeve_restarts"]["triggered"] is False, out["sleeve_restarts"]
    # 被弃候选（失败的基路径）的诊断告警按候选隔离，不得混进交付结果
    assert warnings == [], warnings
    labels = [c["seed"] for c in out["sleeve_restarts"]["candidates"]]
    assert labels == ["anchor"], labels

    # 反向：基路径 None 且候选也不合格 → 仍然返回 None（与旧版一致地失败）
    warnings2 = []

    def once_bad(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            if kw.get("warnings") is not None:
                kw["warnings"].append("基路径失败诊断")
            return None
        return _solved(sharpe=0.90, vol=0.10, deliverable=False, source="stage1_model")

    out2 = _call(once_bad, alt=[("anchor", copy.deepcopy(ALT_VEC))], warnings=warnings2)
    assert out2 is None, out2
    assert warnings2 == ["基路径失败诊断"], warnings2


def test_triggered_plus_alt_reason_both_and_candidate_order():
    """⑤ 触发 + alt → reason="both"；候选顺序 =（既有种子）+ alt。"""
    calls = []

    def once(assets, corr_pairs, **kw):
        calls.append(kw.get("sleeve_start"))
        start = kw.get("sleeve_start")
        if start is None:
            return _solved(sharpe=0.31, vol=0.24, seed_levels={"O_US_CORE": 0.21})
        if start == SEED_VEC:
            return _solved(sharpe=0.35, vol=0.20)
        return _solved(sharpe=0.44, vol=0.13)

    out = _call(once, alt=[("anchor", copy.deepcopy(ALT_VEC))])
    assert len(calls) == 3, calls
    assert calls[1] == SEED_VEC and calls[2] == ALT_VEC, calls
    restarts = out["sleeve_restarts"]
    assert restarts["triggered"] is True, restarts
    assert restarts["reason"] == "both", restarts
    assert restarts["chosen"] == "anchor", restarts
    labels = [c["seed"] for c in restarts["candidates"]]
    assert labels == ["base", "lambda=0.50", "anchor"], labels


def test_triggered_without_alt_has_no_reason_key():
    """⑤ 触发但没传 alt → **不写 reason**（保留 2026-10-17 的 sleeve_restarts 旧结构）。

    Lead 2026-10-18 收口：reason 只在 alt 路径（敏感性锚定）出现；仅「目标播种分叉」
    触发的旧路径一个键都不多 → canned inject 回放逐字节不变。
    """

    def once(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            return _solved(sharpe=0.31, vol=0.24, seed_levels={"O_US_CORE": 0.21})
        return _solved(sharpe=0.44, vol=0.13)

    out = _call(once)           # 不传 alt
    restarts = out["sleeve_restarts"]
    assert "reason" not in restarts, restarts
    assert set(restarts) == {"triggered", "chosen", "candidates"}, restarts
    assert restarts["chosen"] == "lambda=0.50", restarts


def test_deterministic_for_same_input_with_alt():
    """⑥ 同输入两次运行逐字节一致（含 alt 路径）。"""

    def make_once():
        def once(assets, corr_pairs, **kw):
            if kw.get("sleeve_start") is None:
                return _solved(sharpe=0.31, vol=0.24)
            return _solved(sharpe=0.44, vol=0.13, gap=(0.002, 0.002))
        return once

    a = _call(make_once(), alt=[("anchor", copy.deepcopy(ALT_VEC))])
    b = _call(make_once(), alt=[("anchor", copy.deepcopy(ALT_VEC))])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ── ⑦ sleeve_history[i].zero_weight_sleeves（契约 §2.1）──────────────────────
#: 虚构资产：三只袖，初值 μ/σ 相同（与任何真实运行无关）。
FIX_ASSETS = [
    {"key": "O_US_CORE", "name": "虚构美股核心袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_JP_EQ", "name": "虚构日本权益袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "D_GREATER_CN", "name": "虚构大中华袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
]


def _round_stub(weights, ret=0.16, vol=0.10):
    """受控的一轮结果：交付=目标、三袖都自洽（delta=0 → 1 轮收敛）。"""
    def _round(assets_in, corr_pairs, **kw):
        keys = [a["key"] for a in assets_in]
        sharpe = (ret - RF) / vol
        return {
            "keys": keys, "stage1_weights": np.array(weights, dtype=float),
            "stage1_ret": ret, "stage1_vol": vol, "stage1_sharpe": sharpe,
            "internal": (np.array([1.0]), ret, vol, sharpe, None, ["UA"]),
            "internal_tickers": ["UA"],
            "japan_selection": {"ret": 0.02, "vol": 0.10},
            "gc_selection": {"ret": 0.02, "vol": 0.10},
            "final": {"ret": ret, "vol": vol, "sharpe": sharpe,
                      "floor": ret - 1.96 * vol},
            "target": TARGET, "warnings": [],
        }
    return _round


def _run_solve(weights, *, sleeve_start=None, alt=None):
    """走公开入口 solve_portfolio（受控 hooks）→ 审计键 gating 由入口决定。"""
    hooks = SleeveHooks(
        solve_once=_round_stub(weights),
        sleeve_params_us=lambda internal, tickers: None,
        compute_sleeve_floors=lambda *a, **kw: ({}, {}),
        seed_levels_for_target=lambda *a, **kw: {},
        floor_seed=lambda *a, **kw: None,
    )
    kwargs = {} if alt is None else {"alt_starts": alt}
    return ENG.solve_portfolio(
        [dict(a) for a in FIX_ASSETS], {}, dom_w=0.3, ov_w=0.7, target=TARGET, rf=RF,
        extract={}, total_assets=1.0, universe=UNIVERSE, warnings=[], hooks=hooks,
        sleeve_start=sleeve_start, **kwargs)


def test_zero_weight_sleeves_absent_for_normal_main_run():
    """⑦ 普通主运行（sleeve_start=None、无 alt）：即使有 0 权重袖也**不写**审计键。

    Lead 2026-10-18 收口：加法键只在新增路径（热启动/敏感性/alt）出现，
    否则 canned 主优化回放会因审计键而字节不同。
    """
    out = _run_solve([0.6, 0.0, 0.4])
    entry = out["sleeve_history"][0]
    assert "zero_weight_sleeves" not in entry, entry


def test_zero_weight_sleeves_recorded_for_warm_start():
    """⑦ 热启动路径（外部传 sleeve_start）：阶段一 0 权重的袖进审计键（有序）。"""
    warm = {"O_US_CORE": (0.02, 0.10), "O_JP_EQ": (0.02, 0.10),
            "D_GREATER_CN": (0.02, 0.10)}
    out = _run_solve([0.6, 0.0, 0.4], sleeve_start=warm)
    entry = out["sleeve_history"][0]
    assert entry.get("zero_weight_sleeves") == ["O_JP_EQ"], entry


def test_zero_weight_sleeves_recorded_for_alt_base_path():
    """⑦ alt 路径（敏感性锚定）即使基路径 sleeve_start=None，也写审计键。

    该路径的基路径 sleeve_start 可能为 None，但调用方传了 alt_starts
    → 审计键必须能出现（否则曲线无法诊断「袖全空」）。
    """
    out = _run_solve([0.6, 0.0, 0.4],
                     alt=[("anchor", {"O_US_CORE": (0.02, 0.10)})])
    entry = out["sleeve_history"][0]
    assert entry.get("zero_weight_sleeves") == ["O_JP_EQ"], entry
    assert out["sleeve_restarts"]["chosen"] == "base", out["sleeve_restarts"]


def test_zero_weight_sleeves_key_absent_when_all_have_weight():
    """⑦ 没有 0 权重袖 → 即使热启动也不出键（空则不出键）。"""
    warm = {"O_US_CORE": (0.02, 0.10), "O_JP_EQ": (0.02, 0.10),
            "D_GREATER_CN": (0.02, 0.10)}
    out = _run_solve([0.6, 0.2, 0.2], sleeve_start=warm)
    entry = out["sleeve_history"][0]
    assert "zero_weight_sleeves" not in entry, entry


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
        except Exception as exc:  # noqa: BLE001
            print("  FAIL", name)
            print("   ", type(exc).__name__ + ":", exc)
    print("%d/%d 通过" % (ok, len(tests)))
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
