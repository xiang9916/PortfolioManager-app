#!/usr/bin/env python3
"""core 的**运行时传递依赖**守卫（task-60 D2 建立；task-61 P3-b1 扩到新搬入口）。

为什么需要它：`test_core_purity.py` 只做 **AST 静态** 扫描，看不见「调用期才发生的延迟 import」，
也看不见 import 链下游把别的东西拉进 sys.modules（ADR 0003 §P2/P3-a 的教训）。

做法：在**子进程**里 import core 并**真的调用**公开入口，再断言「调用 core 之后**新增**的
sys.modules 条目」不含网络/磁盘/子进程/上层业务模块。覆盖范围（P3-b1 后）：
 ① `core.constraints`：`active_cap_map` / `apply_active_caps` / `max_return_given_mu`
 ② `core.optimizer.LocalConvexOptimizer.optimize`（单层路径）
 ③ `core.stage2`：`stage2_covariance` / `stage2_optimize` / `stage2_japan_optimize` /
    `optimize_within_pool` / `pool_cap_norm` / `pool_max_return`（真跑 SLSQP 与池内贪心）
 ④ `core.sleeve_params`：`sleeve_params_us` / `sleeve_tangency` / `inject_sleeve_params` /
    `damp_sleeve_params`

基线在 numpy/scipy 导入之后取：它们是 core 允许的第三方依赖，自身就会拉进 urllib/subprocess，
那不是 core 的传递依赖（否则守卫会永远红、变成假守卫）。⚠️ 因此判定分两层：**根模块**
（urllib/requests/socket/ssl/subprocess/sqlite3/上层业务模块）按「基线之后新增」判定；
**子模块**（urllib.request / urllib.error …）单独按名字判定 —— 它们的根常已被第三方预先导入，
只看根会漏检（实测：numpy 会预导入 urllib，只看根则 `import urllib.request` 的证伪无法被抓住）。

守卫自身可证伪（两条，都要过）：
 · `test_guard_is_falsifiable`：往探针源码里注入**调用期**延迟 import → 必须红；
 · `test_lazy_import_in_moved_core_module_is_caught`：把一条调用期延迟 import 注入
   **新搬进 core 的模块**（`core/stage2.py` 的 `optimize_within_pool`）→ 必须红。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_runtime_deps_guard.py
祖先对照：PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts（本文件不硬写 ../scripts）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")

# P4：包根 = scripts 的兄弟目录（由 PM_OPT_SCRIPTS 派生；旧布局树回退到 scripts 内）。
_PKG_ROOT = os.path.dirname(os.path.abspath(_SCRIPTS))
pkg_dir = (os.path.join(_PKG_ROOT, 'portfolio_system')
           if os.path.isdir(os.path.join(_PKG_ROOT, 'portfolio_system'))
           else os.path.join(_SCRIPTS, 'portfolio_system'))

#: 守卫断言：这些**根**模块不得在调用 core 之后新增
FORBIDDEN_MODULES = ("urllib", "requests", "socket", "ssl", "subprocess", "sqlite3",
                     "fund_pipeline", "market_data", "test_tickers", "optimize_portfolio")
#: 子模块按名字单独判定（根可能已被第三方依赖预导入，只看根会漏检）
FORBIDDEN_SUBMODULES = ("urllib.request", "urllib.error", "subprocess.Popen", "ssl._ssl")

#: 探针源码（编译期常量；证伪用例会注入一条调用期延迟 import）
PROBE_SOURCE = r'''
"""core 运行时传递依赖探针：import + 真的调用公开入口，再把 sys.modules 报成一行 JSON。"""

import json
import sys

import numpy as np

FORBIDDEN_ROOTS = ("urllib", "requests", "socket", "ssl", "subprocess", "sqlite3",
                   "fund_pipeline", "market_data", "test_tickers", "optimize_portfolio")
FORBIDDEN_SUBMODULES = ("urllib.request", "urllib.error", "subprocess.Popen", "ssl._ssl")


def snapshot():
    return sorted(name for name in sys.modules
                  if name.split(".")[0] in FORBIDDEN_ROOTS)


def _submods():
    return sorted(name for name in FORBIDDEN_SUBMODULES if name in sys.modules)


def main():
    # 基线：numpy/scipy 是 core 允许的第三方依赖 —— 它们自己拉进来的不算 core 的账
    import numpy  # noqa: F401
    from scipy.optimize import minimize  # noqa: F401
    baseline = set(snapshot())
    baseline_submods = set(_submods())

    from portfolio_system.core import constraints
    from portfolio_system.core import sleeve_params
    from portfolio_system.core import stage2
    from portfolio_system.core.optimizer import LocalConvexOptimizer
    from portfolio_system.models.data_types import (AssetSpec, GoalSpec, PoolSpec,
                                                    ProblemSpec)

    # ① core.constraints：active 上限表 + 触顶记录 + 贪心 LP
    rows = [{"key": "A_ACTIVE", "pool": "overseas", "test_class": "active"}]
    upper = [1.0]
    warnings = []
    cap_map = constraints.active_cap_map(rows, ("A_ACTIVE",))
    hits = constraints.apply_active_caps(upper, rows, cap_map, [], 0.0, warnings)
    mret_given = constraints.max_return_given_mu(np.array([0.05, 0.08]), np.array([1.0, 1.0]),
                                                 [0], [1], 0.30, 0.70)

    # ② LocalConvexOptimizer.optimize：单层问题（无袖）→ 真纯求解路径
    problem = ProblemSpec(
        assets=(AssetSpec(key="BROAD_X", mu=0.08, sigma=0.15, asset_class="broad",
                          extra={"pool": "overseas"}),
                AssetSpec(key="A_ACTIVE", mu=0.12, sigma=0.30, asset_class="active",
                          extra={"pool": "overseas"})),
        goal=GoalSpec(target_return=0.05, rf=0.02),
        pools=PoolSpec(0.0, 1.0),
        extract={"internal_pool_keys": ("A_ACTIVE",)},
    )
    result = LocalConvexOptimizer().optimize(problem)

    # ③ core.stage2：阶段二 + 袖内池优化（全部真调用；虚构池，不碰 params）
    US = {"UA": {"mu": 0.06, "vol": 0.10}, "UB": {"mu": 0.14, "vol": 0.22}}
    US_CORR = {("UA", "UB"): 0.3}
    JP = {"JC": {"mu": 0.05, "vol": 0.12}}
    SP500 = {"UA": 0.90}
    SP500_DEF = 0.75

    broad_keys = ["O_US_CORE"]
    broad_vols = np.array([0.12])
    broad_cov = np.outer(broad_vols, broad_vols) * np.eye(1)
    mus, cov, keys = stage2.stage2_covariance(
        [], ["UA", "UB"], broad_cov, broad_keys, broad_vols,
        us_internal=US, us_internal_corr=US_CORR,
        sp500_internal_corr=SP500, sp500_internal_corr_default=SP500_DEF)

    phase2_hits = []
    assets_us = [{"key": "O_US_CORE", "mu": 0.05, "vol": 0.10, "pool": "overseas"}]
    best, err = stage2.stage2_optimize(
        np.array([0.20]), {"us_equity": {"holdings": [{"ticker": "UA"}, {"ticker": "UB"}]}},
        1000.0, assets_us,
        broad_cov, broad_keys, broad_vols, 0.01, 0.02,
        cap_map={"UA": 0.10, "UB": 0.10}, n_starts=5,
        us_internal=US, us_internal_corr=US_CORR,
        sp500_internal_corr=SP500, sp500_internal_corr_default=SP500_DEF,
        cap_hits_out=phase2_hits)

    jp_sel = stage2.stage2_japan_optimize(
        np.array([0.20]), [{"key": "O_JP_EQ", "mu": 0.05, "vol": 0.10, "pool": "overseas"}],
        1000.0, JP, {}, 0.02, cap_map=None, cap_hits_out=[])
    got = stage2.optimize_within_pool(US, US_CORR, 0.02, 19)
    cap_norm = stage2.pool_cap_norm(US, {"UA": 0.10}, 0.5)
    pool_mret = stage2.pool_max_return(US, US_CORR)

    # ④ core.sleeve_params：袖参数 / 切点 / 阻尼 / 注入
    us_params = sleeve_params.sleeve_params_us((got[0],), ["UA", "UB"], US, US_CORR)
    tangency = sleeve_params.sleeve_tangency("O_US_CORE", 0.02,
                                             {"O_US_CORE": (US, US_CORR, 19)})
    injected = sleeve_params.inject_sleeve_params(assets_us, {"O_US_CORE": (0.07, 0.11)})
    damped = sleeve_params.damp_sleeve_params({"O_US_CORE": (0.07, 0.11)},
                                              {"O_US_CORE": (0.05, 0.10)}, 0.5)

    # ⑤ core.engine.solve_portfolio：真的跑一遍两层流水线（虚构宇宙，不碰 params）
    from portfolio_system.core import engine
    from portfolio_system.core.sleeve_params import SleeveUniverse

    GC = {"GC": {"mu": 0.10, "vol": 0.20}}
    universe = SleeveUniverse(
        us_internal=US, us_internal_corr=US_CORR,
        jp_internal=JP, jp_internal_corr={},
        gc_internal=GC, gc_internal_corr={},
        sp500_internal_corr=SP500, sp500_internal_corr_default=SP500_DEF,
        pool_specs={"O_US_CORE": (US, US_CORR, 19), "O_JP_EQ": (JP, {}, 31),
                    "D_GREATER_CN": (GC, {}, 43)},
        internal_pool_keys=("A_ACTIVE", "UA"),
    )
    engine_hits = []
    engine_warns = []
    engine_out = engine.solve_portfolio(
        [{"key": "O_US_CORE", "mu": 0.08, "vol": 0.12, "pool": "overseas"},
         {"key": "A_ACTIVE", "mu": 0.12, "vol": 0.30, "pool": "overseas"}],
        {("O_US_CORE", "A_ACTIVE"): 0.3},
        dom_w=0.0, ov_w=1.0, target=0.05, rf=0.02,
        extract={"us_equity": {"holdings": [{"ticker": "UA"}]}}, total_assets=1000.0,
        universe=universe, cap_hits_out=engine_hits, warnings=engine_warns,
        max_sleeve_iter=2, stage1_starts=5, stage2_starts=5)

    after = snapshot()
    report = {
        "roots_added_after_core": sorted(set(after) - baseline),
        "roots_present": after,
        "roots_present_at_import": sorted(baseline),
        "submodules_after_core": sorted(set(_submods()) - baseline_submods),
        "submodules_present_at_import": sorted(baseline_submods),
        # ①
        "upper_after_caps": [float(x) for x in upper],
        "cap_hits": [[str(k), float(w), float(c)] for k, w, c in hits],
        "warnings": [str(w) for w in warnings],
        "weights": {str(k): float(v) for k, v in result.weights.items()},
        "final_source": str(result.final_source),
        "deliverable": bool(result.deliverable),
        "max_return_given_mu": float(mret_given),
        # ③
        "phase2_cov_keys": [str(k) for k in keys],
        "phase2_best_found": best is not None,
        "phase2_err": err,
        "phase2_cap_hits": len(phase2_hits),
        "japan_basket": bool(jp_sel),
        "within_pool": got is not None,
        "cap_norm": {str(k): float(v) for k, v in cap_norm.items()},
        "pool_max_return": float(pool_mret),
        # ④
        "us_params": [float(us_params[0]), float(us_params[1])],
        "tangency": [float(tangency[0]), float(tangency[1])],
        "injected_mu": float(injected[0]["mu"]),
        "damped_mu": float(damped["O_US_CORE"][0]),
        # ⑤
        "engine_ran": engine_out is not None,
        "engine_iterations": int((engine_out or {}).get("sleeve_iterations") or 0),
        "engine_deliverable": bool((engine_out or {}).get("deliverable", False)),
        "engine_warnings": len(engine_warns),
    }
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
'''


def _run_probe(scripts_dir, source=PROBE_SOURCE):
    """在子进程里跑探针，返回 (returncode, stdout, stderr)。"""
    with tempfile.TemporaryDirectory(prefix="pm-deps-guard-") as tmp:
        probe_path = os.path.join(tmp, "pm_deps_probe.py")
        with open(probe_path, "w", encoding="utf-8") as fh:
            fh.write(source)
        env = dict(os.environ)
        # P4：包根是 scripts 的兄弟目录 → PYTHONPATH 同时给两处（旧布局树也兼容）。
        env["PYTHONPATH"] = scripts_dir + os.pathsep + os.path.dirname(os.path.abspath(scripts_dir))
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
    # 探针必须**真的调用到了**这些路径，否则等于没验（防空转）
    assert report["cap_hits"] == [["A_ACTIVE", 0.1, 0.1]], report["cap_hits"]
    assert abs(report["upper_after_caps"][0] - 0.1) < 1e-12, report["upper_after_caps"]
    assert report["weights"] and report["final_source"] == "delivered", report
    assert report["max_return_given_mu"] > 0.0, report
    # P3-b1 新搬入口
    assert report["phase2_cov_keys"] == ["UA", "UB"], report
    assert report["phase2_best_found"] and report["phase2_err"] is None, report
    assert report["phase2_cap_hits"] == 2, report   # 触顶记录经 cap_hits_out 回传（core 不写全局）
    assert report["japan_basket"] and report["within_pool"], report
    assert report["cap_norm"] == {"UA": 0.2}, report["cap_norm"]
    assert report["pool_max_return"] > 0.0, report
    assert report["us_params"] and report["tangency"], report
    assert abs(report["injected_mu"] - 0.07) < 1e-12 and abs(report["damped_mu"] - 0.06) < 1e-12, report
    # P3-b2 新搬入口：core.engine.solve_portfolio 必须真的跑出结果（不是只 import）
    assert report["engine_ran"] and report["engine_iterations"] >= 1, report


def test_guard_is_falsifiable():
    """守卫可证伪：往探针源码注入一条**调用期**延迟 import → 必须被抓到。"""
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


def test_lazy_import_in_moved_core_module_is_caught():
    """守卫覆盖 P3-b1 新搬进 core 的模块：往 core/stage2.py 注入调用期延迟 import → 必须红。"""
    anchor = "    tickers = list(internal_pool.keys())"
    src_path = os.path.join(pkg_dir, "core", "stage2.py")
    if not os.path.isfile(src_path):
        raise AssertionError("找不到 %s（守卫未能覆盖新搬入的 core 模块）" % src_path)
    source = open(src_path, encoding="utf-8").read()
    assert source.count(anchor) >= 1, "注入锚点未命中 core/stage2.py"
    with tempfile.TemporaryDirectory(prefix="pm-deps-guard-tree-") as tmp:
        tree = os.path.join(tmp, "scripts")
        pkg_dst = os.path.join(tmp, "portfolio_system")
        shutil.copytree(_SCRIPTS, tree, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(pkg_dir, pkg_dst, ignore=shutil.ignore_patterns("__pycache__"))
        mutated = os.path.join(pkg_dst, "core", "stage2.py")
        body = open(mutated, encoding="utf-8").read()
        # 只在 optimize_within_pool 里注入（该函数在探针里被真调用）
        idx = body.index("def optimize_within_pool(")
        head, tail = body[:idx], body[idx:]
        tail = tail.replace(anchor, "    import urllib.request  # 证伪：调用期延迟 import\n" + anchor, 1)
        open(mutated, "w", encoding="utf-8").write(head + tail)
        rc, out, err = _run_probe(tree)
    report = _report_or_fail(rc, out, err)
    roots, subs = _violations(report)
    assert "urllib.request" in subs, (
        "往 core/stage2.py 注入调用期 import urllib.request 后守卫没变红：新增根=%s 新增子模块=%s"
        % (roots, report.get("submodules_after_core")))


def test_lazy_import_in_engine_is_caught():
    """守卫覆盖 P3-b2 新搬的 core/engine.py：注入调用期延迟 import → 必须红。"""
    rel = os.path.join("core", "engine.py")   # pkg_dir 已指向包内
    anchor = "    pool_specs = dict(universe.pool_specs or {})"
    src_path = os.path.join(pkg_dir, rel)
    assert os.path.isfile(src_path), "找不到 %s" % src_path
    assert anchor in open(src_path, encoding="utf-8").read(), "注入锚点未命中 engine.py"
    with tempfile.TemporaryDirectory(prefix="pm-deps-guard-tree-") as tmp:
        tree = os.path.join(tmp, "scripts")
        pkg_dst = os.path.join(tmp, "portfolio_system")
        shutil.copytree(_SCRIPTS, tree, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(pkg_dir, pkg_dst, ignore=shutil.ignore_patterns("__pycache__"))
        mutated = os.path.join(pkg_dst, rel)
        body = open(mutated, encoding="utf-8").read()
        body = body.replace(anchor, "    import urllib.request  # 证伪：调用期延迟 import" + chr(10) + anchor, 1)
        open(mutated, "w", encoding="utf-8").write(body)
        rc, out, err = _run_probe(tree)
    report = _report_or_fail(rc, out, err)
    roots, subs = _violations(report)
    assert "urllib.request" in subs, (
        "往 core/engine.py 注入调用期 import urllib.request 后守卫没变红：新增根=%s 新增子模块=%s"
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
