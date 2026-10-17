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

import ast
import os
import sys

# 祖先对照的正确做法（2026-10-17 收口）：
#   PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts PYTHONPATH=<祖先树>/Optimizer/scripts \
#     python3 <本文件>
# ⚠️ 只改 PYTHONPATH 是**假的祖先对照**：下面这行优先用 PM_OPT_SCRIPTS。
_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)
# P4：包根 = scripts 的兄弟目录（由 PM_OPT_SCRIPTS 派生 → 祖先/旧布局树同样可用）。
_PKG_ROOT = os.path.dirname(os.path.abspath(_SCRIPTS))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import optimize_portfolio as OP  # noqa: E402

_SRC_PATH = os.path.join(_SCRIPTS, "optimize_portfolio.py")
#: D2/P3-b1 起部分 code 的发出点上移到 core（active_cap.pool_conflict → core/constraints；
#: sleeve.floor_infeasible / sleeve.floor_postcheck_failed → core/stage2）。
#: 「登记 ↔ 发出」是**跨模块**的性质，扫描范围必须覆盖实现所在模块，否则会把
#: 已经发出的 code 误判成死 code（本文件在 D2 后与 P3-b1 后各红过一次，原因都是这个）。
#: 因此不再硬编码单个文件，而是扫 optimize_portfolio.py + 包内**全部** .py
#:（P3-b2 再搬函数时不必回来改这里）。
def _impl_paths():
    paths = [_SRC_PATH]
    pkg = (os.path.join(os.path.dirname(os.path.abspath(_SCRIPTS)), "portfolio_system")
    if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(_SCRIPTS)), "portfolio_system")) else os.path.join(_SCRIPTS, "portfolio_system"))
    for dirpath, _dirs, names in os.walk(pkg):
        for name in sorted(names):
            if name.endswith(".py"):
                paths.append(os.path.join(dirpath, name))
    return tuple(paths)


_IMPL_PATHS = _impl_paths()


def _source():
    """登记表所在文件（optimize_portfolio.py）的源码。"""
    return open(_SRC_PATH, encoding="utf-8").read()


def _literal_codes(node):
    """从一个实参表达式里取出它可能承载的 code 字面量。

    支持两种真实写法：直接字面量（`"active_cap.hit"`）与条件表达式
    （`"target.budget_exhausted" if budget else "target.infeasible"`）。
    只取字符串常量，不递归进任意 f-string → 不会把消息文本误当 code。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _literal_codes(node.body) + _literal_codes(node.orelse)
    return []


def _emitted_codes():
    """AST 精确提取「真的被当参数传给告警构造器」的 code。

    不用正则扫文本：登记表里的键、注释、文档字符串都会假命中 —— 本文件在 D2 后
    先踩过一次「把登记表当发出点」的假绿（删掉 core 的发出块仍全绿）。
    告警构造器在实现里的名字：_Warn（optimize_portfolio，D2 起 = core CodedWarning 别名）
    与 CodedWarning（core/constraints 直接用）。
    """
    codes = set()
    for path in _IMPL_PATHS:
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name in ("_Warn", "CodedWarning"):
                for arg in node.args[1:]:
                    codes.update(_literal_codes(arg))
    return codes


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
    """登记表里每个 code 都必须真的被发出去（否则就是死 code / 生产恒不出现的态）。

    发出点扫描范围 = optimize_portfolio.py + 发出点上移后的 core 模块（D2）；
    用 AST 取**构造器实参**，不把登记表键/注释当发出点（否则是假绿）。
    """
    emitted = _emitted_codes()
    dead = sorted(c for c in OP.WARNING_CODES if c != "other" and c not in emitted)
    assert not dead, "已登记但从未发出: %s" % dead


def test_every_emitted_code_is_registered():
    """发出的 code 必须都在登记表里（防拼错/漏登）。"""
    unknown = sorted(c for c in _emitted_codes() if c not in OP.WARNING_CODES)
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
