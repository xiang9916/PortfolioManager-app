#!/usr/bin/env python3
"""PresetRunner 编排路径的离线单测（main() 瘦身的**跟踪**覆盖；不联网、全虚构）。

背景：main() 瘦身后，「取数 → 装配 → 求解 → 结果汇总」搬进
callers/preset_runner.PresetRunner._run_pipeline。tmp/ 里的逐字节等价门不入库，
本文件用**受控桩**锁住该路径的契约（不动数值、不依赖真实数据）：

  ① 成功路径：payload 键结构 / 池与目标 / stage1 权重 / 国内选基 / sleeve / context；
  ② 错误分级：管线失败 → PipelineUnavailable；无境内基金 → NoDomesticAssets；
     求解不可行 → InfeasibleOptimization（都由 CLI 层映射为既有退出码）；
  ③ 告警顺序：校准告警在前、CAP_HITS 合并告警在后，warning_items 由最终列表生成；
  ④ 选基规则（select_domestic_funds）：锚定基金优先、否则候选第一、零权重大类跳过；
  ⑤ 无 deps 时不进编排模式（既有求解路径保持不变 = 等价门用的那条）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_preset_pipeline_offline.py
"""
import os
import sys

import numpy as np

_SCRIPTS = os.path.abspath(os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
_PKG_ROOT = os.path.dirname(_SCRIPTS)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from portfolio_system.callers.base_runner import RunRequest  # noqa: E402
from portfolio_system.models.data_types import SolveResult  # noqa: E402
from portfolio_system.callers.preset_runner import (  # noqa: E402
    InfeasibleOptimization, NoDomesticAssets, PipelineUnavailable, PresetDeps, PresetRunner,
    select_domestic_funds)
from portfolio_system.core.warnings import CodedWarning  # noqa: E402

DOM_W, OV_W, TOTAL, RF, TARGET = 0.40, 0.60, 1_000_000.0, 0.02, 0.04

EXTRACT = {
    "pool_mode": "fixture",
    "domestic_weight": DOM_W,
    "overseas_weight": OV_W,
    "domestic_value": TOTAL * DOM_W,
    "overseas_value": TOTAL * OV_W,
    "total_value": TOTAL,
    "rf": RF,
    "us_equity": {"holdings": [{"ticker": "FX", "weight": 1.0}]},
}

ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05, "pool": "domestic"},
    {"key": "O_GOLD", "name": "虚构黄金", "mu": 0.05, "vol": 0.15, "pool": "overseas"},
]

HSBC = {"funds": [{"code": "F1"}], "fetched_at": "FIXTURE",
        "total_count": 1, "open_count": 1}
AVAIL = {"funds": {"F1": {"status": "open"}}, "checked_at": "FIXTURE",
         "open_count": 1, "stats": {"hsbc_fallback": 0}, "elapsed_seconds": 0.0,
         "source": "fixture", "base_url": "fixture"}
POOL = {"D1": [{"code": "F1", "name": "虚构基金"}]}
POOL_META = {"kept": 1, "categories": []}


def _solved(assets=None, target=TARGET):
    assets = list(ASSETS if assets is None else assets)
    weights = np.array([0.4, 0.6][:len(assets)])
    ret, vol = 0.042, 0.08
    return {
        "assets": assets, "keys": [a["key"] for a in assets],
        "stage1_weights": weights, "stage1_ret": ret, "stage1_vol": vol,
        "stage1_sharpe": (ret - RF) / vol, "target": target,
        "internal": None, "internal_tickers": None, "stage2_error": None,
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": ret, "vol": vol, "sharpe": (ret - RF) / vol,
                  "floor": ret - 1.96 * vol},
        "sleeve_params": {}, "sleeve_iterations": 1, "sleeve_converged": True,
        "sleeve_history": [], "deliverable": True, "final_source": "delivered",
    }


def _deps(**over):
    """受控桩：全部外部依赖都是确定性 lambda（不联网、不读文件）。"""
    values = dict(
        calibration_warnings=["虚构校准提示"],
        us_core_params=lambda weights: (0.06, 0.12),
        build_investable_assets=lambda *a, **k: [dict(x) for x in ASSETS],
        build_open_pool=lambda *a, **k: (POOL, POOL_META),
        ensure_pipeline=lambda *a, **k: (HSBC, AVAIL),
        ensure_work_dir=lambda path=None: "/fixture-work",
        live_anchors_from_extract=lambda extract: [],
        # 与 NewAssetRunner.inject_test_assets 同契约：无 raw_tickers 时原样返回
        inject_test_assets=lambda assets, **k: (assets, None, [], []),
        resolve_test_assets=lambda *a, **k: ([], {}, [], []),
        internal_keys=(), corr_keys=("D1", "O_GOLD"), class_rules={},
        test_skip_warning=lambda t, r: CodedWarning("%s: %s" % (t, r), "test.ticker_skipped"),
        solve_portfolio=lambda *a, **k: _solved(),
        broad_corr={}, parse_test_tickers=lambda raw: [t for t in raw.split(",") if t],
        class_param_help="fixture",
        cap_hits=[], cap_hit_warning=lambda hits: CodedWarning("cap fixture", "active_cap.hit"),
        warning_items_fn=lambda ws: [{"code": getattr(w, "code", "other"), "message": str(w)}
                                     for w in ws],
    )
    values.update(over)
    return PresetDeps(**values)


def _request(deps):
    return RunRequest(
        assets=(), target_return=TARGET, rf=RF, domestic_weight=DOM_W,
        overseas_weight=OV_W, total_assets=TOTAL, extract=dict(EXTRACT),
        deps=deps, work_dir="/fixture-work", warnings_out=[])


def _run(deps=None):
    return PresetRunner(optimizer=None).run(_request(_deps() if deps is None else deps))


def test_pipeline_payload_contract():
    """① 成功路径：结果页 JSON 的键结构与关键值（唯一装配点）。"""
    outcome = _run()
    payload = outcome.payload
    assert outcome.result is None
    assert payload["pool"]["total_assets_used"] == TOTAL, payload["pool"]
    assert payload["pool"]["domestic_weight"] == DOM_W
    assert payload["stage1"]["weights"] == {"D1": 0.4, "O_GOLD": 0.6}, payload["stage1"]
    assert payload["target_return"] == TARGET
    assert payload["objective"] == "max_sharpe"
    assert payload["sleeve"] == {"params": {}, "iterations": 1, "converged": True,
                                 "history": []}
    # 零权重大类不进选基清单；锚定/候选逻辑见 ④
    assert list(payload["domestic_selection"]) == ["D1"], payload["domestic_selection"]
    assert payload["domestic_selection"]["D1"]["selected"]["code"] == "F1"
    assert payload["pipeline"]["work_dir"] == "/fixture-work"
    assert outcome.context["hsbc_pool"] == POOL
    assert payload["warning_items"][0]["message"] == "虚构校准提示"


def test_pipeline_error_tiers():
    """② 错误分级：三类失败各自抛专用异常（CLI 层据此映射退出码）。"""
    def _boom(*a, **k):
        raise RuntimeError("管线炸了")

    try:
        _run(_deps(ensure_pipeline=_boom))
    except PipelineUnavailable as exc:
        assert exc.detail == "管线炸了"
        assert exc.warnings == ["虚构校准提示"], exc.warnings
    else:
        raise AssertionError("管线失败必须抛 PipelineUnavailable")

    overseas_only = [{"key": "O_GOLD", "name": "虚构黄金", "mu": 0.05, "vol": 0.15,
                      "pool": "overseas"}]
    try:
        _run(_deps(build_investable_assets=lambda *a, **k: [dict(x) for x in overseas_only]))
    except NoDomesticAssets as exc:
        assert any(getattr(w, "code", "") == "fund.available_none" for w in exc.warnings), \
            exc.warnings
    else:
        raise AssertionError("无可申购境内基金必须抛 NoDomesticAssets")

    try:
        _run(_deps(solve_portfolio=lambda *a, **k: None))
    except InfeasibleOptimization:
        pass
    else:
        raise AssertionError("求解不可行必须抛 InfeasibleOptimization")


def test_pipeline_warning_order_and_cap_hits():
    """③ 告警顺序：校准告警 →（CAP_HITS 合并）→ warning_items 以最终列表重算。"""
    hits = []
    # CAP_HITS 在编排开始时会被 clear()（与旧 main 同口径）→ 触顶必须发生在求解期间
    outcome = _run(_deps(cap_hits=hits,
                         solve_portfolio=lambda *a, **k: (
                             hits.append(("O_GOLD", 0.10, 0.10)) or _solved())))
    payload = outcome.payload
    assert str(payload["warnings"][0]) == "虚构校准提示", payload["warnings"]
    assert str(payload["warnings"][-1]) == "cap fixture", payload["warnings"]
    assert [w["message"] for w in payload["warning_items"]] == \
        [str(w) for w in payload["warnings"]], payload["warning_items"]
    assert payload["warning_items"][-1]["code"] == "active_cap.hit"


def test_select_domestic_funds_rules():
    """④ 选基规则：锚定基金优先 → 否则候选第一；权重 0 的大类跳过。"""
    weights = np.array([0.4, 0.6])
    assets = [
        {"key": "D1", "pool": "domestic", "fund_code": "F2"},
        {"key": "D2", "pool": "domestic"},
    ]
    pool = {"D1": [{"code": "F1"}, {"code": "F2"}], "D2": [{"code": "F3"}]}
    sel = select_domestic_funds(weights, assets, pool, 1000.0)
    assert sel["D1"]["selected"]["code"] == "F2"          # 锚定优先
    assert round(sel["D1"]["amount"], 6) == 400.0
    assert sel["D2"]["selected"]["code"] == "F3"          # 无锚定 → 候选第一
    assert set(sel) == {"D1", "D2"}


def test_no_deps_keeps_legacy_solve_path():
    """⑤ 无 deps 时不进编排模式：仍是 assemble_problem → optimizer.optimize（等价门路径）。"""
    calls = []

    class _Optimizer:
        def optimize(self, problem):
            calls.append(problem)
            return SolveResult(weights={"D1": 1.0},
                               stats={"ret": 0.03, "vol": 0.05, "sharpe": 0.2, "floor": 0.0})

    outcome = PresetRunner(optimizer=_Optimizer()).run(RunRequest(
        assets=(), target_return=TARGET, rf=RF, domestic_weight=DOM_W,
        overseas_weight=OV_W, extract=dict(EXTRACT)))
    assert calls, "无 deps 时必须走 optimizer.optimize"
    assert outcome.result is not None and outcome.payload["weights"] == {"D1": 1.0}, outcome


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
