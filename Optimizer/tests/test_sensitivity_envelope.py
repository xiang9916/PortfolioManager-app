#!/usr/bin/env python3
"""敏感性候选包络 + 支配修复（2026-10-25，契约 §2.3）守卫 —— 纯离线、受控桩、虚构。

背景：扫描逐点用「上一点热启动」，会收敛到一个**较差但自洽**的固定点（1–2 轮即
converged）。某目标点因此低于「更高目标点交付的组合在该目标下」的夏普 —— 后者在该点
同样可行且更优 → 该点被支配、不是 max。用户的定理（固定资产级 μ/Σ 下目标↑ ⇒
max 夏普不增）本身成立。

修法：候选包络（每点 = 所有可交付候选中 ret ≥ T 的夏普最大者；可行集随 T 收缩 ⇒
必然非增）+ 支配修复（被支配点先用主结果袖参数重解一次，修不掉借用包络并标
source_target）。

本文件锁定：
  ① 被支配点 → 包络/修复后序列非增（repaired=true）；
  ② monotone_envelope=False（旧 start-policy 回归用）保留旧倒挂；
  ③ 无 anchor（CLI 场景）→ 只做包络：该点借用更高目标的候选并标 source_target；
  ④ 不可交付候选（final_source=stage1_model）不进包络；
  ⑤ 已经单调的输入 → 数值不变、无 source_target/repaired、请求数不变（包络是纯后处理）；
  ⑥ 锚点目标点仍与主结果逐位一致（anchor.matched=true）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_sensitivity_envelope.py
祖先对照：PM_OPT_SCRIPTS=<祖先>/Optimizer/scripts（祖先没有包络参数 → TypeError）。
"""
import os
import sys

import numpy as np

_SCRIPTS = os.path.abspath(os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
_PKG_ROOT = os.path.dirname(_SCRIPTS)
for _p in (_SCRIPTS, _PKG_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sensitivity_analysis as SENS  # noqa: E402
from portfolio_system.core.sleeve_params import SLEEVE_KEYS  # noqa: E402

DOM_W, OV_W, TOTAL, RF = 0.30, 0.70, 1_000_000.0, 0.02
ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_GOLD", "name": "虚构黄金", "mu": 0.05, "vol": 0.15,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_BROAD", "name": "虚构海外宽基", "mu": 0.20, "vol": 0.25,
     "pool": "overseas", "test_class": "broad"},
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
ANCHOR_SLEEVE = {"O_US_CORE": (0.08, 0.12)}
SEED_VEC = {"O_US_CORE": (0.11, 0.12)}
ANCHOR_TARGET = 0.10
ANCHOR_VOL = 0.05755          # (0.10-0.02)/1.390


def _solved(assets, target, *, ret, vol, deliverable=True, final_source="delivered",
            converged=True):
    """与引擎 result 同形的受控桩结果（fake runner 返回它）。"""
    keys = [str(a["key"]) for a in assets]
    return {
        "assets": list(assets), "keys": keys,
        "mu": np.array([float(a["mu"]) for a in assets], dtype=float),
        "cov": np.eye(len(assets)),
        "sleeve_params": {k: [0.09, 0.12] for k in keys if k in SLEEVE_KEYS},
        "final": {"ret": float(ret), "vol": float(vol),
                  "sharpe": (float(ret) - RF) / float(vol) if vol else 0.0,
                  "floor": float(ret) - 1.96 * float(vol)},
        "target": float(target),
        "stage1_ret": float(ret), "stage1_vol": float(vol),
        "deliverable": bool(deliverable),
        "final_source": final_source,
        "sleeve_converged": bool(converged),
        "warnings": [],
    }


def _base_responder(bad95=True):
    """7.0% 切点 1.52；8.0/8.5/9.0 平滑下降；9.5 坏盆地 1.353（或好 1.45）；10.0 1.39。"""
    def responder(target, alt_starts, sleeve_start, warnings):
        t = round(float(target), 4)
        if t == 0.07:
            return _solved(ASSETS, t, ret=0.0785, vol=0.0385)     # (0.0785-0.02)/0.0385 = 1.5195
        if t == 0.08:
            return _solved(ASSETS, t, ret=0.08, vol=0.04)         # 1.50
        if t == 0.085:
            return _solved(ASSETS, t, ret=0.085, vol=0.04362)     # 1.49
        if t == 0.09:
            return _solved(ASSETS, t, ret=0.09, vol=0.04730)      # 1.48
        if t == 0.095:
            if sleeve_start == ANCHOR_SLEEVE:
                return _solved(ASSETS, t, ret=0.095, vol=0.05172)  # 1.45（修复）
            if bad95:
                return _solved(ASSETS, t, ret=0.095, vol=0.05542)  # 1.353（坏盆地）
            return _solved(ASSETS, t, ret=0.095, vol=0.05172)      # 1.45
        if t == 0.10:
            return _solved(ASSETS, t, ret=0.10, vol=ANCHOR_VOL)    # 1.39
        return _solved(ASSETS, t, ret=t, vol=0.05)
    return responder


def _patch_runner(responder):
    saved = SENS.SensitivityRunner
    made = []

    class _FakeRunner:
        def __init__(self, optimizer=None, universe=None):
            self.universe = universe
            self.calls = []
            made.append(self)

        def solve_point(self, assets, corr_pairs, *, dom_w, ov_w, target, rf, extract,
                        total_assets, sleeve_start=None, warnings=None, alt_starts=None):
            self.calls.append({"target": float(target), "sleeve_start": sleeve_start,
                               "alt_starts": alt_starts})
            return responder(float(target), alt_starts, sleeve_start, warnings)

    SENS.SensitivityRunner = _FakeRunner
    return made, saved


def _patch_assembly():
    saved = SENS.load_extract_assets
    fixed = [dict(a) for a in ASSETS]

    def stub(extract, work_dir, **kw):
        return fixed, {}, [], {"source": "fixture"}

    SENS.load_extract_assets = stub
    return saved


def _patch_seed():
    saved = getattr(SENS, "restart_seed_params", None)
    existed = hasattr(SENS, "restart_seed_params")

    def fake(rf, pool_specs, lambdas):
        return [("lambda=%.2f" % float(lambdas[0]), dict(SEED_VEC))]

    def restore():
        if existed:
            SENS.restart_seed_params = saved
        elif hasattr(SENS, "restart_seed_params"):
            del SENS.restart_seed_params

    SENS.restart_seed_params = fake
    return restore


def _run(responder, *, anchor=True, envelope=True, step_min=0.07, step_max=0.10):
    made, saved_runner = _patch_runner(responder)
    saved_asm = _patch_assembly()
    restore_seed = _patch_seed()
    try:
        kwargs = dict(extract=dict(EXTRACT), step_min=step_min, step_max=step_max,
                      log=lambda *a, **k: None, monotone_envelope=envelope)
        if anchor:
            kwargs.update(anchor_target=ANCHOR_TARGET, anchor_sleeve=dict(ANCHOR_SLEEVE),
                          anchor_ret=0.10, anchor_vol=ANCHOR_VOL)
        out = SENS.run_sensitivity(**kwargs)
    finally:
        SENS.SensitivityRunner = saved_runner
        SENS.load_extract_assets = saved_asm
        restore_seed()
    return out, (made[0].calls if made else [])


def _by_target(points):
    return {round(p["target_return"], 4): p for p in points}


def _deliverable_sharpes(out):
    return [p["sharpe"] for p in out["points"]
            if p.get("feasible") and not p.get("fallback")]


def _assert_non_increasing(sharpes):
    assert all(b <= a + 1e-6 for a, b in zip(sharpes, sharpes[1:])), sharpes


# ──────────────────────────────────────────────────────────────────────────────
def test_envelope_repairs_dominated_point():
    """① 9.5 被 10.0 支配 → 主结果袖参数重解到 1.450；序列非增；锚点仍匹配。"""
    out, calls = _run(_base_responder())
    pts = _by_target(out["points"])
    assert pts[0.095]["repaired"] is True, pts[0.095]
    assert abs(pts[0.095]["sharpe"] - 1.45) < 5e-4, pts[0.095]
    assert abs(pts[0.095]["achieved_return"] - 0.095) < 1e-9, pts[0.095]
    assert pts[0.095].get("source_target") is None, pts[0.095]
    _assert_non_increasing(_deliverable_sharpes(out))
    assert out["anchor"]["matched"] is True, out["anchor"]
    repairs = [c for c in calls
               if abs(c["target"] - 0.095) < 1e-9
               and c["sleeve_start"] == dict(ANCHOR_SLEEVE)]
    assert len(repairs) == 1, calls
    assert out["scope"].startswith("候选包络"), out["scope"]


def test_legacy_off_keeps_inversion():
    """② monotone_envelope=False 保留旧倒挂（start-policy 回归路径不被新层影响）。"""
    out, _ = _run(_base_responder(), envelope=False)
    pts = _by_target(out["points"])
    assert abs(pts[0.095]["sharpe"] - 1.353) < 5e-4, pts[0.095]
    assert "repaired" not in pts[0.095], pts[0.095]
    assert out["scope"].startswith("完整流水线"), out["scope"]


def test_no_anchor_envelope_borrows_candidate():
    """③ 无 anchor（CLI）：只做包络，9.5 借用 10.0 的 1.390，source_target=10%。"""
    out, calls = _run(_base_responder(), anchor=False)
    pts = _by_target(out["points"])
    assert 1.38 < pts[0.095]["sharpe"] < 1.40, pts[0.095]
    assert abs(pts[0.095]["source_target"] - 0.10) < 1e-9, pts[0.095]
    assert "repaired" not in pts[0.095], pts[0.095]
    _assert_non_increasing(_deliverable_sharpes(out))
    # 只包络、不修复：0.095 只有扫描自身那一次求解
    assert sum(1 for c in calls if abs(c["target"] - 0.095) < 1e-9) == 1, calls


def test_non_deliverable_candidate_not_used():
    """④ final_source=stage1_model（不可交付）的候选不进包络。"""
    def responder(target, alt_starts, sleeve_start, warnings):
        t = round(float(target), 4)
        if t == 0.10:
            # 高夏普但不可交付：st=stage1_model（模型统计，不是交付组合）
            return _solved(ASSETS, t, ret=0.10, vol=0.01,
                           deliverable=False, final_source="stage1_model")
        if t == 0.095:
            if sleeve_start == ANCHOR_SLEEVE:
                return _solved(ASSETS, t, ret=0.095, vol=0.05172)   # 1.45
            return _solved(ASSETS, t, ret=0.095, vol=0.05542)       # 1.353
        if t == 0.09:
            return _solved(ASSETS, t, ret=0.09, vol=0.04730)
        if t == 0.085:
            return _solved(ASSETS, t, ret=0.085, vol=0.04362)
        if t == 0.08:
            return _solved(ASSETS, t, ret=0.08, vol=0.04)
        if t == 0.07:
            return _solved(ASSETS, t, ret=0.0785, vol=0.0385)
        return _solved(ASSETS, t, ret=t, vol=0.05)

    out, _ = _run(responder)
    pts = _by_target(out["points"])
    # 10.0 的 8.0 夏普（vol=1%）不得被 9.5 借用
    assert pts[0.095]["sharpe"] < 2.0, pts[0.095]
    assert abs(pts[0.095]["sharpe"] - 1.45) < 5e-4, pts[0.095]
    # 10.0 自己也不显示不可交付的模型统计（回落到主结果锚点 1.39）
    assert pts[0.10]["sharpe"] < 2.0, pts[0.10]
    assert abs(pts[0.10]["sharpe"] - 1.39) < 5e-3, pts[0.10]


def test_envelope_noop_when_already_monotone():
    """⑤ 已单调输入：数值不变、无 source_target/repaired、请求数不变（纯后处理）。"""
    out_on, calls_on = _run(_base_responder(bad95=False))
    out_off, calls_off = _run(_base_responder(bad95=False), envelope=False)
    assert len(out_on["points"]) == len(out_off["points"])
    for a, b in zip(out_on["points"], out_off["points"]):
        for key in ("target_return", "achieved_return", "volatility", "sharpe",
                    "worst_year_95", "cagr_lognormal", "cagr_fat_tail"):
            assert abs(float(a[key]) - float(b[key])) <= 1e-6, (key, a, b)
        assert "source_target" not in a, a
        assert "repaired" not in a, a
    assert len(calls_on) == len(calls_off), (len(calls_on), len(calls_off))
    _assert_non_increasing(_deliverable_sharpes(out_on))


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  OK " + name)
        except AssertionError as exc:
            failed += 1
            print("  FAIL " + name + ": " + str(exc)[:600])
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc)[:600])
    print("\n%d/%d 通过" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
