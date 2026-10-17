#!/usr/bin/env python3
"""袖内固定点「多初值重启择优」守卫（2026-10-17，修「加新标的 → 夏普下降」）。

背景：目标播种分叉后固定点映射有多个自洽解，单次迭代会锁在较差的一个。
公开入口 `core.engine.solve_portfolio` 在「走过目标播种分叉」时用确定性种子重启并择优。

本文件锁定契约（不联网、无随机、受控桩；全部数值均为**虚构**）：
  ① 未触发 → **只跑一遍**、不加 `sleeve_restarts` 键（未触发路径逐字节不变）；
  ② 触发 → 重启；候选必须过**硬门**（真的交付 + 模型-交付差 ≤0.5pp）才有资格替换基路径，
     合格候选之间按 `_restart_score` 择优（交付缺口 → 模型差 → 交付夏普 → 波动）；
  ③ 被弃候选的 warnings / cap_hits 不得泄漏进交付结果（按候选隔离；生产薄壳直写全局
     CAP_HITS 也靠"调用方同一个 list + 切片"兜住）；
  ④ 同输入两次运行逐字节一致；
  ⑤ 种子 = 切点 μ + λ·(无上限最大收益 μ − 切点 μ)，σ 取切点 σ；λ 取值受保守区间约束。

祖先对照：PM_OPT_SCRIPTS=<祖先>/Optimizer/scripts 且把本文件拷进祖先树 → 必红（无该入口）。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_restart.py
"""
import copy
import json
import os
import sys

_SCRIPTS = os.path.abspath(os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
_PKG_ROOT = os.path.dirname(_SCRIPTS)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from portfolio_system.core import constants as K  # noqa: E402
from portfolio_system.core import engine as ENG  # noqa: E402
from portfolio_system.core import fixpoint as FP  # noqa: E402

TARGET = 0.20
#: 虚构种子向量（与任何真实运行无关）
SEED_VEC = {"O_US_CORE": (0.234, 0.111), "O_JP_EQ": (0.05, 0.12),
            "D_GREATER_CN": (0.04, 0.09)}


class _Universe:
    pool_specs = {k: ({}, {}, 19) for k in ("O_US_CORE", "O_JP_EQ", "D_GREATER_CN")}


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


def _call(monkey_once, monkey_seeds=None, *, warnings=None, caps=None):
    saved = (ENG._solve_fixpoint_once, ENG.restart_seed_params)
    seeds = monkey_seeds if monkey_seeds is not None else [
        ("lambda=0.50", copy.deepcopy(SEED_VEC))]
    ENG._solve_fixpoint_once = monkey_once
    ENG.restart_seed_params = lambda rf, pool_specs, lambdas: copy.deepcopy(seeds)
    try:
        return ENG.solve_portfolio(
            [], {}, dom_w=0.4, ov_w=0.6, target=TARGET, rf=0.02, extract={},
            total_assets=1.0, universe=UNIVERSE, warnings=warnings,
            cap_hits_out=caps)
    finally:
        ENG._solve_fixpoint_once, ENG.restart_seed_params = saved


def test_no_trigger_runs_once_and_adds_no_key():
    """① 未走过目标播种分叉：只跑一遍、不加键。"""
    calls = []

    def once(assets, corr_pairs, **kw):
        calls.append(kw.get("sleeve_start"))
        return _solved(sharpe=0.31, vol=0.24)

    out = _call(once)
    assert len(calls) == 1, calls
    assert "sleeve_restarts" not in out, out.keys()


def test_triggered_prefers_better_restart():
    """② 触发后择优：基路径锁在差固定点，λ 候选过门且更好 → 选中候选 + 审计。"""
    calls = []

    def once(assets, corr_pairs, **kw):
        calls.append(kw.get("sleeve_start"))
        if kw.get("sleeve_start") is None:
            return _solved(sharpe=0.31, vol=0.24, gap=(0.0001, 0.0001),
                           seed_levels={"O_US_CORE": 0.21})
        return _solved(sharpe=0.44, vol=0.13, gap=(0.0021, 0.0022))

    out = _call(once)
    assert len(calls) == 2, calls
    assert out["sleeve_restarts"]["triggered"] is True
    assert out["sleeve_restarts"]["chosen"] == "lambda=0.50", out["sleeve_restarts"]
    assert abs(out["final"]["sharpe"] - 0.44) < 1e-9, out["final"]
    picked = [c for c in out["sleeve_restarts"]["candidates"] if c.get("picked")]
    assert len(picked) == 1 and picked[0]["seed"] == "lambda=0.50", picked


def test_candidate_hard_gates():
    """② 硬门：不可交付 / 模型差越界 的候选不得替换基路径（即使夏普很高）。"""

    def once_undeliverable(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            return _solved(sharpe=0.31, vol=0.24, gap=(0.0001, 0.0001),
                           seed_levels={"O_US_CORE": 0.21})
        return _solved(sharpe=0.90, vol=0.10, deliverable=False,
                       source="stage1_model")

    out = _call(once_undeliverable)
    assert out["sleeve_restarts"]["chosen"] == "base", out["sleeve_restarts"]

    def once_gap(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            return _solved(sharpe=0.31, vol=0.24, gap=(0.0001, 0.0001),
                           seed_levels={"O_US_CORE": 0.21})
        return _solved(sharpe=0.90, vol=0.10, gap=(0.003, 0.003))  # 合计 0.6pp > 0.5pp

    out2 = _call(once_gap)
    assert out2["sleeve_restarts"]["chosen"] == "base", out2["sleeve_restarts"]


def test_gap_gate_survives_base_shortfall():
    """② 基路径**未达标**时，超容差的「达标候选」仍不得替换（自洽性硬门优先于缺口）。"""

    def once(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            # 基路径：缺口 1pp（未达标），但模型自洽
            return _solved(sharpe=0.31, vol=0.24, ret=0.19, gap=(0.0001, 0.0001),
                           seed_levels={"O_US_CORE": 0.21})
        # 候选：达标但模型-交付差 0.6pp（不合格）
        return _solved(sharpe=0.90, vol=0.10, ret=0.20, gap=(0.003, 0.003))

    out = _call(once)
    assert out["sleeve_restarts"]["chosen"] == "base", out["sleeve_restarts"]


def test_ineligible_base_requires_target_met_candidate():
    """② 基路径**不合格**时：候选必须真正达标才允许替换（不能只是「换一种坏结果」）。"""

    def once_short(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            # 基路径不可交付（模型统计、不合格）
            return _solved(sharpe=0.31, vol=0.24, deliverable=False,
                           source="stage1_model",
                           seed_levels={"O_US_CORE": 0.21})
        # 候选合格但未达标（缺口 1pp）→ 必须保留基路径
        return _solved(sharpe=0.44, vol=0.13, ret=0.19)

    out = _call(once_short)
    assert out["sleeve_restarts"]["chosen"] == "base", out["sleeve_restarts"]

    def once_met(assets, corr_pairs, **kw):
        if kw.get("sleeve_start") is None:
            return _solved(sharpe=0.31, vol=0.24, deliverable=False,
                           source="stage1_model",
                           seed_levels={"O_US_CORE": 0.21})
        return _solved(sharpe=0.44, vol=0.13, ret=TARGET)  # 达标 → 接受

    out2 = _call(once_met)
    assert out2["sleeve_restarts"]["chosen"] == "lambda=0.50", out2["sleeve_restarts"]


def test_discarded_candidate_warnings_and_caps_do_not_leak():
    """③ 按候选隔离：交付只带「调用方既有 + 被选中候选」的告警/触顶记录。"""
    warnings = ["调用方既有告警"]
    caps = [("PRE", 0.1, 0.1)]

    def once(assets, corr_pairs, **kw):
        w = kw.get("warnings")
        c = kw.get("cap_hits_out")
        if kw.get("sleeve_start") is None:
            if w is not None:
                w.append("基路径中间告警")
            if c is not None:
                c.append(("BASE", 0.2, 0.2))
            return _solved(sharpe=0.31, vol=0.24, seed_levels={"O_US_CORE": 0.21})
        if w is not None:
            w.append("候选告警")
        if c is not None:
            c.append(("CAND", 0.3, 0.3))
        return _solved(sharpe=0.44, vol=0.13)

    out = _call(once, warnings=warnings, caps=caps)
    assert out["sleeve_restarts"]["chosen"] == "lambda=0.50"
    assert warnings == ["调用方既有告警", "候选告警"], warnings
    assert caps == [("PRE", 0.1, 0.1), ("CAND", 0.3, 0.3)], caps
    assert out["warnings"] is warnings
    assert [it["message"] for it in out["warning_items"]] == \
        ["调用方既有告警", "候选告警"], out["warning_items"]


def test_deterministic_for_same_input():
    """④ 同输入两次运行逐字节一致。"""

    def make_once():
        def once(assets, corr_pairs, **kw):
            if kw.get("sleeve_start") is None:
                return _solved(sharpe=0.31, vol=0.24, seed_levels={"O_US_CORE": 0.21})
            return _solved(sharpe=0.44, vol=0.13, gap=(0.002, 0.002))
        return once

    a = _call(make_once())
    b = _call(make_once())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_restart_seed_formula_and_determinism():
    """⑤ 种子公式：μ = 切点 + λ·(无上限最大收益 − 切点)，σ 取切点 σ；同输入同输出。"""
    saved = (FP.sleeve_tangency, FP.pool_max_return)
    FP.sleeve_tangency = lambda key, rf, pool_specs, cache=None: {
        "O_US_CORE": (0.111, 0.222), "O_JP_EQ": (0.050, 0.100),
        "D_GREATER_CN": (0.040, 0.080)}[key]
    FP.pool_max_return = lambda pool, corr, cap=None: {
        "O_US_CORE": 0.333, "O_JP_EQ": 0.050, "D_GREATER_CN": 0.100}[pool]
    try:
        specs = {"O_US_CORE": ("O_US_CORE", {}, 19), "O_JP_EQ": ("O_JP_EQ", {}, 31),
                 "D_GREATER_CN": ("D_GREATER_CN", {}, 43)}
        seeds = FP.restart_seed_params(0.02, specs, (0.5,))
        assert len(seeds) == 1, seeds
        label, vec = seeds[0]
        assert label == "lambda=0.50", label
        assert abs(vec["O_US_CORE"][0] - 0.222) < 1e-12, vec   # 0.111 + 0.5×(0.333−0.111)
        assert abs(vec["O_US_CORE"][1] - 0.222) < 1e-12, vec    # σ 取切点
        assert abs(vec["O_JP_EQ"][0] - 0.050) < 1e-12, vec      # 无余量 → 不插值
        assert abs(vec["D_GREATER_CN"][0] - 0.070) < 1e-12, vec  # 0.040 + 0.5×(0.100−0.040)
        again = FP.restart_seed_params(0.02, specs, (0.5,))
        assert seeds == again
    finally:
        FP.sleeve_tangency, FP.pool_max_return = saved


def test_restart_lambdas_are_conservative():
    """⑤ λ 常量守卫：必须落在保守区间（>0 且 ≤0.75）并去重有序 —— 防「改坏了也没人发现」。"""
    lams = tuple(K.SLEEVE_RESTART_LAMBDAS)
    assert lams, lams
    assert list(lams) == sorted(set(lams)), lams
    for lam in lams:
        assert 0.0 < lam <= 0.75, (lam, "λ 过大会过冲成不收敛/差固定点")
    assert 0.0 < K.MODEL_GAP_TOL <= 5e-3, K.MODEL_GAP_TOL


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
