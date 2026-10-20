#!/usr/bin/env python3
"""「袖内固定点」单测（不联网）。

背景（2026-10-14 用户实测发现）：阶段一过去把袖（O_US_CORE / O_JP_EQ / D_GREATER_CN）
当成"当前持仓那一篮子"，而阶段二会在袖内重新选标的 —— 于是阶段一的模型 ≠ 交付组合：
目标落在切点收益以下时，阶段一正好贴着目标给、交付组合却被阶段二顶得更高，
只扫阶段一的敏感性分析因此与结果页对不上。
`solve_portfolio` 现在把袖内 μ/σ 迭代到固定点，本文件锁住这件事。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_sleeve_fixpoint.py
（venv 里没有 pytest，所以写成自带 runner 的脚本；函数名保持 test_* 以便将来接 pytest。）
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import optimize_portfolio as OP                     # noqa: E402
from params import BROAD_ASSETS, BROAD_CORR, US_INTERNAL, us_core_params  # noqa: E402

# 虚构的组合拆分与持仓：**不要**把真实比例/持仓写进来
DOM_W, OV_W = 0.40, 0.60
TOTAL = 1_000_000.0
RF = 0.03
TARGET = 0.06
LIVE_US = {"SPMO": 1.0}          # 虚构的"当前美股持仓"：只有一只动量 ETF


def make_extract():
    return {
        "pool_mode": "currency",
        "domestic_weight": DOM_W,
        "overseas_weight": OV_W,
        "domestic_value": TOTAL * DOM_W,
        "overseas_value": TOTAL * OV_W,
        "total_value": TOTAL,
        "rf": RF,
        "us_equity": {
            "total_value": TOTAL * OV_W,
            "holdings": [{"ticker": k, "name": "虚构", "value_cny": TOTAL * OV_W * w, "weight": w}
                         for k, w in LIVE_US.items()],
        },
    }


def make_assets():
    """与 optimize_portfolio.main() 同一口径：注入 O_US_CORE 聚合值、剔除缺 μ/σ 的标的。"""
    core_mu, core_vol = us_core_params(LIVE_US)
    assets = []
    for a in BROAD_ASSETS:
        item = dict(a)
        if item["key"] == "O_US_CORE":
            item["mu"], item["vol"] = core_mu, core_vol
        if item.get("mu") is None or item.get("vol") is None:
            continue
        assets.append(item)
    return assets


def solve(target=TARGET, sleeve_start=None):
    return OP.solve_portfolio(
        make_assets(), dict(BROAD_CORR), dom_w=DOM_W, ov_w=OV_W, target=target, rf=RF,
        extract=make_extract(), total_assets=TOTAL, warnings=[], sleeve_start=sleeve_start)


def test_sleeve_params_use_correlation_not_weighted_average():
    """袖内 σ 必须是"按相关性算的真实组合波动"，不能退化成各标的 σ 的加权平均。"""
    tickers = ["SPY", "VTV"]
    internal = (np.array([0.5, 0.5]), 0.0, 0.0, 0.0, None, tickers)
    mu_s, vol_s = OP._sleeve_params_us(internal, tickers)
    weighted = 0.5 * US_INTERNAL["SPY"]["vol"] + 0.5 * US_INTERNAL["VTV"]["vol"]
    assert vol_s < weighted, (vol_s, weighted)
    assert abs(mu_s - 0.5 * (US_INTERNAL["SPY"]["mu"] + US_INTERNAL["VTV"]["mu"])) < 1e-9


def test_fixpoint_converges_and_matches_model():
    """目标进入约束区（10%）时：固定点收敛，阶段一模型与交付组合几乎同源。"""
    res = solve(target=0.10)
    assert res is not None
    assert res["sleeve_converged"], f"未收敛：{res['sleeve_history']}"
    gap = abs(res["final"]["ret"] - res["stage1_ret"]) + abs(res["final"]["vol"] - res["stage1_vol"])
    assert gap < 0.005, f"阶段一模型与交付组合同源性不足：{gap:.4f}"
    assert res["sleeve_iterations"] >= 1


# 目标低于切点收益、且 live 篮子只有一只标的时，袖内选标会变成离散的"极限环"（没有真正的
# 固定点）。这时实现取"模型 vs 交付误差最小"的一轮并给出提示 —— 一致性仍然要有保证。
LOW_TARGET = 0.06


def test_unconverged_still_keeps_model_close():
    res = solve(target=LOW_TARGET)
    assert res is not None
    gap = abs(res["final"]["ret"] - res["stage1_ret"]) + abs(res["final"]["vol"] - res["stage1_vol"])
    # 未收敛/极限环档的容差比收敛档（0.005）宽一档，但仍要求模型与交付是同一量级。
    # 2026-10-20 总回报重校准后该档为 0.0065（SPMO μ 上行），仍远低于"不是同一组合"。
    assert gap < 0.010, f"未收敛时的模型误差过大：{gap:.4f}"
    if not res["sleeve_converged"]:
        # 不收敛必须明说，不能悄悄给个数
        assert any("未完全收敛" in w for w in res["warnings"])


def test_delivered_return_honours_target():
    """目标约束必须作用到**交付组合**上（这是固定点迭代要修的东西）。"""
    for target in (LOW_TARGET, 0.10, 0.12):
        res = solve(target=target)
        assert res is not None
        assert res["final"]["ret"] >= target - 1e-4, (target, res["final"]["ret"])


def test_sleeve_params_equal_delivered_mix():
    """收敛时：阶段一假设的美股袖参数，就是阶段二实际交付的那一篮子。"""
    res = solve(target=0.10)
    assert res is not None
    us = OP._sleeve_params_us(res["internal"], res["internal_tickers"])
    assumed = res["sleeve_params"]["O_US_CORE"]
    assert us is not None
    assert abs(us[0] - assumed[0]) < 0.01 and abs(us[1] - assumed[1]) < 0.01, (us, assumed)


def test_warm_start_matches_cold_start():
    """敏感性扫描用上一点的袖参数热启动，结果必须与冷启动一致（否则曲线会失真）。"""
    cold = solve(target=0.10)
    warm = solve(target=0.10, sleeve_start={k: tuple(v) for k, v in cold["sleeve_params"].items()})
    assert cold is not None and warm is not None
    for k in cold["sleeve_params"]:
        for a, b in zip(cold["sleeve_params"][k], warm["sleeve_params"][k]):
            assert abs(a - b) < 0.02, (k, cold["sleeve_params"][k], warm["sleeve_params"][k])


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  ✗ {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
