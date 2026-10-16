#!/usr/bin/env python3
"""交付告警只反映**选中轮**（task-42 回归；不联网、全虚构 fixture）。

背景：中间回退轮的结论性告警（如「美国权益内部优化不可行，组合统计已回退使用第一阶段结果」，
code sleeve.us_unavailable）曾被并入最终交付 warnings，而后几轮已正常交付
→ 交付结果自相矛盾（deliverable=true / final_source='delivered' 却写着"已回退"）。
契约：
  ① 交付 warnings / warning_items 只反映**选中轮**（+ 调用方 + 收尾汇总）；
  ② 中间轮的告警移入加法式审计字段 history[i].warnings / warning_items（可追溯）；
  ③ 反向自洽：选中轮本身就是回退轮时，该结论**必须**出现。

祖先对照见 _SCRIPTS 注释（必须用 PM_OPT_SCRIPTS；只改 PYTHONPATH 会跑成当前树）。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_warning_round.py
"""

import os
import sys

import numpy as np

# 祖先对照的正确做法（2026-10-17 收口）：
#   PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts PYTHONPATH=<祖先树>/Optimizer/scripts \
#     python3 <本文件>        # 并把本文件拷进祖先树（或直接用祖先树里的副本）
# ⚠️ 只改 PYTHONPATH 是**假的祖先对照**：下面这行优先用 PM_OPT_SCRIPTS。
_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402

DOM_W, OV_W, TOTAL, RF = 0.30, 0.70, 1_000_000.0, 0.02
TARGET = 0.18

US = {"UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.10},
      "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.18}}
JP = {"JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.12}}
CORR = {("UA", "UB"): 0.3}
ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_US_CORE", "name": "虚构美股核心袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_JP_EQ", "name": "虚构日本权益袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_HIGH", "name": "虚构高收益腿", "mu": 0.20, "vol": 0.40,
     "pool": "overseas", "test_class": "broad"},
]
EXTRACT = {"pool_mode": "fixture", "domestic_weight": DOM_W, "overseas_weight": OV_W,
           "domestic_value": TOTAL * DOM_W, "overseas_value": TOTAL * OV_W, "total_value": TOTAL,
           "rf": RF, "us_equity": {"total_value": TOTAL * OV_W,
                                   "holdings": [{"ticker": "UA", "name": "虚构",
                                                 "value_cny": TOTAL * OV_W, "weight": 1.0}]}}


class patched_pools:
    def __enter__(self):
        self.saved = (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        OP.US_INTERNAL, OP.US_INTERNAL_CORR = US, CORR
        OP.JP_INTERNAL, OP.JP_INTERNAL_CORR = JP, CORR
        OP._SLEEVE_POOL_SPECS = {"O_US_CORE": (US, CORR, 19), "O_JP_EQ": (JP, CORR, 31)}
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         saved_specs) = self.saved
        if saved_specs is None:
            del OP._SLEEVE_POOL_SPECS
        else:
            OP._SLEEVE_POOL_SPECS = saved_specs
        return False


def _model_fields(assets_in):
    n = len(assets_in)
    return {"mu": np.array([float(a["mu"]) for a in assets_in]),
            "lower": np.zeros(n), "upper": np.ones(n),
            "dom_idx": [0], "ov_idx": list(range(1, n))}


def _round(assets_in, *, ret, target, weights, internal=None, jp=None, warns=()):
    n = len(assets_in)
    out = {
        "keys": [a["key"] for a in assets_in],
        "stage1_weights": np.array(weights),
        "stage1_ret": float(ret), "stage1_vol": 0.16, "stage1_sharpe": float(ret) / 0.16,
        "internal": internal,
        "internal_tickers": (["UA", "UB"] if internal is not None else None),
        "japan_selection": jp, "gc_selection": None,
        "final": {"ret": float(ret), "vol": 0.16, "sharpe": float(ret) / 0.16,
                  "floor": float(ret) - 1.96 * 0.16},
        "target": float(target),
        "warnings": list(warns),
    }
    out.update(_model_fields(assets_in))
    return out


def _run(stub, max_sleeve_iter=3):
    with patched_pools():
        original = OP._solve_once
        OP._solve_once = stub
        try:
            return OP.solve_portfolio([dict(a) for a in ASSETS], {}, dom_w=DOM_W, ov_w=OV_W,
                                      target=TARGET, rf=RF, extract=EXTRACT, total_assets=TOTAL,
                                      warnings=[], log=None, max_sleeve_iter=max_sleeve_iter,
                                      stage1_starts=40, stage2_starts=20)
        finally:
            OP._solve_once = original


def _codes(res):
    return [w.get("code") for w in (res.get("warning_items") or [])]


STALE = [
    OP._Warn("Stage 1 gave zero weight to US core equity.", "sleeve.stage2_error"),
    OP._Warn("美国权益内部优化不可行，组合统计已回退使用第一阶段结果。", "sleeve.us_unavailable"),
]


def _emit(kw, warns):
    """受控桩也要像生产 _solve_once 那样把告警写进调用方传入的 warn 列表。"""
    wl = kw.get("warn")
    if wl is not None:
        wl.extend(warns)


def _stub_mixed(calls):
    """第 0 轮：回退轮（带"已回退"结论性告警）；第 1 轮起：正常交付（无告警）。"""
    def stub(assets_in, corr_pairs, **kw):
        calls.append(1)
        if len(calls) == 1:
            _emit(kw, STALE)
            return _round(assets_in, ret=0.1494, target=kw["target"] * 0.83,
                          weights=[DOM_W, 0.0, 0.0, OV_W], warns=STALE)
        return _round(assets_in, ret=TARGET + 0.004, target=kw["target"],
                      weights=[DOM_W, OV_W, 0.0, 0.0],
                      internal=(np.array([0.0, 1.0]), 0.30, 0.18, 1.56, None, ["UA", "UB"]))
    return stub


def test_delivered_warnings_exclude_intermediate_round_conclusions():
    """中间回退轮的结论性告警不得出现在交付结果里；审计字段仍保留。"""
    res = _run(_stub_mixed([]))
    assert res is not None
    assert res.get("final_source") == "delivered", res.get("final_source")
    codes = _codes(res)
    assert "sleeve.us_unavailable" not in codes, codes      # 行为断言：不得残留中间轮结论
    assert "sleeve.stage2_error" not in codes, codes
    # 审计：第 0 轮（被丢弃的回退轮）的告警原样保留
    hist = res["sleeve_history"]
    first = hist[0]
    assert first.get("fallback") is True, first
    audit_codes = [w["code"] for w in (first.get("warning_items") or [])]
    assert "sleeve.us_unavailable" in audit_codes, first
    assert "sleeve.stage2_error" in audit_codes, first
    # 交付是达标轮
    assert float(res["final"]["ret"]) >= TARGET - 1e-4, res["final"]


def test_true_fallback_keeps_the_conclusion():
    """反向自洽：选中轮本身就是回退轮时，"已回退到第一阶段统计"必须出现。"""
    def stub(assets_in, corr_pairs, **kw):
        _emit(kw, STALE)
        return _round(assets_in, ret=0.1494, target=kw["target"] * 0.83,
                      weights=[DOM_W, 0.0, 0.0, OV_W], warns=STALE)

    res = _run(stub, max_sleeve_iter=2)
    assert res is not None
    codes = _codes(res)
    assert "sleeve.us_unavailable" in codes, codes
    assert float(res["final"]["ret"]) < TARGET - 1e-4, res["final"]


def test_round_audit_is_additive_and_consistent():
    """审计字段自洽：history[i].warnings 与 warning_items 条数一致、逐条 message 相同。"""
    res = _run(_stub_mixed([]))
    with_audit = [h for h in res["sleeve_history"] if h.get("warnings")]
    assert with_audit, res["sleeve_history"]
    for h in with_audit:
        assert len(h["warnings"]) == len(h["warning_items"]), h
        assert [w["message"] for w in h["warning_items"]] == list(h["warnings"]), h
        assert all(w["code"] in OP.WARNING_CODES for w in h["warning_items"]), h
    # 交付 warnings 与 warning_items 仍然一一对应
    assert len(res["warnings"]) == len(res["warning_items"]), res["warnings"]


def test_low_target_has_no_intermediate_conclusions():
    """低目标档：正常交付，不应出现回退/不可交付/预算用尽这类结论性 code。"""
    def stub(assets_in, corr_pairs, **kw):
        return _round(assets_in, ret=0.10, target=kw["target"], weights=[DOM_W, OV_W, 0.0, 0.0],
                      internal=(np.array([1.0, 0.0]), 0.06, 0.10, 0.40, None, ["UA", "UB"]))

    res = _run(stub, max_sleeve_iter=4)
    codes = _codes(res)
    for bad in ("sleeve.us_unavailable", "sleeve.undeliverable", "target.infeasible",
                "target.budget_exhausted"):
        assert bad not in codes, (bad, codes)
    assert not any(h.get("warnings") for h in res["sleeve_history"]), res["sleeve_history"]


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
