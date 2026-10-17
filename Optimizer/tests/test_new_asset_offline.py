#!/usr/bin/env python3
"""新标的注入（③）的离线用例：必须走 callers.NewAssetRunner，且联网取数走 services。

背景：P3-b3 步骤③把 optimize_portfolio._inject_test_assets 的实现搬进
callers/new_asset_runner.NewAssetRunner.inject_test_assets；test_tickers.py 的**联网取数**
（cached_series / series_for_broad_key）改走 services.data_fetcher（唯一网络入口）。

本文件锁定三件事（全程离线，不联网、不读库）：
  ① optimize_portfolio 的薄壳真的调 runner（计数器证明），且 CLI 契约不变：
     全部测试标的不可用 → SystemExit(3)；
  ② 无 --test-tickers 时原样透传（assets 不变、corr 为 None、无告警）；
  ③ test_tickers.py 模块级不得从 market_data 取联网函数（白名单只放纯数学）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_new_asset_offline.py
"""
import ast
import os
import sys
from types import SimpleNamespace

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)
# P4：包根 = scripts 的兄弟目录（由 PM_OPT_SCRIPTS 派生 → 祖先/旧布局树同样可用）。
_PKG_ROOT = os.path.dirname(os.path.abspath(_SCRIPTS))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import optimize_portfolio as OP  # noqa: E402
from portfolio_system.callers import new_asset_runner as NAR  # noqa: E402

#: market_data 里允许被 test_tickers 直接 import 的**纯数学**函数（不碰 I/O）
PURE_MATH = {"annualized_cagr", "annualized_vol", "daily_return_map", "pairwise_corr"}
#: 必须经 services 的联网函数
NETWORK = {"cached_series", "series_for_broad_key"}


def test_test_tickers_network_functions_come_from_services():
    """③ test_tickers 的联网取数必须来自 services.data_fetcher。"""
    path = os.path.join(_SCRIPTS, "test_tickers.py")
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    from_market_data, from_services = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = {a.name for a in node.names}
            if (node.module or "").split(".")[0] == "market_data":
                from_market_data |= names
            if (node.module or "").endswith("services.data_fetcher"):
                from_services |= names
    assert not (from_market_data & NETWORK), (
        "test_tickers 仍直接从 market_data 取联网函数: %s" % sorted(from_market_data & NETWORK))
    assert NETWORK <= from_services, (
        "test_tickers 未从 services 取这些联网函数: %s" % sorted(NETWORK - from_services))
    assert from_market_data <= PURE_MATH, (
        "test_tickers 从 market_data 取了非白名单名字: %s" % sorted(from_market_data - PURE_MATH))


def test_shell_calls_runner_and_keeps_exit_contract():
    """① 薄壳真的调 runner；全部标的不可用时 SystemExit(3)。"""
    calls = []
    original = NAR.NewAssetRunner.inject_test_assets

    def counting(self, assets, **kw):
        calls.append(sorted(kw))
        return original(self, assets, **kw)

    NAR.NewAssetRunner.inject_test_assets = counting
    saved_resolve = OP.resolve_test_assets
    assets = [{"key": "O_GOLD", "pool": "overseas", "mu": 0.05, "vol": 0.15}]
    args = SimpleNamespace(test_tickers="T_X", test_class="active", log_path=None)
    try:
        # (a) 解析成功 → 追加进 assets
        OP.resolve_test_assets = lambda *a, **k: (
            [{"key": "T_X", "pool": "overseas", "name": "T_X", "mu": 0.1, "vol": 0.2}],
            {("T_X", "O_GOLD"): 0.3}, [], [])
        warnings = []
        out_assets, corr, skipped, estimates = OP._inject_test_assets(
            args, [dict(a) for a in assets], ".", warnings)
        assert [a["key"] for a in out_assets] == ["O_GOLD", "T_X"], out_assets
        assert corr == {("T_X", "O_GOLD"): 0.3}, corr
        assert calls, "薄壳没有调用 NewAssetRunner.inject_test_assets"
        # (b) 全部不可用 → 打印错误 JSON 且退 3（CLI 契约）
        OP.resolve_test_assets = lambda *a, **k: ([], None, [("T_X", "取不到行情")], [])
        try:
            OP._inject_test_assets(args, [dict(a) for a in assets], ".", [])
            raise AssertionError("全部测试标的不可用时没有退出")
        except SystemExit as exc:
            assert exc.code == 3, exc.code
    finally:
        NAR.NewAssetRunner.inject_test_assets = original
        OP.resolve_test_assets = saved_resolve


def test_no_test_tickers_is_passthrough():
    """② 没有 --test-tickers 时原样透传（不解析、不告警）。"""
    saved_resolve = OP.resolve_test_assets

    def boom(*a, **k):
        raise AssertionError("没有测试标的时不应调用解析器")

    OP.resolve_test_assets = boom
    try:
        assets = [{"key": "O_GOLD", "pool": "overseas"}]
        warnings = []
        args = SimpleNamespace(test_tickers=None, test_class="active", log_path=None)
        got_assets, corr, skipped, estimates = OP._inject_test_assets(args, assets, ".", warnings)
        assert got_assets is assets and corr is None and not skipped and not estimates
        assert warnings == [], warnings
    finally:
        OP.resolve_test_assets = saved_resolve


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
