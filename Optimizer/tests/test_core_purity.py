#!/usr/bin/env python3
"""纯度静态守卫（task-52 / ADR 0003 §3 R1、R2、R3）。

守卫本身**可证伪**：喂一段含 import urllib 的合成源码必须抛错（见 test_guard_is_falsifiable）。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_core_purity.py
祖先对照：PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts（本文件不硬写 ../scripts）
"""

import ast
import os
import sys
import tempfile

# 祖先对照的正确做法（AGENTS §12）：PM_OPT_SCRIPTS 优先；只改 PYTHONPATH 会跑成当前树。
_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
_PKG = os.path.join(_SCRIPTS, "portfolio_system")

#: R1：core/models 不得 import 的模块（网络 / 磁盘 / 子进程 / 数据库）
FORBIDDEN_MODULES = ("urllib", "requests", "socket", "ssl", "subprocess", "sqlite3",
                     "http", "ftplib", "smtplib", "telnetlib", "asyncio",
                     "shutil", "tempfile", "pathlib", "pickle")
#: 禁止调用的 os.*（子进程/文件系统）
FORBIDDEN_CALLS = (("os", "system"), ("os", "popen"), ("os", "remove"), ("os", "unlink"),
                   ("os", "makedirs"), ("os", "mkdir"), ("os", "rename"))
#: 调用器之间不得互相 import（R3）
CALLER_NAMES = ("preset_runner", "sensitivity_runner", "new_asset_runner")


class PurityViolation(AssertionError):
    pass


def _root(module_name):
    return str(module_name or "").split(".")[0]


def check_source(source, filename="<synthetic>"):
    """静态检查一段源码；违规时抛 PurityViolation（可复用、可证伪）。"""
    tree = ast.parse(source, filename=filename)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _root(alias.name) in FORBIDDEN_MODULES or _root(alias.name) == "services":
                    problems.append("%s: import %s" % (filename, alias.name))
        elif isinstance(node, ast.ImportFrom):
            mod = _root(node.module)
            if mod in FORBIDDEN_MODULES or mod == "services":
                problems.append("%s: from %s import ..." % (filename, node.module or ""))
            if mod in ("", "callers") and any(a.name in CALLER_NAMES for a in node.names):
                problems.append("%s: 调用器互相 import（R3）" % filename)
        elif isinstance(node, ast.Global):
            problems.append("%s:%d: global 语句（R2 禁模块级可变状态）" % (filename, node.lineno))
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "open":
                problems.append("%s:%d: 调用 open(（core/models 禁磁盘）" % (filename, node.lineno))
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                if (fn.value.id, fn.attr) in FORBIDDEN_CALLS:
                    problems.append("%s:%d: 调用 %s.%s" % (filename, node.lineno, fn.value.id, fn.attr))
    if problems:
        raise PurityViolation("; ".join(problems))
    return True


def assert_core_pure(path):
    """对 core/** 与 models/**（或任意目录）做纯度断言；返回被检查的文件数。"""
    checked = 0
    if os.path.isfile(path):
        files = [path]
    else:
        files = []
        for dirpath, _dirs, names in os.walk(path):
            for name in sorted(names):
                if name.endswith(".py"):
                    files.append(os.path.join(dirpath, name))
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            check_source(fh.read(), filename=os.path.relpath(f, _SCRIPTS))
        checked += 1
    return checked


def test_guard_is_falsifiable():
    """守卫自身可证伪：合成违规源码必须各自抛错。"""
    cases = {
        "import urllib.request": "import urllib.request",
        "import subprocess": "import subprocess",
        "import sqlite3": "import sqlite3",
        "from services import pipeline": "from ..services import pipeline",
        "global 语句": "def f():\n    global X\n    X = 1",
        "open( 调用": "def f(p):\n    return open(p).read()",
        "os.system": "import os\ndef f():\n    os.system('ls')",
    }
    for label, src in cases.items():
        try:
            check_source(src, filename=label)
        except PurityViolation:
            continue
        raise AssertionError("守卫漏检: %s" % label)
    # 干净源码必须放行
    check_source("import numpy as np\n\ndef f(x):\n    return np.sqrt(x)", filename="clean")


def test_core_and_models_are_pure():
    """真包扫描：core/** 与 models/** 必须零违规。"""
    assert os.path.isdir(_PKG), "找不到包目录: %s" % _PKG
    n_core = assert_core_pure(os.path.join(_PKG, "core"))
    n_models = assert_core_pure(os.path.join(_PKG, "models"))
    assert n_core >= 5 and n_models >= 2, (n_core, n_models)


def test_callers_do_not_import_each_other():
    """R3：三个调用器互不 import。"""
    callers = os.path.join(_PKG, "callers")
    if not os.path.isdir(callers):
        raise AssertionError("找不到 callers/")
    for name in ("preset_runner.py", "sensitivity_runner.py", "new_asset_runner.py"):
        f = os.path.join(callers, name)
        with open(f, "r", encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in ("", "callers"):
                for alias in node.names:
                    assert alias.name not in CALLER_NAMES, "%s 互相 import: %s" % (name, alias.name)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[-1] not in CALLER_NAMES, \
                        "%s 互相 import: %s" % (name, alias.name)


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
