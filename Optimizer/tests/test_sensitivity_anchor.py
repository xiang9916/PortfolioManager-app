#!/usr/bin/env python3
"""敏感性锚定（Q2，契约 §2.2）守卫 —— 纯离线、受控桩、全部虚构。

背景：敏感性扫描过去用「上一点热启动」逐点求解，会锁进更差的「自洽固定点」，
且从不与主结果比对 → 曲线上锚点目标那一点明显劣于主结果（绿点 vs 蓝点）。
契约 §2.2 的修法：锚点目标并入网格；对「约束绷紧」的点与锚点目标点额外给一个
canonical 候选（主结果的袖参数 / λ=0.20 种子），由引擎择优；点新增
`fallback` / `chosen_start` 字段；锚点被主结果支配时发 `sensitivity.anchor_mismatch` 告警。

本文件锁定：
  ① 锚点目标并入网格（去重、排序、四舍五入 4 位；低于 step_min 也进网格）；
  ② 无锚点调用 = 旧行为（求解次数/网格/数值不变，只多 fallback/chosen_start 两个加法键）；
  ③ 约束绷紧的点用主结果袖参数作 canonical 候选（松弛的连续点跳过）；
  ④ 锚点目标低于切点估计 → canonical 用 λ=0.20 的确定性种子（不是主结果袖参数）；
  ⑤ fallback / chosen_start 字段的映射；
  ⑥ --step-max 是上限：第二阶段补点不得越界；
  ⑦ 锚点被主结果支配 → anchor.matched=False + sensitivity.anchor_mismatch 告警；
  ⑧ 锚点参数解析失败 / 袖 key 与资产不匹配 → 忽略锚点 + 一条 stderr 提示（不失败）；
  ⑨ 持仓纳入接线（契约 §1.4 末）：helper 的 corr 合并进求解、estimates/skipped 透传成 "held" 块；
  ⑩ 锚点目标点与主结果一致（绿点=蓝点）：引擎选中 anchor 候选时该点就是主结果。

祖先对照（`PM_OPT_SCRIPTS=<祖先>/Optimizer/scripts` + 把本文件拷进祖先树）必红：
祖先 `run_sensitivity` 没有 anchor_* 参数（TypeError），也没有 held 接线。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_sensitivity_anchor.py
"""
import contextlib
import io
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

import sensitivity_analysis as SENS  # noqa: E402
from portfolio_system.core.sleeve_params import SLEEVE_KEYS  # noqa: E402

#: 虚构夹具（与 test_sensitivity_offline 同款三只大类资产；无袖）。
DOM_W, OV_W, TOTAL, RF = 0.30, 0.70, 1_000_000.0, 0.02
ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_GOLD", "name": "虚构黄金", "mu": 0.05, "vol": 0.15,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_BROAD", "name": "虚构海外宽基", "mu": 0.09, "vol": 0.20,
     "pool": "overseas", "test_class": "broad"},
]
#: 带一只袖的变体（锚点袖参数要能通过「袖 key 与资产匹配」校验）。
ASSETS_SLEEVE = ASSETS + [
    {"key": "O_US_CORE", "name": "虚构美股核心袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
]
EXTRACT = {
    "pool_mode": "fixture", "domestic_weight": DOM_W, "overseas_weight": OV_W,
    "domestic_value": TOTAL * DOM_W, "overseas_value": TOTAL * OV_W, "total_value": TOTAL,
    "rf": RF,
    "us_equity": {"total_value": TOTAL * OV_W,
                  "holdings": [{"ticker": "UA", "name": "虚构",
                                "value_cny": TOTAL * OV_W, "weight": 1.0}]},
}
#: 契约 §2.2 的低目标候选 λ。
SEED_VEC = {"O_US_CORE": (0.11, 0.12)}
ANCHOR_SLEEVE = {"O_US_CORE": (0.08, 0.12)}


def _fake_solved(assets, target, *, ret=None, vol=0.10, chosen=None, lowered=False,
                 mu=None):
    """与引擎 result 同形的受控桩结果（fake runner 返回它）。"""
    ret = float(target) if ret is None else float(ret)
    keys = [str(a["key"]) for a in assets]
    mu = (np.array([float(a["mu"]) for a in assets], dtype=float)
          if mu is None else np.array(mu, dtype=float))
    out = {
        "assets": list(assets), "keys": keys, "mu": mu,
        "cov": np.eye(len(assets)),
        "sleeve_params": {k: [0.09, 0.12] for k in keys if k in SLEEVE_KEYS},
        "final": {"ret": ret, "vol": float(vol),
                  "sharpe": (ret - RF) / float(vol) if vol else 0.0,
                  "floor": ret - 1.96 * float(vol)},
        "target": (float(target) - 0.01) if lowered else float(target),
        "warnings": [],
    }
    if chosen is not None:
        out["sleeve_restarts"] = {"triggered": True, "chosen": chosen,
                                  "reason": "alt_starts", "candidates": []}
    return out


def _patch_runner(responder):
    """把 SENS.SensitivityRunner 换成受控桩；返回 (实例列表, 还原函数)。"""
    saved = SENS.SensitivityRunner
    made = []

    class _FakeRunner:
        def __init__(self, optimizer=None, universe=None):
            self.universe = universe
            self.calls = []
            made.append(self)

        def solve_point(self, assets, corr_pairs, *, dom_w, ov_w, target, rf, extract,
                        total_assets, sleeve_start=None, warnings=None, alt_starts=None):
            self.calls.append({
                "target": float(target), "sleeve_start": sleeve_start,
                "alt_starts": alt_starts, "corr_pairs": dict(corr_pairs),
                "assets": [str(a["key"]) for a in assets],
            })
            return responder(float(target), alt_starts, sleeve_start, warnings)

    SENS.SensitivityRunner = _FakeRunner
    return made, saved


def _patch_assembly(assets=None, held_meta=None):
    """把装配（extract → assets）换成桩；返回还原函数。"""
    saved = SENS.load_extract_assets
    fixed = [dict(a) for a in (assets if assets is not None else ASSETS)]

    def stub(extract, work_dir, **kw):
        base = (fixed, {}, [], {"source": "fixture"})
        return base + (held_meta,) if held_meta is not None else base

    SENS.load_extract_assets = stub
    return saved


def _patch_seed(vec=None):
    """把 λ 种子构造器换成确定性桩（避免在测试里跑真实袖内优化器）；返回 (调用记录, 还原)。

    用 getattr/hasattr 而不是直接取属性：祖先树没有 `SENS.restart_seed_params`
    （锚定功能未实现）时，本文件仍应让用例**跑到调用点**再以真正的契约缺失失败，
    而不是先被测试桩自己的 AttributeError 拦下。
    """
    existed = hasattr(SENS, "restart_seed_params")
    saved = getattr(SENS, "restart_seed_params", None)
    calls = []
    fixed = dict(vec or SEED_VEC)

    def fake(rf, pool_specs, lambdas):
        calls.append({"rf": rf, "lambdas": tuple(lambdas)})
        return [("lambda=%.2f" % float(lambdas[0]), dict(fixed))]

    def restore():
        if existed:
            SENS.restart_seed_params = saved
        elif hasattr(SENS, "restart_seed_params"):
            del SENS.restart_seed_params

    SENS.restart_seed_params = fake
    return calls, restore


def _run_sensitivity(*, responder, assets=None, anchor_kwargs=None, seed_vec=None,
                     step_min=0.05, step_max=None):
    """跑一次（受控桩）扫描；返回 (out, calls, 种子调用记录)。"""
    made, saved_runner = _patch_runner(responder)
    saved_asm = _patch_assembly(assets)
    seed_calls, saved_seed = _patch_seed(seed_vec)
    try:
        kwargs = dict(extract=dict(EXTRACT), step_min=step_min, step_max=step_max,
                      log=lambda *a, **k: None)
        kwargs.update(anchor_kwargs or {})
        out = SENS.run_sensitivity(**kwargs)
    finally:
        SENS.SensitivityRunner = saved_runner
        SENS.load_extract_assets = saved_asm
        saved_seed()
    return out, (made[0].calls if made else []), seed_calls


def _generic_responder(assets=None):
    """每点交付 = 目标（无锚点时一阶行为恒定）。"""
    fixed = assets if assets is not None else ASSETS

    def responder(target, alt_starts, sleeve_start, warnings):
        return _fake_solved(fixed, target)
    return responder


def _targets(calls):
    return [c["target"] for c in calls]


def _near(values, expected, tol=1e-9):
    return any(abs(v - expected) <= tol for v in values)


# ──────────────────────────────────────────────────────────────────────────────
def test_anchor_target_merged_into_grid_deduped_and_sorted():
    """① 锚点目标并入 target 网格：去重、排序、四舍五入 4 位；低于 step_min 也进网格。"""
    out, calls, _ = _run_sensitivity(
        responder=_generic_responder(), step_min=0.05, step_max=0.06,
        anchor_kwargs={"anchor_target": 0.0533})
    targets = _targets(calls)
    assert targets == sorted(targets), targets
    assert _near(targets, 0.0533), targets
    assert sum(1 for t in targets if abs(t - 0.0533) <= 1e-9) == 1, targets
    assert any(abs(p["target_return"] - 0.0533) <= 1e-9 for p in out["points"]), out["points"]

    # 已在网格上的锚点目标不得重复（去重）
    _, calls2, _ = _run_sensitivity(
        responder=_generic_responder(), step_min=0.05, step_max=0.06,
        anchor_kwargs={"anchor_target": 0.055})
    targets2 = _targets(calls2)
    assert sum(1 for t in targets2 if abs(t - 0.055) <= 1e-9) == 1, targets2
    assert targets2 == sorted(targets2), targets2

    # 低于 step_min 的锚点目标也必须在网格里（且排在最前）
    _, calls3, _ = _run_sensitivity(
        responder=_generic_responder(), step_min=0.05, step_max=0.06,
        anchor_kwargs={"anchor_target": 0.045})
    targets3 = _targets(calls3)
    assert targets3[0] == 0.045, targets3
    assert targets3 == sorted(targets3), targets3


def test_no_anchor_first_point_prev_then_seed():
    """② 无锚点扫描：首点 sleeve_start=None；之后松弛点 prev、绷紧点 λ=0.20 种子。"""
    def responder(target, alt_starts, sleeve_start, warnings):
        return _fake_solved(ASSETS, target)

    out, calls, seed_calls = _run_sensitivity(
        responder=responder, step_min=0.05, step_max=None)
    targets = _targets(calls)
    assert targets[:5] == [0.05, 0.055, 0.06, 0.065, 0.07], targets
    assert len(targets) == 6 and abs(targets[5] - 0.0719) < 1e-9, targets
    assert calls[0]["sleeve_start"] is None, calls[0]          # 首点：旧行为（无 canonical）
    assert calls[0]["alt_starts"] is None, calls[0]
    for call in calls[1:]:
        # 首点交付 5% → 切点估计 5%；之后目标都 > 切点 → λ=0.20 种子 canonical
        assert call["alt_starts"] is None, call
        assert call["sleeve_start"] == dict(SEED_VEC), call
    assert seed_calls and seed_calls[0]["lambdas"] == (0.20,), seed_calls
    assert out["points"][0]["chosen_start"] == "prev", out["points"][0]
    assert all(p["chosen_start"] == "seed" for p in out["points"][1:]), out["points"]
    assert all(p["fallback"] is False for p in out["points"]), out["points"]

def test_tight_points_use_anchor_sleeve_as_start():
    """切点估计 < target <= anchor_target：主结果袖参数 canonical 单次求解。

    v4：首点直接 anchor 起跑；slack 点复用（本例 0.055 一点）；
    锚点目标点直接 anchor；> anchor 段也用 anchor canonical 单次。
    """
    anchor_target = 0.066

    def responder(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.05) <= 1e-9:
            return _fake_solved(ASSETS_SLEEVE, target, ret=0.055)   # 首点交付 5.5%
        return _fake_solved(ASSETS_SLEEVE, target)

    out, calls, seed_calls = _run_sensitivity(
        responder=responder, assets=ASSETS_SLEEVE, step_min=0.05, step_max=0.07,
        anchor_kwargs={"anchor_target": anchor_target, "anchor_sleeve": dict(ANCHOR_SLEEVE)})
    # 0.055 <= 切点估计 5.5% -> slack 复用（不求解）-> 调用点只有 5 个
    assert _targets(calls) == [0.05, 0.06, 0.065, 0.066, 0.07], _targets(calls)
    prev_vec = {"O_US_CORE": (0.09, 0.12)}      # 上一点交付的袖参数
    # 边界带（切点+2pp 内且 <= anchor_target）：0.06/0.065 改 prev 起跑
    assert calls[1]["sleeve_start"] == prev_vec, calls[1]
    assert calls[2]["sleeve_start"] == prev_vec, calls[2]
    # 锚点目标点直接 anchor；> anchor 段 anchor canonical 单次
    assert calls[3]["sleeve_start"] == dict(ANCHOR_SLEEVE), calls[3]
    assert calls[4]["sleeve_start"] == dict(ANCHOR_SLEEVE), calls[4]
    for call in calls:
        assert call["alt_starts"] is None, call
    assert seed_calls == [], "锚点可用时不需要 seed"
    points = {round(p["target_return"], 4): p for p in out["points"]}
    assert len(out["points"]) == 6, out["points"]
    assert points[0.055]["chosen_start"] == "reuse", points[0.055]
    assert points[0.055]["achieved_return"] == points[0.05]["achieved_return"], points
    assert points[0.06]["chosen_start"] == "prev", points[0.06]
    assert points[0.065]["chosen_start"] == "prev", points[0.065]
    assert points[0.05]["chosen_start"] == "anchor", points[0.05]
    assert points[0.066]["chosen_start"] == "anchor", points[0.066]
    assert points[0.07]["chosen_start"] == "anchor", points[0.07]

def test_tight_points_without_anchor_sleeve_use_seed_start():
    """锚点可用但无袖参数：首点/绷紧段/右段都用 λ=0.20 种子单次求解，slack 复用。"""
    def responder(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.05) <= 1e-9:
            return _fake_solved(ASSETS_SLEEVE, target, ret=0.055)   # 首点交付 5.5%
        return _fake_solved(ASSETS_SLEEVE, target)

    out, calls, seed_calls = _run_sensitivity(
        responder=responder, assets=ASSETS_SLEEVE, step_min=0.05, step_max=0.07,
        anchor_kwargs={"anchor_target": 0.06})          # 无 anchor_sleeve
    assert seed_calls and seed_calls[0]["lambdas"] == (0.20,), seed_calls
    # 首点 seed；0.055 <= 切点估计 -> slack 复用；0.06 起 > anchor -> seed 单次
    assert _targets(calls) == [0.05, 0.06, 0.065, 0.07], _targets(calls)
    assert calls[0]["sleeve_start"] == dict(SEED_VEC), calls[0]
    for call in calls[1:]:
        assert call["sleeve_start"] == dict(SEED_VEC), call
        assert call["alt_starts"] is None, call
    points = {round(p["target_return"], 4): p for p in out["points"]}
    assert points[0.055]["chosen_start"] == "reuse", points[0.055]
    for target in (0.05, 0.06, 0.065, 0.07):
        assert points[target]["chosen_start"] == "seed", points[target]

def test_fallback_and_chosen_start_fields():
    """⑤ fallback 判定 + chosen_start 映射；首个 fallback 之后的点不再追加。"""
    plan = {
        0.05: {"chosen": None},              # 首点（旧行为）→ prev
        0.055: {"chosen": "lambda=0.35"},    # 引擎内建种子 → seed
        0.06: {"chosen": "base"},            # seed canonical 基路径 → seed
        0.065: {"lowered": True},            # 回退点 → fallback=True，扫描在此停止
        0.07: {},                            # 不应被求解
    }

    def responder(target, alt_starts, sleeve_start, warnings):
        item = plan.get(round(float(target), 4), {})
        return _fake_solved(ASSETS, target, chosen=item.get("chosen"),
                            lowered=item.get("lowered", False))

    out, calls, _ = _run_sensitivity(responder=responder, step_min=0.05, step_max=None)
    # 0.065 是 canonical(seed) 失败 → 一次独立 prev 重试（同点 2 次调用），然后早停
    assert _targets(calls) == [0.05, 0.055, 0.06, 0.065, 0.065], _targets(calls)
    by_target = {round(p["target_return"], 4): p for p in out["points"]}
    assert by_target[0.05]["chosen_start"] == "prev", by_target[0.05]
    assert by_target[0.05]["fallback"] is False, by_target[0.05]
    assert by_target[0.055]["chosen_start"] == "seed", by_target[0.055]   # lambda 种子
    assert by_target[0.06]["chosen_start"] == "seed", by_target[0.06]     # canonical 基路径
    assert by_target[0.065]["fallback"] is True, by_target[0.065]
    assert by_target[0.065]["chosen_start"] == "seed", by_target[0.065]   # 首跑可用性相同时保留
    assert 0.07 not in by_target, by_target

def test_step_max_caps_second_stage_points():
    """⑥ --step-max 是上限：第二阶段补点（以及最大可行收益那一点）不得越界。"""
    out, calls, _ = _run_sensitivity(
        responder=_generic_responder(), step_min=0.05, step_max=0.06)
    targets = _targets(calls)
    assert targets == [0.05, 0.055, 0.06], targets       # 旧实现会补到 7.2%
    assert max(targets) <= 0.06 + 1e-9, targets
    assert len(out["points"]) == 3, out["points"]
    assert _near(_targets(calls), 0.06), targets


def test_anchor_mismatch_warning_when_point_differs_from_main():
    """⑦ 锚点目标点与主结果不一致 → matched=False + sensitivity.anchor_mismatch 告警。

    Lead 2026-10-18 收口后 matched = **逐位一致**（ret/vol 差 ≤1e-6），不再是「未被支配」。
    """
    def responder(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.06) <= 1e-9:
            return _fake_solved(ASSETS, target, ret=0.06, vol=0.30)
        return _fake_solved(ASSETS, target, ret=target, vol=0.10)

    out, _, _ = _run_sensitivity(
        responder=responder, step_min=0.05, step_max=0.07,
        anchor_kwargs={"anchor_target": 0.06, "anchor_ret": 0.065, "anchor_vol": 0.10})
    block = out["anchor"]
    assert block["target"] == 0.06, block
    assert block["matched"] is False, block
    assert abs(block["point"]["ret"] - 0.06) < 1e-12, block
    assert abs(block["point"]["vol"] - 0.30) < 1e-12, block
    assert abs(block["main"]["sharpe"] - (0.065 - RF) / 0.10) < 1e-12, block
    hits = [w for w in out["warnings"]
            if getattr(w, "code", None) == "sensitivity.anchor_mismatch"]
    assert len(hits) == 1, out["warnings"]
    assert "与主结果不一致" in str(hits[0]), str(hits[0])
    assert json_ok(out) is True, "含 CodedWarning 的结果必须仍可 JSON 序列化"

    # 控制组：点与主结果逐位一致（同 ret/vol）→ matched=True、无该告警
    out2, _, _ = _run_sensitivity(
        responder=responder, step_min=0.05, step_max=0.07,
        anchor_kwargs={"anchor_target": 0.06, "anchor_ret": 0.06, "anchor_vol": 0.30})
    assert out2["anchor"]["matched"] is True, out2["anchor"]
    assert not [w for w in out2["warnings"]
                if getattr(w, "code", None) == "sensitivity.anchor_mismatch"], out2["warnings"]


def json_ok(payload):
    """结果 dict（含 str 子类告警）可 JSON 序列化（CodedWarning 是 str 子类）。"""
    import json
    try:
        json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return True


def test_invalid_anchor_is_ignored_with_one_stderr_hint():
    """⑧ 解析失败 / 袖 key 不匹配 → 忽略锚点 + 一条 stderr 提示，不失败（网格回到旧行为）。"""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out, calls, seed_calls = _run_sensitivity(
            responder=_generic_responder(), step_min=0.05, step_max=0.06,
            anchor_kwargs={"anchor_target": 0.06, "anchor_sleeve": "{not json"})
    text = err.getvalue()
    assert "[anchor]" in text and "已忽略锚点参数" in text, text
    assert "anchor" not in out, sorted(out)
    assert all(c["alt_starts"] is None for c in calls), calls
    # 忽略锚点后走无锚点模式：首点之后绷紧段用 λ=0.20 种子 canonical
    assert seed_calls and seed_calls[0]["lambdas"] == (0.20,), seed_calls

    # 袖 key 与资产不匹配（虚构资产里没有 O_US_CORE）→ 同样整体忽略
    err2 = io.StringIO()
    with contextlib.redirect_stderr(err2):
        out2, calls2, _ = _run_sensitivity(
            responder=_generic_responder(), step_min=0.05, step_max=0.06,
            anchor_kwargs={"anchor_target": 0.06,
                           "anchor_sleeve": {"O_US_CORE": [0.1, 0.2]}})
    assert "已忽略锚点参数" in err2.getvalue(), err2.getvalue()
    assert "anchor" not in out2, sorted(out2)
    assert all(c["alt_starts"] is None for c in calls2), calls2


def test_held_injection_wired_into_sensitivity_output():
    """⑨ 持仓纳入（契约 §1.4 末）：helper 的 corr 合并进求解、estimates/skipped 透传 "held"。"""
    import held_assets   # 祖先树没有该模块 → 该用例（以及整个文件）在祖先上必红

    saved_deps = {name: getattr(SENS, name) for name in (
        "live_anchors_from_extract", "ensure_work_dir", "load_pipeline",
        "build_open_pool_from_status", "build_investable_assets", "us_core_params")}
    saved_held = (held_assets.classify_held_assets, held_assets.inject_held_assets)
    recorded = {}

    def classify(extract, assets, hsbc_pool):
        return ([{"ticker": "OLD1"}],
                [{"ticker": "NEW1", "harmonized_ticker": "NEW1"}])

    def inject(extract, assets, *, work_dir, hsbc_pool, existing_keys, corr_keys,
               resolve, log=None):
        recorded.update({"work_dir": work_dir, "hsbc_pool": hsbc_pool,
                         "existing_keys": set(existing_keys), "corr_keys": list(corr_keys),
                         "resolve": resolve})
        held_asset = {"key": "H_NEW1", "name": "[持仓] NEW1", "fund_code": None,
                      "pool": "overseas", "mu": 0.08, "vol": 0.20,
                      "test_class": "active"}
        estimate = {"key": "H_NEW1", "ticker": "NEW1", "source": "yahoo",
                    "pool": "overseas", "mu": 0.08, "vol": 0.20,
                    "is_held": True, "asset_class": "other", "market": "US"}
        return (list(assets) + [held_asset], {("H_NEW1", "D1"): 0.25},
                [estimate], [("BAD1", "行情不可用")])

    SENS.live_anchors_from_extract = lambda extract: []
    SENS.ensure_work_dir = lambda path=None: "/tmp/虚构工作目录"
    SENS.load_pipeline = lambda work: (
        {"funds": [], "fetched_at": "2026-01-01T00:00:00"},
        {"funds": [], "checked_at": "2026-01-01T00:00:00"})
    SENS.build_open_pool_from_status = lambda funds, availability, live_anchors=None: (
        {}, {"categories": {}, "kept": 0})
    SENS.build_investable_assets = lambda *a, **kw: [dict(x) for x in ASSETS]
    SENS.us_core_params = lambda weights: (0.05, 0.10)
    held_assets.classify_held_assets = classify
    held_assets.inject_held_assets = inject

    made, saved_runner = _patch_runner(_generic_responder())
    extract = dict(EXTRACT)
    extract["held_assets"] = [{"ticker": "NEW1", "name": "虚构持仓"}]
    extract["include_unmodeled_holdings"] = True
    try:
        out = SENS.run_sensitivity(extract, step_min=0.04, step_max=0.04,
                                   log=lambda *a, **k: None)
    finally:
        SENS.SensitivityRunner = saved_runner
        for name, value in saved_deps.items():
            setattr(SENS, name, value)
        held_assets.classify_held_assets, held_assets.inject_held_assets = saved_held

    calls = made[0].calls if made else []
    assert calls, "扫描没有调用 solve_point"
    assert "H_NEW1" in calls[0]["assets"], calls[0]["assets"]
    assert calls[0]["corr_pairs"].get(("H_NEW1", "D1")) == 0.25, calls[0]["corr_pairs"]
    assert recorded["corr_keys"] == [a["key"] for a in ASSETS], recorded["corr_keys"]
    block = out["held"]
    assert [e["key"] for e in block["included"]] == ["H_NEW1"], block
    assert block["skipped"] == [{"ticker": "BAD1", "reason": "行情不可用"}], block
    assert block["counts"] == {"held": 1, "modeled": 1, "injected": 1, "skipped": 1}, block


def test_anchor_point_equals_main_result():
    """⑩ 锚点目标点与主结果一致（绿点=蓝点）：该点**直接**用主结果袖参数求解。

    Lead 2026-10-18 收口：target == anchor_target 时以 anchor_sleeve 为 base 初值
    （不传 prev、不传 alt），于是与主运行同一固定点 → 交付统计应与主结果逐位一致。
    """
    main_ret, main_vol = 0.06, 0.10
    main_sleeve = {"O_US_CORE": (0.08, 0.12)}

    def responder(target, alt_starts, sleeve_start, warnings):
        if sleeve_start == dict(main_sleeve):
            # 锚点目标点用主结果袖参数作初值 → 复现主结果（模拟同一固定点）
            return _fake_solved(ASSETS_SLEEVE, target, ret=main_ret, vol=main_vol)
        return _fake_solved(ASSETS_SLEEVE, target, ret=target, vol=0.25)

    anchor_target = 0.06     # 首个可行点（0.05）交付 0.05 → 切点估计 5%；6% 属绷紧区
    out, calls, _ = _run_sensitivity(
        responder=responder, assets=ASSETS_SLEEVE, step_min=0.05, step_max=0.06,
        anchor_kwargs={"anchor_target": anchor_target, "anchor_sleeve": dict(main_sleeve),
                       "anchor_ret": main_ret, "anchor_vol": main_vol})
    block = out["anchor"]
    assert block["matched"] is True, block
    assert abs(block["point"]["ret"] - main_ret) < 1e-12, block
    assert abs(block["point"]["vol"] - main_vol) < 1e-12, block
    assert abs(block["main"]["sharpe"] - (main_ret - RF) / main_vol) < 1e-12, block
    anchor_calls = [c for c in calls if abs(c["target"] - anchor_target) <= 1e-9]
    assert anchor_calls, _targets(calls)
    assert anchor_calls[0]["alt_starts"] is None, anchor_calls[0]
    assert anchor_calls[0]["sleeve_start"] == dict(main_sleeve), anchor_calls[0]
    anchor_point = [p for p in out["points"]
                    if abs(p["target_return"] - anchor_target) <= 1e-9][0]
    assert abs(anchor_point["achieved_return"] - block["point"]["ret"]) < 1e-12
    assert anchor_point["chosen_start"] == "anchor", anchor_point


def test_anchor_point_matches_main_result_with_real_engine():
    """⑩b 真引擎（虚构小池，离线）：锚点目标点的交付 == 主结果（绿点=蓝点）。

    装配桩只替换 extract → assets；求解走真实 core/engine（袖内固定点 + 阶段二）。
    主结果用与敏感性**完全相同**的输入（同一 corr / universe / target）先跑一次；
    敏感性在锚点目标点的基路径（冷启动）就是同一问题 → 该点必须与主结果逐位一致。
    """
    from portfolio_system.core import engine as ENG
    from portfolio_system.core.sleeve_params import SleeveUniverse, build_pool_specs

    dom_w, ov_w, total, rf = 0.25, 0.75, 1_000_000.0, 0.02
    fake_us = {"UA": {"name": "虚构A", "mu": 0.06, "vol": 0.10},
               "UB": {"name": "虚构B", "mu": 0.30, "vol": 0.18}}
    fake_jp = {"JC": {"name": "虚构C", "mu": 0.05, "vol": 0.12},
               "JD": {"name": "虚构D", "mu": 0.08, "vol": 0.15}}
    fake_gc = {"GE": {"name": "虚构E", "mu": 0.07, "vol": 0.16},
               "GF": {"name": "虚构F", "mu": 0.09, "vol": 0.20}}
    corr = {("UA", "UB"): 0.3, ("JC", "JD"): 0.3, ("GE", "GF"): 0.3}
    universe = SleeveUniverse(
        us_internal=fake_us, us_internal_corr=corr,
        jp_internal=fake_jp, jp_internal_corr=corr,
        gc_internal=fake_gc, gc_internal_corr=corr,
        sp500_internal_corr=corr, sp500_internal_corr_default=0.75,
        pool_specs=build_pool_specs(fake_us, corr, fake_jp, corr, fake_gc, corr),
        internal_pool_keys=tuple(fake_us) + tuple(fake_jp) + tuple(fake_gc))
    assets = [
        {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
         "pool": "domestic", "test_class": "broad"},
        {"key": "O_US_CORE", "name": "虚构美股核心袖", "mu": 0.02, "vol": 0.10,
         "pool": "overseas", "test_class": "broad"},
        {"key": "O_JP_EQ", "name": "虚构日本权益袖", "mu": 0.02, "vol": 0.10,
         "pool": "overseas", "test_class": "broad"},
        {"key": "D_GREATER_CN", "name": "虚构大中华袖", "mu": 0.02, "vol": 0.10,
         "pool": "overseas", "test_class": "broad"},
        {"key": "O_BROAD", "name": "虚构海外宽基", "mu": 0.09, "vol": 0.20,
         "pool": "overseas", "test_class": "broad"},
    ]
    extract = {
        "pool_mode": "fixture", "domestic_weight": dom_w, "overseas_weight": ov_w,
        "domestic_value": total * dom_w, "overseas_value": total * ov_w,
        "total_value": total, "rf": rf,
        "us_equity": {"total_value": total * ov_w,
                      "holdings": [{"ticker": "UA", "name": "虚构",
                                    "value_cny": total * ov_w, "weight": 1.0}]},
    }
    target = 0.05
    main = ENG.solve_portfolio(
        [dict(a) for a in assets], dict(SENS.BROAD_CORR),
        dom_w=dom_w, ov_w=ov_w, target=target, rf=rf, extract=extract,
        total_assets=total, universe=universe)
    saved_asm = _patch_assembly(assets)
    saved_universe = SENS._sleeve_universe
    SENS._sleeve_universe = lambda: universe
    try:
        out = SENS.run_sensitivity(
            dict(extract), step_min=target, step_max=target, log=lambda *a, **k: None,
            anchor_target=target, anchor_sleeve=main["sleeve_params"],
            anchor_ret=main["final"]["ret"], anchor_vol=main["final"]["vol"])
    finally:
        SENS.load_extract_assets = saved_asm
        SENS._sleeve_universe = saved_universe
    point = out["points"][0]
    assert point["target_return"] == target, point
    # achieved_return / volatility 是 6 位小数的展示值；与主结果的**全精度**比较，
    # 容差用展示精度 1e-6（与 anchor.matched 同口径），避免两次独立求解的末位舍入差。
    assert abs(point["achieved_return"] - main["final"]["ret"]) <= 1e-6, \
        (point, main["final"])
    assert abs(point["volatility"] - main["final"]["vol"]) <= 1e-6, \
        (point, main["final"])
    assert out["anchor"]["matched"] is True, out["anchor"]
    assert abs(out["anchor"]["point"]["ret"] - point["achieved_return"]) < 1e-12, out["anchor"]



def test_anchor_one_solve_per_non_slack_point():
    """锚定模式每个非 slack 点只求解 1 次；初值全部来自 canonical。"""
    anchor_target = 0.06

    def responder(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.05) <= 1e-9:
            return _fake_solved(ASSETS_SLEEVE, target, ret=0.05)   # 切点估计 5%
        return _fake_solved(ASSETS_SLEEVE, target)

    out, calls, _ = _run_sensitivity(
        responder=responder, assets=ASSETS_SLEEVE, step_min=0.05, step_max=0.07,
        anchor_kwargs={"anchor_target": anchor_target, "anchor_sleeve": dict(ANCHOR_SLEEVE)})
    assert len(calls) == len(out["points"]) == 5, (len(calls), len(out["points"]))
    assert _targets(calls) == [0.05, 0.055, 0.06, 0.065, 0.07], _targets(calls)
    prev_vec = {"O_US_CORE": (0.09, 0.12)}
    assert calls[0]["sleeve_start"] == dict(ANCHOR_SLEEVE), calls[0]   # 首点 canonical
    assert calls[1]["sleeve_start"] == prev_vec, calls[1]              # 边界带 prev 优先
    assert calls[2]["sleeve_start"] == dict(ANCHOR_SLEEVE), calls[2]   # 锚点目标点
    assert calls[3]["sleeve_start"] == dict(ANCHOR_SLEEVE), calls[3]   # > anchor
    assert calls[4]["sleeve_start"] == dict(ANCHOR_SLEEVE), calls[4]
    for call in calls:
        assert call["alt_starts"] is None, call

def test_stop_at_first_fallback():
    """⑫ 顺序扫描到**首个 fallback 点**即停（含一次独立重试后仍 fallback）。"""
    def responder(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.06) <= 1e-9:
            return _fake_solved(ASSETS, target, lowered=True)   # 该点目标被降级 → fallback
        return _fake_solved(ASSETS, target)

    out, calls, _ = _run_sensitivity(responder=responder, step_min=0.05, step_max=None)
    # 0.06 是 seed canonical 失败 → 一次独立 prev 重试（同点 2 次调用），仍 fallback → 停
    assert _targets(calls) == [0.05, 0.055, 0.06, 0.06], _targets(calls)
    assert len(out["points"]) == 3, out["points"]
    assert out["points"][-1]["fallback"] is True, out["points"][-1]
    assert isinstance(out["max_feasible_return"], float), out["max_feasible_return"]
    assert all(p["target_return"] <= 0.06 + 1e-9 for p in out["points"]), out["points"]

def test_stop_at_first_fallback_in_second_stage():
    """⑫b 第二阶段补点遇到 fallback 也 break（不把回退角点全跑一遍）。"""
    # 每点返回更高的 mu → max_ret_final(11.4%) > 初始模型(7.2%) → 第二阶段会补 7.5% 点
    HIGH_MU = [0.03, 0.05, 0.15]

    def responder(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.075) <= 1e-9:
            return _fake_solved(ASSETS, target, mu=HIGH_MU, lowered=True)
        return _fake_solved(ASSETS, target, mu=HIGH_MU)

    out, calls, _ = _run_sensitivity(
        responder=responder, step_min=0.05, step_max=0.08)
    assert abs(_targets(calls)[-1] - 0.075) < 1e-9, _targets(calls)
    # 0.075 两次调用（seed 首跑 fallback → 一次独立 prev 重试仍 fallback）
    assert len(calls) == 7, _targets(calls)
    assert out["points"][-1]["fallback"] is True, out["points"][-1]
    assert abs(max(p["target_return"] for p in out["points"]) - 0.075) < 1e-9, out["points"]
    assert all(p["target_return"] <= 0.075 + 1e-9 for p in out["points"]), out["points"]

def test_tight_canonical_failure_retries_once_with_prev():
    """一般绷紧段（切点估计 + 2pp 之外、<= anchor_target）：anchor 先，失败一次独立 prev 重试。

    边界带内反过来（prev 先、anchor 重试）；见 test_boundary_band_prev_first。
    """
    anchor_target = 0.10

    def responder(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.075) <= 1e-9 and sleeve_start == dict(ANCHOR_SLEEVE):
            return _fake_solved(HIGH_ASSETS, target, lowered=True)    # canonical 首跑 fallback
        if abs(target - 0.075) <= 1e-9:
            return _fake_solved(HIGH_ASSETS, target)                  # 独立 prev 重试成功
        return _fake_solved(HIGH_ASSETS, target)

    out, calls, _ = _run_sensitivity(
        responder=responder, assets=HIGH_ASSETS, step_min=0.05, step_max=0.09,
        anchor_kwargs={"anchor_target": anchor_target, "anchor_sleeve": dict(ANCHOR_SLEEVE)})
    target_calls = [c for c in calls if abs(c["target"] - 0.075) <= 1e-9]
    assert len(target_calls) == 2, target_calls
    assert target_calls[0]["sleeve_start"] == dict(ANCHOR_SLEEVE), target_calls[0]
    assert target_calls[1]["sleeve_start"] == {"O_US_CORE": (0.09, 0.12)}, target_calls[1]
    for call in target_calls:
        assert call["alt_starts"] is None, call        # 独立求解：绝不出现 base+alt 双跑
    point = [p for p in out["points"] if abs(p["target_return"] - 0.075) <= 1e-9][0]
    assert point["fallback"] is False, point
    assert point["chosen_start"] == "prev", point
    assert len(calls) == len(out["points"]) + 1, (len(calls), len(out["points"]))


def test_boundary_band_prev_first_then_anchor_retry():
    """边界带（切点估计 + 2pp 内、<= anchor_target、非锚点目标点）：prev 先、失败才 anchor 重试。"""
    anchor_target = 0.10

    def responder_ok(target, alt_starts, sleeve_start, warnings):
        return _fake_solved(HIGH_ASSETS, target)

    out, calls, _ = _run_sensitivity(
        responder=responder_ok, assets=HIGH_ASSETS, step_min=0.05, step_max=0.09,
        anchor_kwargs={"anchor_target": anchor_target, "anchor_sleeve": dict(ANCHOR_SLEEVE)})
    prev_vec = {"O_US_CORE": (0.09, 0.12)}
    by_target = {round(c["target"], 4): c for c in calls}
    # 首点交付 5% → 切点估计 5%；边界带 = (5%, 7%]：0.06/0.065/0.07 全部 prev 起跑
    for target in (0.06, 0.065, 0.07):
        assert by_target[target]["sleeve_start"] == prev_vec, by_target[target]
        assert by_target[target]["alt_starts"] is None, by_target[target]
    points = {round(p["target_return"], 4): p for p in out["points"]}
    for target in (0.06, 0.065, 0.07):
        assert points[target]["chosen_start"] == "prev", points[target]
    # 1 次/点（prev 可用 → 不做 anchor 重试）
    assert len(calls) == len(out["points"]), (len(calls), len(out["points"]))

    # prev 坏 → 该点 2 次：prev 首跑 fallback → 独立 anchor 重试
    def responder_prev_bad(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.06) <= 1e-9 and sleeve_start == prev_vec:
            return _fake_solved(HIGH_ASSETS, target, lowered=True)
        return _fake_solved(HIGH_ASSETS, target)

    out2, calls2, _ = _run_sensitivity(
        responder=responder_prev_bad, assets=HIGH_ASSETS, step_min=0.05, step_max=0.09,
        anchor_kwargs={"anchor_target": anchor_target, "anchor_sleeve": dict(ANCHOR_SLEEVE)})
    target_calls = [c for c in calls2 if abs(c["target"] - 0.06) <= 1e-9]
    assert len(target_calls) == 2, target_calls
    assert target_calls[0]["sleeve_start"] == prev_vec, target_calls[0]
    assert target_calls[1]["sleeve_start"] == dict(ANCHOR_SLEEVE), target_calls[1]
    for call in target_calls:
        assert call["alt_starts"] is None, call
    point = [p for p in out2["points"] if abs(p["target_return"] - 0.06) <= 1e-9][0]
    assert point["fallback"] is False, point
    assert point["chosen_start"] == "anchor", point

    # 无锚点袖参数：prev 坏 → 一次独立 seed 重试
    def responder_no_sleeve(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.06) <= 1e-9 and sleeve_start == prev_vec:
            return _fake_solved(HIGH_ASSETS, target, lowered=True)
        return _fake_solved(HIGH_ASSETS, target)

    out3, calls3, seed_calls = _run_sensitivity(
        responder=responder_no_sleeve, assets=HIGH_ASSETS, step_min=0.05, step_max=0.09,
        anchor_kwargs={"anchor_target": anchor_target})           # 无 anchor_sleeve
    target_calls = [c for c in calls3 if abs(c["target"] - 0.06) <= 1e-9]
    assert len(target_calls) == 2, target_calls
    assert target_calls[1]["sleeve_start"] == dict(SEED_VEC), target_calls[1]
    assert seed_calls and seed_calls[0]["lambdas"] == (0.20,), seed_calls
    point = [p for p in out3["points"] if abs(p["target_return"] - 0.06) <= 1e-9][0]
    assert point["chosen_start"] == "seed", point


def test_right_of_anchor_uses_anchor_single_and_stops():
    """target > anchor_target：anchor canonical 单次；fallback/未收敛直接判停、不重试。

    v3 的「先 prev、失败才 anchor」会让 0.215 这类点双跑约 82s（D3 实测），v4 直接单次。
    """
    anchor_target = 0.055

    def responder(target, alt_starts, sleeve_start, warnings):
        if abs(target - 0.06) <= 1e-9:
            return _fake_solved(ASSETS_SLEEVE, target, lowered=True)   # >anchor 点 fallback
        return _fake_solved(ASSETS_SLEEVE, target)

    out, calls, _ = _run_sensitivity(
        responder=responder, assets=ASSETS_SLEEVE, step_min=0.05, step_max=0.07,
        anchor_kwargs={"anchor_target": anchor_target, "anchor_sleeve": dict(ANCHOR_SLEEVE)})
    target_calls = [c for c in calls if abs(c["target"] - 0.06) <= 1e-9]
    assert len(target_calls) == 1, target_calls          # 只有一次 anchor 求解，没有重试
    assert target_calls[0]["sleeve_start"] == dict(ANCHOR_SLEEVE), target_calls[0]
    assert target_calls[0]["alt_starts"] is None, target_calls[0]
    assert out["points"][-1]["target_return"] == 0.06, out["points"][-1]
    assert out["points"][-1]["fallback"] is True, out["points"][-1]
    assert all(p["target_return"] <= 0.06 + 1e-9 for p in out["points"]), out["points"]

    def responder_ok(target, alt_starts, sleeve_start, warnings):
        return _fake_solved(ASSETS_SLEEVE, target)

    out2, calls2, _ = _run_sensitivity(
        responder=responder_ok, assets=ASSETS_SLEEVE, step_min=0.05, step_max=0.07,
        anchor_kwargs={"anchor_target": anchor_target, "anchor_sleeve": dict(ANCHOR_SLEEVE)})
    assert len(calls2) == len(out2["points"]), (len(calls2), len(out2["points"]))
    assert _targets(calls2) == [0.05, 0.055, 0.06, 0.065, 0.07], _targets(calls2)
    for call in calls2:
        assert call["sleeve_start"] == dict(ANCHOR_SLEEVE), call

def test_anchor_first_point_uses_canonical_start():
    """锚定模式首点直接用 canonical 起跑：有袖参数 → anchor；无 → λ=0.20 种子。"""
    def responder(target, alt_starts, sleeve_start, warnings):
        return _fake_solved(ASSETS_SLEEVE, target)

    out, calls, _ = _run_sensitivity(
        responder=responder, assets=ASSETS_SLEEVE, step_min=0.05, step_max=0.07,
        anchor_kwargs={"anchor_target": 0.06, "anchor_sleeve": dict(ANCHOR_SLEEVE)})
    assert calls[0]["target"] == 0.05, calls[0]
    assert calls[0]["sleeve_start"] == dict(ANCHOR_SLEEVE), calls[0]
    assert calls[0]["alt_starts"] is None, calls[0]
    assert out["points"][0]["chosen_start"] == "anchor", out["points"][0]

    out2, calls2, seed_calls = _run_sensitivity(
        responder=responder, assets=ASSETS_SLEEVE, step_min=0.05, step_max=0.07,
        anchor_kwargs={"anchor_target": 0.06})
    assert calls2[0]["sleeve_start"] == dict(SEED_VEC), calls2[0]
    assert seed_calls and seed_calls[0]["lambdas"] == (0.20,), seed_calls
    assert out2["points"][0]["chosen_start"] == "seed", out2["points"][0]



#: 高 mu 虚构资产：让初始模型最大可行收益高于 slack 段目标（便于测 0.05-0.12 网格）
HIGH_ASSETS = ASSETS_SLEEVE + [
    {"key": "O_HIGH", "name": "虚构高收益资产", "mu": 0.20, "vol": 0.30,
     "pool": "overseas", "test_class": "broad"},
]


def test_anchor_slack_points_reuse_without_solving():
    """slack 点**不调 solve_point**：直接复用上一个 slack 点的交付字段，target 用新值。"""
    def responder(target, alt_starts, sleeve_start, warnings):
        # 任意初值交付同一组切点组合（ret=9%/vol=8%）-> 切点估计 9%；
        # T<=9% 的点（除首点/锚点目标点）全部复用，不求解；>9% 的点照常沿前沿交付目标。
        return _fake_solved(HIGH_ASSETS, target, ret=max(float(target), 0.09), vol=0.08)

    out, calls, _ = _run_sensitivity(
        responder=responder, assets=HIGH_ASSETS, step_min=0.05, step_max=0.12,
        anchor_kwargs={"anchor_target": 0.06, "anchor_sleeve": dict(ANCHOR_SLEEVE)})
    targets = [round(p["target_return"], 4) for p in out["points"]]
    assert targets == [round(0.05 + 0.005 * i, 4) for i in range(15)], targets
    # 求解的只有：首点 0.05、锚点目标点 0.06、以及 > 切点估计的 0.095-0.12（6 点）
    assert _targets(calls) == [0.05, 0.06, 0.095, 0.10, 0.105, 0.11, 0.115, 0.12], _targets(calls)
    by_target = {round(p["target_return"], 4): p for p in out["points"]}
    reused = [0.055, 0.065, 0.07, 0.075, 0.08, 0.085, 0.09]
    for target in reused:
        point = by_target[target]
        assert point["chosen_start"] == "reuse", point
        assert abs(point["achieved_return"] - 0.09) < 1e-12, point      # 交付统计不变
        assert abs(point["volatility"] - 0.08) < 1e-12, point
        assert point["fallback"] is False, point
        assert target not in _targets(calls), (target, _targets(calls))  # 没调用 solve_point
    # 锚点目标点仍是直接的 anchor 初值求解（不是复用）
    assert by_target[0.06]["chosen_start"] == "anchor", by_target[0.06]

def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
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
