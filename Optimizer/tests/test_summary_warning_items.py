#!/usr/bin/env python3
"""summary 的 warning_items 回归测试（不联网、全虚构输入）。

背景（task-38）：App 读的是 build_result_summary() 产出的 summary；它此前**没有** warning_items 键
→ 生产路径上 warningItems 恒为 nil，Swift 只能退回文案匹配（"改文案即失配"实际没被解决）。
本文件锁定三条契约：
  1. summary 的 warning_items 与 warnings **条数一致、逐条 message 一致**；
  2. summary 的全部告警（含自检类与 CAP_HITS 类）都能落到稳定 code；
  3. 加法式：既有键与既有 warnings 内容一个字节都不动（新键之外无差异）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_summary_warning_items.py
祖先对照见 _SCRIPTS 注释（必须用 PM_OPT_SCRIPTS；只改 PYTHONPATH 会跑成当前树）。
"""

import os
import sys

# 祖先对照的正确做法（2026-10-17 收口）：
#   PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts PYTHONPATH=<祖先树>/Optimizer/scripts \
#     python3 <本文件>        # 并把本文件拷进祖先树（或直接用祖先树里的副本）
# ⚠️ 只改 PYTHONPATH 是**假的祖先对照**：下面这行优先用 PM_OPT_SCRIPTS，
#    会把「祖先全绿」当成结论（verify 首轮就踩过这个坑）。
_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402


def _output(warnings=None, weights=None, stage1_extra=None):
    return {
        "assets": [],
        "stage1": {"weights": weights if weights is not None else {"D1": 1.0},
                   **(stage1_extra or {})},
        "final": {"ret": 0.05, "vol": 0.10, "sharpe": 0.30, "floor": -0.146},
        "warnings": list(warnings or []),
        "availability": {"checked_at": "2026-01-01T00:00:00"},
        "target_return": 0.18,
        "target_gap": 0.0,
        "objective": "max_sharpe",
    }


def _items(summary):
    return summary.get("warning_items")


def test_summary_warning_items_match_warnings():
    """条数与逐条 message 必须与 warnings 完全一致，且 code 全部落在 WARNING_CODES。"""
    out = _output(warnings=[OP._Warn("目标 18.00% 不可行：已按最大可行收益 5.00% 交付。",
                                     "target.infeasible")])
    summary = OP.build_result_summary(out)
    items = _items(summary)
    assert isinstance(items, list) and items, items
    assert len(items) == len(summary["warnings"]), (items, summary["warnings"])
    assert [it["message"] for it in items] == list(summary["warnings"]), items
    for it in items:
        assert set(it) == {"code", "message"}, it
        assert it["code"] in OP.WARNING_CODES, it
    assert any(it["code"] == "target.infeasible" for it in items), items


def test_summary_self_check_codes():
    """自检类告警（映射漏项 / 权重合计≠1）必须带专属 code，而不是 other。"""
    out = _output(weights={"D1": 1.0})
    summary = OP.build_result_summary(out)
    codes = [it["code"] for it in _items(summary)]
    assert "summary.weight_sum_mismatch" in codes, codes
    assert "summary.missing_weights" in codes, codes
    assert "other" not in codes, codes


def test_optimizer_codes_survive_into_summary():
    """优化器自己的告警（带 code）必须原样传进 summary，不丢码。"""
    out = _output(warnings=[
        OP._Warn("袖 O_US_CORE 在阶段二给不出篮子：已按 0 权重把它从组合里剔除后重解。",
                 "sleeve.excluded_no_basket"),
        OP._Warn("本次结果不可交付：…", "sleeve.undeliverable"),
        "没有 code 的普通字符串（应落到 other）",
    ])
    summary = OP.build_result_summary(out)
    codes = [it["code"] for it in _items(summary)]
    assert "sleeve.excluded_no_basket" in codes, codes
    assert "sleeve.undeliverable" in codes, codes
    assert codes.count("other") == 1, codes
    assert len(codes) == len(summary["warnings"]), (codes, summary["warnings"])


def test_cap_hit_warning_has_stable_code():
    """CAP_HITS 合并提示必须带 active_cap.hit，且 message 仍与旧文案同形。"""
    w = OP._cap_hit_warning([("T_X", 0.10, 0.10), ("T_X", 0.08, 0.10)])
    assert getattr(w, "code", None) == "active_cap.hit", w
    assert "上限" in str(w) and "T_X" in str(w), str(w)
    assert OP._warning_items([w]) == [{"code": "active_cap.hit", "message": str(w)}]


def test_summary_is_additive_only():
    """加法式契约：除新键 warning_items 外，summary 的既有键与既有 warnings 内容不变。"""
    warn = OP._Warn("阶段一按当前模型判定目标不可行。", "target.stage1_fallback")
    out = _output(warnings=[warn], weights={"D1": 1.0})
    summary = OP.build_result_summary(out)
    legacy = {"generated_at", "portfolio", "assets", "benchmark", "test_assets",
              "target_return", "target_gap", "objective", "warnings", "source_detail"}
    assert set(summary) == legacy | {"warning_items"}, sorted(summary)
    # warnings 内容 = 输入告警 + 自检两条（顺序不变）
    assert str(summary["warnings"][0]) == str(warn), summary["warnings"]
    assert any("自检" in str(x) for x in summary["warnings"]), summary["warnings"]


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
