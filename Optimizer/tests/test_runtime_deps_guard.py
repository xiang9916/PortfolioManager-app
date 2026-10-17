#!/usr/bin/env python3
"""F：core 的**运行时传递依赖**守卫（task-60 D2 之后的独立核验点）。

为什么需要它：`test_core_purity.py` 只做 **AST 静态** 扫描，看不见「调用期才发生的延迟 import」，
也看不见 import 链下游把别的东西拉进 sys.modules（ADR 0003 §P2 已把这条列为残留风险）。

做法：在**子进程**里 import core 并**真的调用**会走 active 上限表的公开入口
（`core.constraints.apply_active_caps` + `LocalConvexOptimizer.optimize` 单层路径），
再断言「调用 core 之后**新增**的 sys.modules 条目」不含网络/磁盘/子进程/上层业务模块。
基线在 numpy/scipy 导入之后取：它们是 core 允许的第三方依赖，自身就会拉进 urllib/subprocess，
那不是 core 的传递依赖（否则守卫会永远红、变成假守卫）。
⚠️ 因此判定分两层：**根模块**（urllib/requests/socket/ssl/subprocess/sqlite3/上层业务模块）
按「基线之后新增」判定；**子模块**（urllib.request / urllib.error …）单独按名字判定——
它们的根常已被第三方预先导入，只看根会漏检（实测：numpy 会预导入 urllib，只看根则
`import urllib.request` 的证伪无法被抓住）。

守卫自身可证伪：`test_guard_is_falsifiable` 往探针源码里注入一条**调用期**延迟 import
（`import urllib.request`），`urllib.request` 必须出现在「新增子模块」里 → 守卫变红；还原后必须绿。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_runtime_deps_guard.py
祖先对照：PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts（本文件不硬写 ../scripts）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")

#: 守卫断言：这些**根**模块不得在调用 core 之后新增
FORBIDDEN_MODULES = ("urllib", "requests", "socket", "ssl", "subprocess", "sqlite3",
                     "fund_pipeline", "market_data", "test_tickers", "optimize_portfolio")
#: 子模块按名字单独判定（根可能已被第三方依赖预导入，只看根会漏检）
FORBIDDEN_SUBMODULES = ("urllib.request", "urllib.error", "subprocess.Popen", "ssl._ssl")

#: 探针源码（编译期常量；由 test_guard_is_falsifiable 注入一条调用期延迟 import 来证伪）
PROBE_SOURCE = (
    "\"\"\"D2 运行时传递依赖守卫探针：import + **真的调用** core 公开入口，再报 sys.modules。\n"
    "\n"
    "stdout 只允许有一行 JSON（守卫靠它判定）；异常走 stderr + 非 0 退出码。\n"
    "\n"
    "为什么比较「基线 vs 之后」：numpy/scipy 自身会 import urllib / subprocess 等第三方依赖，\n"
    "那不是 core 的传递依赖；只有**基线里没有、调用 core 之后才出现**的才算 core 拉进来的。\n"
    "子模块（如 urllib.request，其根 urllib 常已被第三方预先导入）单独按名字判定。\n"
    "\"\"\"\n"
    "\n"
    "import json\n"
    "import sys\n"
    "\n"
    "FORBIDDEN_ROOTS = (\"urllib\", \"requests\", \"socket\", \"ssl\", \"subprocess\", \"sqlite3\",\n"
    "                   \"fund_pipeline\", \"market_data\", \"test_tickers\", \"optimize_portfolio\")\n"
    "FORBIDDEN_SUBMODULES = (\"urllib.request\", \"urllib.error\", \"subprocess.Popen\", \"ssl._ssl\")\n"
    "\n"
    "\n"
    "def snapshot():\n"
    "    return sorted(name for name in sys.modules\n"
    "                  if name.split(\".\")[0] in FORBIDDEN_ROOTS)\n"
    "\n"
    "\n"
    "def _submods():\n"
    "    return sorted(name for name in FORBIDDEN_SUBMODULES if name in sys.modules)\n"
    "\n"
    "\n"
    "def main():\n"
    "    # 基线：numpy/scipy 是 core 允许的第三方依赖 —— 它们自己拉进来的不算 core 的账\n"
    "    import numpy  # noqa: F401\n"
    "    from scipy.optimize import minimize  # noqa: F401\n"
    "    baseline = set(snapshot())\n"
    "    baseline_submods = set(_submods())\n"
    "\n"
    "    from portfolio_system.core import constraints\n"
    "    from portfolio_system.core.optimizer import LocalConvexOptimizer\n"
    "    from portfolio_system.models.data_types import (AssetSpec, GoalSpec, PoolSpec,\n"
    "                                                    ProblemSpec)\n"
    "\n"
    "    # ① core.constraints.apply_active_caps：真的调用（会走 active 上限 + 触顶记录返回）\n"
    "    rows = [{\"key\": \"A_ACTIVE\", \"pool\": \"overseas\", \"test_class\": \"active\"}]\n"
    "    upper = [1.0]\n"
    "    warnings = []\n"
    "    cap_map = constraints.active_cap_map(rows, (\"A_ACTIVE\",))\n"
    "    hits = constraints.apply_active_caps(upper, rows, cap_map, [], 0.0, warnings)\n"
    "\n"
    "    # ② LocalConvexOptimizer.optimize：单层问题（无袖）→ 真纯求解路径\n"
    "    problem = ProblemSpec(\n"
    "        assets=(AssetSpec(key=\"BROAD_X\", mu=0.08, sigma=0.15, asset_class=\"broad\",\n"
    "                          extra={\"pool\": \"overseas\"}),\n"
    "                AssetSpec(key=\"A_ACTIVE\", mu=0.12, sigma=0.30, asset_class=\"active\",\n"
    "                          extra={\"pool\": \"overseas\"})),\n"
    "        goal=GoalSpec(target_return=0.05, rf=0.02),\n"
    "        pools=PoolSpec(0.0, 1.0),\n"
    "        extract={\"internal_pool_keys\": (\"A_ACTIVE\",)},\n"
    "    )\n"
    "    result = LocalConvexOptimizer().optimize(problem)\n"
    "\n"
    "    after = snapshot()\n"
    "    report = {\n"
    "        \"roots_added_after_core\": sorted(set(after) - baseline),\n"
    "        \"roots_present\": after,\n"
    "        \"roots_present_at_import\": sorted(baseline),\n"
    "        \"submodules_after_core\": sorted(set(_submods()) - baseline_submods),\n"
    "        \"submodules_present_at_import\": sorted(baseline_submods),\n"
    "        \"upper_after_caps\": [float(x) for x in upper],\n"
    "        \"cap_hits\": [[str(k), float(w), float(c)] for k, w, c in hits],\n"
    "        \"warnings\": [str(w) for w in warnings],\n"
    "        \"weights\": {str(k): float(v) for k, v in result.weights.items()},\n"
    "        \"final_source\": str(result.final_source),\n"
    "        \"deliverable\": bool(result.deliverable),\n"
    "    }\n"
    "    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))\n"
    "\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    main()\n"
)


def _run_probe(scripts_dir, source=PROBE_SOURCE):
    """在子进程里跑探针，返回 (returncode, stdout, stderr)。"""
    with tempfile.TemporaryDirectory(prefix="pm-deps-guard-") as tmp:
        probe_path = os.path.join(tmp, "pm_deps_probe.py")
        with open(probe_path, "w", encoding="utf-8") as fh:
            fh.write(source)
        env = dict(os.environ)
        env["PYTHONPATH"] = scripts_dir
        env["PM_OPT_SCRIPTS"] = scripts_dir
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run([sys.executable, "-B", probe_path],
                              capture_output=True, text=True, env=env, timeout=300)
    return proc.returncode, proc.stdout, proc.stderr


def _violations(report):
    """返回 (新增禁用根模块, 新增禁用子模块)。"""
    roots = [m for m in FORBIDDEN_MODULES if m in report["roots_added_after_core"]]
    subs = [m for m in FORBIDDEN_SUBMODULES if m in report.get("submodules_after_core", [])]
    return roots, subs


def _report_or_fail(rc, out, err):
    assert rc == 0, "探针子进程失败 rc=%s\nstdout=%s\nstderr=%s" % (rc, out[-2000:], err[-2000:])
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        raise AssertionError("探针输出不是 JSON 一行：%r（stderr=%s）" % (out[:500], err[-500:])) from exc


def test_core_calls_do_not_pull_forbidden_modules():
    """F 主体：import + 调用 core 公开入口后，不得新增网络/磁盘/子进程/上层业务模块。"""
    rc, out, err = _run_probe(_SCRIPTS)
    report = _report_or_fail(rc, out, err)
    roots, subs = _violations(report)
    assert not roots and not subs, (
        "调用 core 公开入口后新增禁用依赖：根=%s 子模块=%s\n新增根=%s\n基线根=%s"
        % (roots, subs, report["roots_added_after_core"], report["roots_present_at_import"]))
    # 探针必须**真的调用到了** active 上限路径，否则等于没验
    assert report["cap_hits"] == [["A_ACTIVE", 0.1, 0.1]], report["cap_hits"]
    assert abs(report["upper_after_caps"][0] - 0.1) < 1e-12, report["upper_after_caps"]
    assert report["weights"] and report["final_source"] == "delivered", report


def test_guard_is_falsifiable():
    """守卫可证伪：注入一条**调用期**延迟 import → 必须被抓到。"""
    injected = PROBE_SOURCE.replace(
        "    hits = constraints.apply_active_caps(upper, rows, cap_map, [], 0.0, warnings)",
        "    import urllib.request  # 证伪：调用期延迟 import\n"
        "    hits = constraints.apply_active_caps(upper, rows, cap_map, [], 0.0, warnings)")
    assert injected != PROBE_SOURCE, "注入点未命中探针源码"
    rc, out, err = _run_probe(_SCRIPTS, source=injected)
    report = _report_or_fail(rc, out, err)
    roots, subs = _violations(report)
    assert "urllib.request" in subs, (
        "注入调用期 import urllib.request 后守卫没变红（假守卫）：新增根=%s 新增子模块=%s"
        % (roots, report.get("submodules_after_core")))


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
