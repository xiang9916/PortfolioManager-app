#!/usr/bin/env python3
"""敏感性扫描的**离线**用例（P3-b3 步骤④：先补测试，再做解耦）。

背景：sensitivity_analysis.py 至今**没有任何测试**，而它是端到端要联网的那条路。
P3-b3 要把它从 optimize_portfolio 解耦（P3 的正式门），因此先补离线桩用例：
不联网、不读真实库、不跑管线 —— 装配（extract → assets）用桩替换。

本文件锁定四件事：
  ① **解算路径必须来自 core**：sensitivity_analysis 不得 import optimize_portfolio，
     且扫描必须真的调到 core.engine.solve_portfolio（用计数器证明，不是只 import）。
  ② 扫描输出结构/关键字段正确（点集、每点字段、max_feasible_return、warnings）。
  ③ 真引擎跑得通（单点、真实 solve，离线夹具）。
  ④ 热启动链路：第二点起把上一点的 sleeve_params 作为 sleeve_start 传下去。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_sensitivity_offline.py
祖先/修复前对照：PM_OPT_SCRIPTS=<旧树>/Optimizer/scripts（本文件不硬写 ../scripts）
"""

import ast
import json
import os
import subprocess
import sys

import numpy as np

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import sensitivity_analysis as SENS  # noqa: E402
from portfolio_system.core import engine as CORE_ENGINE  # noqa: E402

#: 虚构夹具（3 只大类资产，无袖 → 扫描逻辑与装配都可离线跑）
DOM_W, OV_W, TOTAL, RF = 0.30, 0.70, 1_000_000.0, 0.02
ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_GOLD", "name": "虚构黄金", "mu": 0.05, "vol": 0.15,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_BROAD", "name": "虚构海外宽基", "mu": 0.09, "vol": 0.20,
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


def _patch_assembly():
    """把装配（extract → assets）换成桩：不联网、不读真实库。"""
    saved = SENS.load_extract_assets

    def stub(extract, work_dir, **kw):
        return [dict(a) for a in ASSETS], {}, [], {"source": "fixture"}

    SENS.load_extract_assets = stub
    return saved


def _restore_assembly(saved):
    SENS.load_extract_assets = saved


def _module_imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    return mods


def test_sensitivity_does_not_import_optimize_portfolio():
    """① 解耦硬门：sensitivity_analysis 不得 import optimize_portfolio（含函数体内延迟 import）。"""
    path = os.path.join(_SCRIPTS, "sensitivity_analysis.py")
    bad = [m for m in _module_imports(path) if m.split(".")[0] == "optimize_portfolio"]
    assert not bad, "sensitivity_analysis 仍 import optimize_portfolio：%s" % bad


def test_importing_sensitivity_does_not_load_optimize_portfolio():
    # ①b 调用期 sys.modules 硬门：import sensitivity_analysis 后不得出现 optimize_portfolio。
    # （子进程里只 import —— 若模块级 import 了 optimize_portfolio，它必然落在 sys.modules 里。）
    probe = (
        "import json, sys" + chr(10) +
        "sys.path.insert(0, %r)" % _SCRIPTS + chr(10) +
        "import sensitivity_analysis" + chr(10) +
        "print(json.dumps(sorted(m for m in sys.modules"
        " if m.split('.')[0] == 'optimize_portfolio')))" + chr(10)
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = _SCRIPTS
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, "-B", "-c", probe], capture_output=True,
                          text=True, env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr[-1500:]
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert loaded == [], "import sensitivity_analysis 后 sys.modules 出现 optimize_portfolio：%s" % loaded


def test_scan_solves_through_core_engine():
    """② 扫描必须真的走 core.engine.solve_portfolio（计数器证明），且点结构正确。"""
    calls = []
    original = CORE_ENGINE.solve_portfolio

    def counting_solver(assets, corr_pairs, *, dom_w, ov_w, target, rf, extract, total_assets,
                         universe, **kw):
        calls.append({"target": float(target), "sleeve_start": kw.get("sleeve_start")})
        solved = original(assets, corr_pairs, dom_w=dom_w, ov_w=ov_w, target=target, rf=rf,
                          extract=extract, total_assets=total_assets, universe=universe, **kw)
        return solved

    saved = _patch_assembly()
    CORE_ENGINE.solve_portfolio = counting_solver
    try:
        out = SENS.run_sensitivity(dict(EXTRACT), work_dir=None, step_min=0.04, step_max=0.05,
                                   log=None)
    finally:
        CORE_ENGINE.solve_portfolio = original
        _restore_assembly(saved)
    assert calls, "扫描没有调用 core.engine.solve_portfolio（可能仍走 optimize_portfolio）"
    assert out["points"], out
    first = out["points"][0]
    for key in ("target_return", "feasible", "achieved_return", "volatility", "sharpe",
                "worst_year_95"):
        assert key in first, (key, first)
    assert isinstance(out["max_feasible_return"], float), out["max_feasible_return"]
    assert isinstance(out["warnings"], list), out
    assert out["pipeline"] == {"source": "fixture"}, out["pipeline"]
    # 热启动：第 2 次起应带上一点的 sleeve_params（空 dict 也要显式传）
    assert len(calls) >= 2, calls
    assert calls[1]["sleeve_start"] is not None, calls[1]


def test_scan_runs_real_core_engine_offline():
    """③ 真引擎单点扫描（离线夹具）：产出一个可行点且字段合理。"""
    saved = _patch_assembly()
    try:
        out = SENS.run_sensitivity(dict(EXTRACT), work_dir=None, step_min=0.045, step_max=0.045,
                                   log=None)
    finally:
        _restore_assembly(saved)
    assert out.get("error") is None, out
    feas = [p for p in out["points"] if p.get("feasible")]
    assert feas, out
    p = feas[0]
    assert p["achieved_return"] > 0 and p["volatility"] > 0, p
    assert abs(p["sharpe"] - (p["achieved_return"] - RF) / p["volatility"]) < 1e-3, p


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
