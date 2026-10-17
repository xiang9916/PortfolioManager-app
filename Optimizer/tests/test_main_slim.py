#!/usr/bin/env python3
"""main() 瘦身守卫（task-65 ② 后半，ADR 0003 §5）。

契约：CLI 入口只保留**装配层职责**（argparse → RunRequest → PresetRunner.run → 写 JSON）；
取数/装配/求解/结果汇总全部在 callers。本文件是机械闸，把编排搬回入口即红：
  ① main() 不得直接调用编排/联网/求解/载荷装配/落盘（json.dump、open）；
  ② main() 必须保留 _parse_args / RunRequest / PresetRunner / _write_outputs 四个环节；
  ③ 结果汇总与选基规则的唯一来源 = callers/preset_runner.py；
  ④ callers/** 不得 import 上层（含函数体内延迟 import）。

祖先对照：PM_OPT_SCRIPTS=<祖先>/Optimizer/scripts python3 本文件 —— 旧 main() 上 ①②③ 必红。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_main_slim.py
"""
import ast
import os
import sys

_SCRIPTS = os.path.abspath(os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
_PKG_ROOT = os.path.dirname(_SCRIPTS)
_PKG = os.path.join(_PKG_ROOT, "portfolio_system")
if not os.path.isdir(_PKG):
    _PKG = os.path.join(_SCRIPTS, "portfolio_system")   # 旧布局（包在 scripts/ 下）
_ENTRY = os.path.join(_SCRIPTS, "optimize_portfolio.py")

#: main() 里禁止直接出现的编排/求解/汇总/落盘调用（必须在 callers 或 _write_outputs 内）。
DENY = {
    "ensure_work_dir", "ensure_pipeline", "build_investable_assets",
    "build_open_pool_from_status", "solve_portfolio", "select_domestic_funds",
    "build_output_payload", "dump", "open",
}
#: main() 必须保留的四个环节（缺一即说明编排又回到了入口）。
REQUIRED = {"_parse_args", "RunRequest", "PresetRunner", "_write_outputs"}


def _tree(path):
    return ast.parse(open(path, encoding="utf-8").read(), filename=path)


def _main_func():
    for node in _tree(_ENTRY).body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("找不到 main()")


def _calls(node):
    """main() 内所有调用点（含 lambda / 推导式 / 嵌套 def）。"""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def test_main_has_no_orchestration_calls():
    bad = sorted(_calls(_main_func()) & DENY)
    assert not bad, "main() 又直接做了编排/求解/汇总/落盘：%s（应搬进 callers）" % bad


def test_main_keeps_the_four_stages():
    missing = sorted(REQUIRED - _calls(_main_func()))
    assert not missing, "main() 缺少环节：%s" % missing


def test_single_assembly_point_in_callers():
    """结果汇总 / 选基规则必须只存在于 callers（入口只 import，不自带实现）。"""
    defs = {n.name for n in _tree(os.path.join(_PKG, "callers", "preset_runner.py")).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for name in ("build_output_payload", "select_domestic_funds", "PresetDeps", "PresetRunner"):
        assert name in defs, "callers/preset_runner.py 缺 %s" % name
    entry_defs = {n.name for n in _tree(_ENTRY).body if isinstance(n, ast.FunctionDef)}
    assert "select_domestic_funds" not in entry_defs, \
        "选基规则又回到入口脚本了（唯一来源应为 callers）"


def test_callers_do_not_import_upper_layers():
    forbidden = {"params", "asset_classes", "market_data", "fund_pipeline", "test_tickers",
                 "optimize_portfolio", "sensitivity_analysis", "optimize_common"}
    bad = []
    callers = os.path.join(_PKG, "callers")
    for name in sorted(os.listdir(callers)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(callers, name)
        for node in ast.walk(_tree(path)):   # 含函数体内延迟 import
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                if m.split(".")[0] in forbidden:
                    bad.append("%s: %s" % (name, m))
    assert not bad, "callers 不得 import 上层模块：%s" % bad


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
