#!/usr/bin/env python3
"""task-31 加固三项回归测试（不联网、全虚构 fixture）。

覆盖：
 ① 袖给不出篮子时 **不得**把阶段一模型统计当交付 —— 先按 0 权重剔除后重解（真有解的路径），
    重解不成则显式 deliverable=False + final_source=stage1_model + 可机读 code（不许静默降级）。
 ② ε 只保留**单次**语义 ε（交付断言处）：袖内下限 = 需求本身，实际保证「交付收益 ≥ 目标 − ε」。
 ③ 择优按**交付目标**优先（同输入同输出，确定性 tie-break），并给出极限环可诊断振幅；
    不承诺无条件收敛（边界写在输出与注释里）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_robustness.py
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

DOM_W, OV_W = 0.30, 0.70
TOTAL = 1_000_000.0
RF = 0.02
TARGET = 0.18

FAKE_US = {"UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.10},
           "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.18}}
FAKE_JP = {"JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.12},
           "JD": {"name": "虚构日股D", "mu": 0.35, "vol": 0.45}}
FAKE_GC = {"GD": {"name": "虚构大中华D", "mu": 0.04, "vol": 0.10}}
FAKE_CORR = {("UA", "UB"): 0.3, ("JC", "JD"): 0.3}

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
]

EXTRACT = {
    "pool_mode": "fixture", "domestic_weight": DOM_W, "overseas_weight": OV_W,
    "domestic_value": TOTAL * DOM_W, "overseas_value": TOTAL * OV_W,
    "total_value": TOTAL, "rf": RF,
    "us_equity": {"total_value": TOTAL * OV_W,
                  "holdings": [{"ticker": "UA", "name": "虚构", "value_cny": TOTAL * OV_W,
                                "weight": 1.0}]},
}


class patched_pools:
    def __init__(self, us=None, jp=None, gc=None):
        self.us, self.jp, self.gc = us or FAKE_US, jp or FAKE_JP, gc or FAKE_GC

    def __enter__(self):
        self.saved = (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
                      getattr(OP, "GC_INTERNAL", None), getattr(OP, "GC_INTERNAL_CORR", None),
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        OP.US_INTERNAL, OP.US_INTERNAL_CORR = self.us, FAKE_CORR
        OP.JP_INTERNAL, OP.JP_INTERNAL_CORR = self.jp, FAKE_CORR
        OP.GC_INTERNAL, OP.GC_INTERNAL_CORR = self.gc, FAKE_CORR
        OP._SLEEVE_POOL_SPECS = {"O_US_CORE": (self.us, FAKE_CORR, 19),
                                 "O_JP_EQ": (self.jp, FAKE_CORR, 31),
                                 "D_GREATER_CN": (self.gc, FAKE_CORR, 43)}
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         OP.GC_INTERNAL, OP.GC_INTERNAL_CORR, saved_specs) = self.saved
        if saved_specs is None:
            del OP._SLEEVE_POOL_SPECS
        else:
            OP._SLEEVE_POOL_SPECS = saved_specs
        return False


def _run(stub, target=TARGET, max_sleeve_iter=3):
    with patched_pools():
        original = OP._solve_once
        OP._solve_once = stub
        try:
            return OP.solve_portfolio([dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W,
                                      target=target, rf=RF, extract=EXTRACT, total_assets=TOTAL,
                                      warnings=[], log=None, max_sleeve_iter=max_sleeve_iter,
                                      stage1_starts=40, stage2_starts=20)
        finally:
            OP._solve_once = original


def _model_fields(assets_in, sleeve_mu=None):
    n = len(assets_in)
    mu = np.array([float(a["mu"]) for a in assets_in])
    if sleeve_mu:
        for i, a in enumerate(assets_in):
            if a["key"] in sleeve_mu:
                mu[i] = float(sleeve_mu[a["key"]])
    return {"mu": mu, "lower": np.zeros(n), "upper": np.ones(n),
            "dom_idx": [0], "ov_idx": list(range(1, n))}


def _round(assets_in, *, ret, stage1_ret=None, vol=0.16, weights=None, target,
           internal=None, internal_tickers=("UA", "UB"), jp=None, gc=None, sleeve_mu=None):
    n = len(assets_in)
    w = np.array(weights) if weights is not None else np.array([DOM_W] + [0.0] * (n - 1))
    ret1 = float(ret if stage1_ret is None else stage1_ret)
    out = {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": w,
        "stage1_ret": ret1, "stage1_vol": vol, "stage1_sharpe": ret1 / max(vol, 1e-9),
        "internal": internal,
        "internal_tickers": (list(internal_tickers) if internal is not None else None),
        "japan_selection": jp, "gc_selection": gc,
        "final": {"ret": float(ret), "vol": vol, "sharpe": float(ret) / max(vol, 1e-9),
                  "floor": float(ret) - 1.96 * vol},
        "target": float(target),
        "warnings": [],
    }
    out.update(_model_fields(assets_in, sleeve_mu))
    return out


def _codes(res):
    return [w.get("code") for w in (res.get("warning_items") or [])]


def test_sleeve_without_basket_is_re_solved_with_zero_weight():
    """① 阶段一给了袖权重、阶段二给不出篮子 → 必须按 0 权重剔除后重解（不拿模型统计当交付）。"""
    calls = []

    def stub(assets_in, corr_pairs, **kw):
        ex = tuple(kw.get("sleeve_exclude") or ())
        calls.append({"exclude": ex, "target": float(kw.get("target", 0.0))})
        if "O_US_CORE" in ex:
            return _round(assets_in, ret=TARGET + 0.004, vol=0.16, target=kw["target"],
                          weights=[DOM_W, 0.0, 0.0, 0.0, OV_W],
                          jp={"ret": 0.05, "vol": 0.12})
        # 该袖有权重、阶段二给不出篮子 → final 只是阶段一的**模型**统计
        return _round(assets_in, ret=TARGET + 0.004, vol=0.16, target=kw["target"],
                      weights=[DOM_W, OV_W, 0.0, 0.0, 0.0],
                      jp={"ret": 0.05, "vol": 0.12})

    res = _run(stub, max_sleeve_iter=2)
    assert res is not None
    w = {k: float(v) for k, v in zip(res["keys"], res["stage1_weights"])}
    assert w["O_US_CORE"] == 0.0, w                        # 剔除后不再有权重（行为断言）
    assert res.get("deliverable") is True, res.get("deliverable")
    assert res.get("final_source") == "delivered", res.get("final_source")
    assert any(c["exclude"] == ("O_US_CORE",) for c in calls), calls
    assert "sleeve.excluded_no_basket" in _codes(res), _codes(res)
    assert float(res["final"]["ret"]) >= TARGET - 1e-4, res["final"]


def test_undeliverable_is_explicit_when_exclusion_also_fails():
    """① 剔除后仍解不出 → 必须显式 deliverable=False + final_source=stage1_model + code。"""
    def stub(assets_in, corr_pairs, **kw):
        return _round(assets_in, ret=TARGET + 0.004, vol=0.16, target=kw["target"],
                      weights=[DOM_W, OV_W, 0.0, 0.0, 0.0],
                      jp={"ret": 0.05, "vol": 0.12})

    res = _run(stub, max_sleeve_iter=2)
    assert res is not None
    assert res.get("deliverable") is False, res.get("deliverable")
    assert res.get("final_source") == "stage1_model", res.get("final_source")
    assert "sleeve.undeliverable" in _codes(res), _codes(res)


def test_cycle_amplitude_and_deterministic_selection():
    """③ 极限环：给出可诊断振幅；同输入同输出（确定性），且不承诺收敛。"""
    def make_stub(log):
        def stub(assets_in, corr_pairs, **kw):
            log.append(1)
            if len(log) % 2 == 1:      # 交替交付两套袖 → 极限环
                internal = (np.array([1.0, 0.0]), 0.10, 0.12, 0.83, None, ["UA", "UB"])
            else:
                internal = (np.array([0.0, 1.0]), 0.20, 0.18, 0.94, None, ["UA", "UB"])
            return _round(assets_in, ret=TARGET + 0.004, vol=0.16, target=kw["target"],
                          weights=[DOM_W, OV_W, 0.0, 0.0, 0.0], internal=internal)
        return stub

    log1, log2 = [], []
    res1 = _run(make_stub(log1), max_sleeve_iter=4)
    res2 = _run(make_stub(log2), max_sleeve_iter=4)
    assert res1 is not None and res2 is not None
    cyc = res1.get("sleeve_cycle")
    assert isinstance(cyc, dict), cyc
    assert cyc["max_amplitude"] > 0.05, cyc                 # 可诊断振幅
    assert cyc["amplitude_mu"], cyc
    assert res1["sleeve_converged"] is False, res1["warnings"]
    assert "不承诺无条件收敛" in cyc["note"], cyc
    # 同输入同输出
    assert ([h["sleeve"] for h in res1["sleeve_history"]]
            == [h["sleeve"] for h in res2["sleeve_history"]]), (res1["sleeve_history"],
                                                                res2["sleeve_history"])
    assert res1["final"] == res2["final"], (res1["final"], res2["final"])



# 下限生效专用池：切点低（约一格）、上限高 → 目标落在"切点前沿"与"上限前沿"之间
EPS_US = {"UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.04},
          "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.40}}
EPS_JP = {"JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.03},
          "JD": {"name": "虚构日股D", "mu": 0.30, "vol": 0.80}}
EPS_GC = {"GD": {"name": "虚构大中华D", "mu": 0.04, "vol": 0.03},
          "GE": {"name": "虚构大中华E", "mu": 0.30, "vol": 0.80}}


def test_single_eps_only_at_delivery_assertion():
    """② 袖内下限 = 需求本身（不再 −ε）；唯一语义 ε 在交付断言处（交付收益 ≥ 目标 − ε）。"""
    with patched_pools(us=EPS_US, jp=EPS_JP, gc=EPS_GC):
        solved = {"keys": [a["key"] for a in FAKE_ASSETS]}
        solved.update(_model_fields(FAKE_ASSETS))
        assumed = {"O_US_CORE": (0.02, 0.10), "O_JP_EQ": (0.02, 0.10),
                   "D_GREATER_CN": (0.02, 0.10)}
        floors, audit = OP._compute_sleeve_floors(solved, assumed, 0.20, RF, None, 0.5,
                                                  DOM_W, OV_W, {})
    assert floors, audit
    for k, f in floors.items():
        req = float(audit[k]["required_mu"])
        # 审计里的 required_mu 只保留 6 位小数 → 容差按舍入给；旧的 floor−ε（差 1e-4）仍会被判失败
    assert abs(float(f) - req) <= 2e-5, (k, f, req)      # 下限就是需求，不再 −ε
    # 交付侧的保证：交付收益 ≥ 目标 − ε（唯一语义 ε）
    with patched_pools(us=EPS_US, jp=EPS_JP, gc=EPS_GC):
        res = OP.solve_portfolio([dict(a) for a in FAKE_ASSETS], {}, dom_w=DOM_W, ov_w=OV_W,
                                 target=0.20, rf=RF, extract=EXTRACT, total_assets=TOTAL,
                                 warnings=[], log=None, max_sleeve_iter=4,
                                 stage1_starts=30, stage2_starts=15)
    assert res is not None
    assert float(res["final"]["ret"]) >= 0.20 - OP.SLEEVE_FLOOR_EPS, res["final"]


def test_best_round_is_selected_by_delivery_target():
    """③ 择优以**交付目标**优先：不得让"模型-交付差最小但没达标"的一轮当选。"""
    calls = []

    def stub(assets_in, corr_pairs, **kw):
        calls.append(1)
        if len(calls) == 1:
            # 第 1 轮：交付达标（0.184），但阶段一模型与交付差很大
            return _round(assets_in, ret=TARGET + 0.004, stage1_ret=0.10, vol=0.16,
                          target=kw["target"], weights=[DOM_W, OV_W, 0.0, 0.0, 0.0],
                          internal=(np.array([0.0, 1.0]), 0.30, 0.18, 1.56, None, ["UA", "UB"]),
                          jp={"ret": 0.05, "vol": 0.12})
        # 第 2 轮：模型-交付差为 0，但交付**没达标**
        return _round(assets_in, ret=0.15, stage1_ret=0.15, vol=0.16,
                      target=kw["target"], weights=[DOM_W, 0.0, OV_W, 0.0, 0.0],
                      internal=(np.array([1.0, 0.0]), 0.06, 0.10, 0.40, None, ["UA", "UB"]),
                      jp={"ret": 0.20, "vol": 0.30})

    res = _run(stub, max_sleeve_iter=2)
    assert res is not None
    assert len(calls) >= 2, calls
    assert float(res["final"]["ret"]) >= TARGET - 1e-4, res["final"]


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  OK " + name)
        except AssertionError as exc:
            failed += 1
            print("  FAIL " + name + ": " + str(exc)[:300])
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc)[:300])
    print("\n%d/%d 通过" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

