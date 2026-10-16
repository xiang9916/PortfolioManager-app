#!/usr/bin/env python3
"""告警 code 的「登记 ↔ 发出」一致性（task-47；不联网、全虚构）。

背景：test.ticker_skipped / fund.available_none 在 WARNING_CODES 里登记了，
但发出时用的是普通字符串 → warning_items 落到 other，结果页的「因数据不可用被跳过」态在生产里
恒不出现（改文案即失配）。本文件锁定：
  ① 跳过场景必须带 code test.ticker_skipped，且消息文本与旧文案逐字一致（只补 code）；
  ② 登记表里每个 code 都必须真的被发出去（反向 = 无死 code）；
  ③ 发出的 code 必须都在登记表里（正向 = 无未登记 code）。

祖先对照见 _SCRIPTS 注释（必须用 PM_OPT_SCRIPTS；只改 PYTHONPATH 会跑成当前树）。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_warning_codes.py
"""

import os
import re
import sys

# 祖先对照的正确做法（2026-10-17 收口）：
#   PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts PYTHONPATH=<祖先树>/Optimizer/scripts \
#     python3 <本文件>
# ⚠️ 只改 PYTHONPATH 是**假的祖先对照**：下面这行优先用 PM_OPT_SCRIPTS。
_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402

_SRC_PATH = os.path.join(_SCRIPTS, "optimize_portfolio.py")


def _source():
    return open(_SRC_PATH, encoding="utf-8").read()


def _table_and_rest():
    src = _source()
    start = src.index("WARNING_CODES = {")
    end = src.index("\n}", start) + 2
    return src[start:end], src[:start] + src[end:]


def test_skipped_ticker_warning_has_code():
    """跳过场景：带 code，且消息与旧文案（普通 f-string）逐字一致。"""
    w = OP._test_ticker_skip_warning("ZZZ.FAKE", "行情不可用")
    assert getattr(w, "code", None) == "test.ticker_skipped", w
    assert str(w) == "测试标的 ZZZ.FAKE 已跳过: 行情不可用", str(w)
    assert OP._warning_items([w]) == [{"code": "test.ticker_skipped",
                                       "message": "测试标的 ZZZ.FAKE 已跳过: 行情不可用"}]


def test_skip_scenario_flows_into_warning_items():
    """行为链：跳过告警 → summary 的 warning_items 必须带 test.ticker_skipped（不再是 other）。"""
    out = {"assets": [], "stage1": {"weights": {"D1": 1.0}},
           "final": {"ret": 0.05, "vol": 0.10, "sharpe": 0.30, "floor": -0.146},
           "warnings": [OP._test_ticker_skip_warning("ZZZ.FAKE", "行情不可用")],
           "availability": {}, "target_return": 0.06, "target_gap": 0.0,
           "objective": "max_sharpe"}
    summ = OP.build_result_summary(out)
    codes = [it["code"] for it in (summ.get("warning_items") or [])]
    assert "test.ticker_skipped" in codes, codes
    assert "other" not in codes, codes


def test_every_registered_code_is_emitted():
    """登记表里每个 code 都必须真的被发出去（否则就是死 code / 生产恒不出现的态）。"""
    table_src, rest = _table_and_rest()
    registered = [c for c in OP.WARNING_CODES if c != "other"]
    dead = [c for c in registered
            if not re.search(r"['\"]" + re.escape(c) + r"['\"]", rest)]
    assert not dead, "已登记但从未发出: %s" % dead


def test_every_emitted_code_is_registered():
    """发出的 code 必须都在登记表里（防拼错/漏登）。"""
    _table_src, rest = _table_and_rest()
    emitted = set(re.findall(r"['\"]([a-z][a-z0-9_]*\.[a-z0-9_.]+)['\"]", rest))
    unknown = sorted(c for c in emitted if c not in OP.WARNING_CODES)
    assert not unknown, "发出了但未登记: %s" % unknown


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
