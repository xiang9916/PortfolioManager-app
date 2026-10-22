#!/usr/bin/env python3
"""IALT（系统化另类多策略）注册 + 短样本置信区间保守口径（ADR 0007）。

锁定这些决策：
1. IALT 是**顶层 strategy 腿**（`O_IALT`），不进 US/JP/GC 内部池；
2. 数据源 `yahoo/IALT`，**不挂长历史代理**（无同质序列），短样本靠置信区间抑制噪声；
3. 校准阶段把 μ 收缩到 95% 置信**下界**、σ 扩张到**上界**，原始历史统计仍写进 provenance；
4. 持有 IALT 属于 modeled（不再被注入为 `H_IALT` what-if 腿）；
5. 结果页执行代码是 `IALT`。

离线：只读注册表与仓库里的 calibrated_params.json，不联网。
运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_ialt_registration.py
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, os.path.join(_HERE, ".."))

import asset_classes as AC          # noqa: E402
import held_assets as HA            # noqa: E402
import market_data as M             # noqa: E402
import params as P                  # noqa: E402
from portfolio_system.callers.preset_runner import _EXEC_CODE   # noqa: E402

KEY = "O_IALT"


def _calibration():
    with open(P.CALIBRATION_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 注册

def test_registry_complete():
    assert AC.ASSET_CLASS.get(KEY) == "strategy", AC.ASSET_CLASS.get(KEY)
    assert KEY in AC.RF_PREMIUM and KEY in AC.PRIOR
    assert AC.RF_PREMIUM[KEY][1] == "usd"
    # 0.5% 粒度（与 test_asset_classes 同一约定）
    prem = AC.RF_PREMIUM[KEY][0]
    assert abs(round(prem / 0.005) * 0.005 - prem) < 1e-9
    assert abs(round(AC.PRIOR[KEY] / 0.005) * 0.005 - AC.PRIOR[KEY]) < 1e-9
    errs, _ = AC.audit_table()
    assert errs == [], errs


def test_sources_and_no_proxy():
    assert M.SOURCES.get(KEY) == ("yahoo", "IALT")
    assert M.PROXY_MAP.get(KEY) is None, "IALT 没有同质长历史代理（ADR 0007）"


def test_top_level_leg_not_internal():
    assert KEY in [a["key"] for a in P.BROAD_ASSETS]
    item = next(a for a in P.BROAD_ASSETS if a["key"] == KEY)
    assert item["pool"] == "overseas" and item.get("name")
    assert KEY not in P.US_INTERNAL and KEY not in P.JP_INTERNAL and KEY not in P.GC_INTERNAL


def test_ci_policy_only_for_ialt():
    assert set(AC.CONSERVATIVE_CI) == {KEY}, AC.CONSERVATIVE_CI
    assert AC.CONSERVATIVE_CI[KEY]["level"] == 0.95


# ---------------------------------------------------------------- CI 数学

def test_ci_bounds_math():
    mu_low, vol_high, info = AC.ci_bounds(0.2263, 0.0753, 215)
    assert 0.060 < mu_low < 0.072, mu_low          # ≈ 6.6%
    assert 0.081 < vol_high < 0.086, vol_high      # ≈ 8.3%
    assert mu_low < 0.2263 and vol_high > 0.0753
    assert info["n"] == 215 and info["level"] == 0.95
    assert info["t"] < 0 < info["chi2"] and info["se_mu"] > 0
    # 样本越短越宽
    m_short, v_short, _ = AC.ci_bounds(0.2263, 0.0753, 60)
    assert m_short < mu_low and v_short > vol_high
    # 置信水平越高越宽
    m99, v99, _ = AC.ci_bounds(0.2263, 0.0753, 215, 0.99)
    assert m99 < mu_low and v99 > vol_high
    # 退化输入 → 不套用
    assert AC.ci_bounds(0.10, 0.10, 1)[0] is None
    assert AC.ci_bounds(None, 0.10, 215)[0] is None
    assert AC.ci_bounds(0.10, 0.0, 215)[0] is None


def test_no_manual_history_weight_override():
    """短样本抑制走统计口径，不再加人工 mu_hist_weight 覆盖。"""
    assert KEY not in AC.CLASS_OVERRIDES, AC.CLASS_OVERRIDES.get(KEY)
    assert AC.class_rule(KEY)["mu_hist_weight"] == AC.CLASS_RULES["strategy"]["mu_hist_weight"]


# ---------------------------------------------------------------- 校准数据

def test_calibration_uses_ci():
    cal = _calibration()
    entry = cal["assets"][KEY]
    prov = cal["provenance"][KEY]
    ci = prov["ci_bounds"]
    assert ci["level"] == 0.95 and 180 <= ci["n"] <= 400, ci
    assert ci["mu_low"] < ci["mu_hist_raw"], ci
    assert ci["vol_high"] > ci["vol_hist_raw"], ci
    assert abs(prov["mu_input"] - ci["mu_low"]) < 1e-9
    assert abs(prov["vol_input"] - ci["vol_high"]) < 1e-9
    assert prov["class"] == "strategy"
    assert prov["series"] == "adjclose" and str(prov["source"]).upper() == "IALT"
    # 最终 μ = 类别混合（历史输入 = CI 下界）
    w = AC.CLASS_RULES["strategy"]["mu_hist_weight"]
    expected_mu = w * ci["mu_low"] + (1 - w) * prov["prior"]
    assert abs(entry["mu"] - expected_mu) < 2e-4, (entry["mu"], expected_mu)
    assert abs(entry["vol"] - ci["vol_high"]) < 1e-4
    # 先验 = 美元 RF（数据源）+ 2.0%
    rf_usd = cal["prior_rf"]["usd"]
    assert abs(cal["priors"][KEY] - (rf_usd + 0.020)) < 1e-6
    assert abs(prov["prior"] - (rf_usd + 0.020)) < 1e-6


def test_calibration_corr_covers_universe():
    cal = _calibration()
    pairs = cal["corr"]
    for other in ("O_US_CORE", "O_GOLD", "O_US_TLT", "SPY"):
        key = f"{KEY}|{other}" if KEY < other else f"{other}|{KEY}"
        assert key in pairs, key
        assert -1.0 <= pairs[key] <= 1.0, (key, pairs[key])


def test_params_exposes_calibrated_values():
    assert P.param_origin(KEY) == "calibrated", P.param_origin(KEY)
    item = next(a for a in P.BROAD_ASSETS if a["key"] == KEY)
    assert item["mu"] is not None and item["vol"] is not None
    assert 0.0 < item["mu"] < 0.30 and 0.0 < item["vol"] < 1.50
    text = P.describe_param_source(KEY)
    assert "保守口径" in text, text


# ---------------------------------------------------------------- 装配/持仓

def test_held_ialt_is_modeled():
    extract = {"held_assets": [
        {"ticker": "IALT", "value_cny": 100.0, "weight": 0.10},
        {"ticker": "ZZFICT", "value_cny": 100.0, "weight": 0.10},
    ]}
    assets = [{"key": KEY, "fund_code": None, "pool": "overseas"}]
    modeled, unmodeled = HA.classify_held_assets(extract, assets, {})
    assert "IALT" in {e["harmonized_ticker"] for e in modeled}
    assert "ZZFICT" in {e["harmonized_ticker"] for e in unmodeled}


def test_exec_code():
    assert _EXEC_CODE.get(KEY) == "IALT"


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
