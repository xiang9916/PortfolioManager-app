#!/usr/bin/env python3
"""两个内部 API 崩溃点的守卫回归测试（P2，不联网、全虚构参数）。

背景（2026-10-15 P2）：
  ① stage2_optimize 在 broad_keys 里没有 O_US_CORE 时直接 broad_keys.index(...) 抛裸
     ValueError（消息只有 "'O_US_CORE' is not in list"，无任何上下文），整次求解崩掉；
  ② solve_portfolio(sleeve_start=...) 传入 σ=0 的袖参数时，build_broad_cov 里出现 0/0 → NaN，
     随后 stage2_covariance 调 project_psd 抛
     numpy.linalg.LinAlgError: Eigenvalues did not converge（同样无上下文）。

修复口径：① 安全降级 —— 返回 (None, 可诊断原因)，由 _solve_once 记进 warnings，不崩栈；
          ② 入口提前校验，抛**含 key / 取值 / 来源**的 ValueError，不等到 numpy 内部报错。
两条都必须显式可见（原因是调用方传入的参数），不许静默吞掉、也不许改成"看起来正常"的结果。

运行（本环境无 pytest，逐文件直跑）：
  Optimizer/.venv/bin/python3 Optimizer/tests/test_solver_guards.py
修复前对照（同一份测试加载修复前的 optimize_portfolio.py，应当至少 1 项失败）：
  PM_OPT_SCRIPTS=tmp/agent-team/prefix/task-7 PYTHONPATH=Optimizer/scripts \
    Optimizer/.venv/bin/python3 Optimizer/tests/test_solver_guards.py
"""

import os
import sys

import numpy as np

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)

import optimize_portfolio as OP  # noqa: E402

# ---- 虚构参数（与任何真实持仓无关）----
DOM_W, OV_W = 0.25, 0.75
TOTAL = 1_000_000.0
RF = 0.02

FAKE_US = {"UA": {"name": "虚构美股A", "mu": 0.06, "vol": 0.10},
           "UB": {"name": "虚构美股B", "mu": 0.30, "vol": 0.18}}
FAKE_JP = {"JC": {"name": "虚构日股C", "mu": 0.05, "vol": 0.12},
           "JD": {"name": "虚构日股D", "mu": 0.08, "vol": 0.15}}
FAKE_CORR = {("UA", "UB"): 0.3, ("JC", "JD"): 0.3}

ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_US_CORE", "name": "虚构美股核心袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_JP_EQ", "name": "虚构日本权益袖", "mu": 0.02, "vol": 0.10,
     "pool": "overseas", "test_class": "broad"},
    {"key": "O_BTC", "name": "虚构高收益资产", "mu": 0.20, "vol": 0.40,
     "pool": "overseas", "test_class": "broad"},
]
# 崩溃点①：资产表里没有美股核心袖
ASSETS_NO_CORE = [a for a in ASSETS if a["key"] != "O_US_CORE"]

EXTRACT = {
    "pool_mode": "fixture",
    "domestic_weight": DOM_W, "overseas_weight": OV_W,
    "domestic_value": TOTAL * DOM_W, "overseas_value": TOTAL * OV_W,
    "total_value": TOTAL, "rf": RF,
    "us_equity": {"total_value": TOTAL * OV_W,
                  "holdings": [{"ticker": "UA", "name": "虚构",
                                "value_cny": TOTAL * OV_W, "weight": 1.0}]},
}


class patched_pools:
    """把模块级袖内候选池换成虚构池（用完还原），让本文件完全自包含。"""

    def __enter__(self):
        self.saved = (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
                      getattr(OP, "_SLEEVE_POOL_SPECS", None))
        OP.US_INTERNAL = FAKE_US
        OP.US_INTERNAL_CORR = FAKE_CORR
        OP.JP_INTERNAL = FAKE_JP
        OP.JP_INTERNAL_CORR = FAKE_CORR
        OP._SLEEVE_POOL_SPECS = {"O_US_CORE": (FAKE_US, FAKE_CORR, 19),
                                 "O_JP_EQ": (FAKE_JP, FAKE_CORR, 31)}
        return self

    def __exit__(self, *exc):
        (OP.US_INTERNAL, OP.US_INTERNAL_CORR, OP.JP_INTERNAL, OP.JP_INTERNAL_CORR,
         saved_specs) = self.saved
        if saved_specs is None:
            del OP._SLEEVE_POOL_SPECS
        else:
            OP._SLEEVE_POOL_SPECS = saved_specs
        return False


def run_solve(assets, sleeve_start=None, target=0.10):
    with patched_pools():
        return OP.solve_portfolio([dict(a) for a in assets], {}, dom_w=DOM_W, ov_w=OV_W,
                                  target=target, rf=RF, extract=EXTRACT, total_assets=TOTAL,
                                  warnings=[], log=None, max_sleeve_iter=2,
                                  stage1_starts=30, stage2_starts=20,
                                  sleeve_start=sleeve_start)


# ------------------------------------------------- ① 缺 O_US_CORE：安全降级 + 可诊断

def test_missing_us_core_returns_diagnostic():
    """直接调 stage2_optimize：不得抛裸 ValueError，必须返回 (None, 可诊断原因)。"""
    keys = [a["key"] for a in ASSETS_NO_CORE]
    vols = np.array([a["vol"] for a in ASSETS_NO_CORE])
    cov = OP.build_broad_cov([{"key": a["key"], "vol": a["vol"]} for a in ASSETS_NO_CORE], {})
    with patched_pools():
        got = OP.stage2_optimize(np.array([0.5, 0.5]), EXTRACT, TOTAL, ASSETS_NO_CORE,
                                 cov, keys, vols, n_starts=20)
    assert isinstance(got, tuple) and len(got) == 2, got
    result, reason = got
    assert result is None, result
    assert "O_US_CORE" in reason, reason
    assert "D1" in reason and "O_BTC" in reason, reason      # 上下文：实际大类


def test_missing_us_core_is_reported_not_crashed():
    """端到端：缺 O_US_CORE 时求解照常返回，但必须把原因写进 warnings（不静默）。"""
    res = run_solve(ASSETS_NO_CORE)
    assert res is not None, "缺 O_US_CORE 时不得整次求解失败"
    joined = " ".join(res["warnings"])
    assert "O_US_CORE" in joined, res["warnings"]
    assert "没有 O_US_CORE" in joined, joined
    assert "美股袖没有可优化的内部篮子" in joined, joined


# ------------------------------------------------- ② σ=0 的 sleeve_start：可诊断报错

def _capture_value_error(sleeve_start):
    try:
        run_solve(ASSETS, sleeve_start=sleeve_start)
    except ValueError as exc:
        return str(exc)
    return None


def test_zero_vol_sleeve_start_raises_diagnostic_error():
    msg = _capture_value_error({"O_US_CORE": (0.08, 0.0)})
    assert msg is not None, "σ=0 的 sleeve_start 必须报错，不得静默继续"
    assert "O_US_CORE" in msg, msg
    assert "sleeve_start" in msg, msg
    assert "0.0" in msg, msg
    assert "σ>0" in msg, msg


def test_negative_vol_sleeve_start_raises_diagnostic_error():
    msg = _capture_value_error({"O_US_CORE": (0.08, -0.05)})
    assert msg is not None, "σ<0 的 sleeve_start 必须报错"
    assert "O_US_CORE" in msg and "sleeve_start" in msg, msg
    assert "-0.05" in msg, msg


# ------------------------------------------------- 反向对照：合法 sleeve_start 不得被误拒

def test_valid_sleeve_start_still_accepted():
    res = run_solve(ASSETS, sleeve_start={"O_US_CORE": (0.08, 0.12)})
    assert res is not None, "合法 sleeve_start 不得被守卫误拒"
    assert "final" in res and "sleeve_params" in res


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  OK " + name)
        except AssertionError as exc:
            failed += 1
            print("  FAIL " + name + ": " + str(exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
    print("\n%d/%d 通过" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
