#!/usr/bin/env python3
"""services 真实接线守卫（P3-b3 步骤①）。

背景：P0 的 services 是「方法存在就调」的宽松壳（猜了 ensure_availability/run 之类并不存在的名字），
等于没接线。P3-b3 起 services 按真实函数名转发，且**脚本层不再直接 import** fund_pipeline / market_data。

本文件锁定四件事（都不联网、只用临时目录）：
  ① 两个 CLI 脚本不直接 import 数据/流水线模块（必须经 services）；
  ② services.pipeline 的转发是**真转发**（ensure_work_dir 真的建目录）；
  ③ services.portfolio_source.load_extract 真的读 JSON；
  ④ services/pipeline 与 data_fetcher 在**模块级**不 import fund_pipeline / market_data
    （延迟 import：import 期无副作用）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_services_wiring.py
"""
import ast
import json
import os
import sys
import tempfile

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

from portfolio_system.services import data_fetcher, pipeline, portfolio_source  # noqa: E402

#: 脚本层不得直接 import 的模块（必须经 services）
DATA_MODULES = ("fund_pipeline", "market_data")
SCRIPTS = ("optimize_portfolio.py", "sensitivity_analysis.py")


def _module_level_imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    names = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def test_cli_scripts_do_not_import_data_modules_directly():
    """① 脚本层只能经 services 取数据/状态。"""
    bad = []
    for name in SCRIPTS:
        path = os.path.join(_SCRIPTS, name)
        if not os.path.isfile(path):
            continue
        for mod in _module_level_imports(path):
            if mod.split(".")[0] in DATA_MODULES:
                bad.append("%s: import %s" % (name, mod))
    assert not bad, "脚本层直接 import 了数据/流水线模块：%s" % bad


def test_pipeline_service_really_forwards():
    """② services.pipeline.ensure_work_dir 必须真的建目录（与 fund_pipeline 同一实现）。"""
    with tempfile.TemporaryDirectory(prefix="pm-svc-") as tmp:
        target = os.path.join(tmp, "work")
        got = pipeline.ensure_work_dir(target)
        assert got, got
        assert os.path.isdir(str(got)), got


def test_portfolio_source_load_extract_reads_json():
    """③ load_extract 真的是唯一 JSON 读取入口。"""
    payload = {"total_value": 1234.5, "domestic_weight": 0.3, "pool_mode": "fixture"}
    with tempfile.TemporaryDirectory(prefix="pm-svc-") as tmp:
        path = os.path.join(tmp, "extract.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        assert portfolio_source.load_extract(path) == payload


def test_services_do_not_import_data_modules_at_module_level():
    """④ import 期无副作用：服务模块只在函数体内延迟 import 上游数据模块。"""
    svc = os.path.join(_SCRIPTS, "portfolio_system", "services")
    bad = []
    for name in ("pipeline.py", "data_fetcher.py"):
        for mod in _module_level_imports(os.path.join(svc, name)):
            if mod.split(".")[0] in DATA_MODULES:
                bad.append("%s: import %s" % (name, mod))
    assert not bad, "services 在模块级 import 了数据模块（应延迟 import）：%s" % bad


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
