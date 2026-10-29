#!/usr/bin/env python3
"""统一 μ/σ 数值护栏（ADR 0012，2026-10-29）的单测（离线）。

锁定：
1. 护栏只有一份 = asset_classes.MU_BOUNDS / SIGMA_BOUNDS，所有类别/标的共用；
2. 类别规则只剩 {label, mu_hist_weight, window_years}，不再有单标的覆盖表；
3. σ 三态：带内原样、>60% 截断到 60%、>150% 仍按数据毛刺丢弃；
4. 仓库里的 calibrated_params.json 带 bounds 快照，4 个受影响标的的 provenance 可见；
5. params.py 对旧版校准文件做防御式重截断并告警（不静默采用越界值）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_param_bounds.py
"""

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scripts"))
sys.path.insert(0, os.path.join(_HERE, ".."))

import asset_classes as AC          # noqa: E402
import params as P                  # noqa: E402


def _calibration():
    with open(P.CALIBRATION_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 护栏本身

def test_bounds_payload_shape():
    assert AC.MU_BOUNDS == (-0.10, 0.30)
    assert AC.SIGMA_BOUNDS == (0.01, 0.60)
    assert AC.BOUNDS_VERSION == 2
    assert AC.bounds_payload() == {
        "version": 2, "mu_lo": -0.10, "mu_hi": 0.30,
        "sigma_lo": 0.01, "sigma_hi": 0.60,
    }
    assert abs(AC.clamp_mu(-0.50) + 0.10) < 1e-12
    assert abs(AC.clamp_mu(0.90) - 0.30) < 1e-12
    assert abs(AC.clamp_sigma(0.001) - 0.01) < 1e-12
    assert abs(AC.clamp_sigma(0.90) - 0.60) < 1e-12


def test_rules_have_no_per_class_or_per_asset_bounds():
    assert not hasattr(AC, "CLASS_OVERRIDES")
    for cls, rule in AC.CLASS_RULES.items():
        assert set(rule) == {"label", "mu_hist_weight", "window_years"}, (cls, rule)
    for key in ("O_BTC", "NVDA", "D_HSBC_SP500", "SPMO"):
        assert set(AC.class_rule(key)) == {"label", "mu_hist_weight", "window_years", "class"}
    # audit_table 自己也在守这三条不变量
    errs, _ = AC.audit_table()
    assert errs == [], errs


def test_sigma_three_states():
    vol, note = AC.build_vol("D_HSBC_SP500", 0.18)
    assert abs(vol - 0.18) < 1e-12 and note is None
    vol, note = AC.build_vol("D_HSBC_SP500", 0.80)
    assert abs(vol - 0.60) < 1e-12 and note is None
    vol, note = AC.build_vol("D_HSBC_SP500", 1.80)
    assert vol is None and note == "sigma_glitch"


# ---------------------------------------------------------------- 仓库校准文件

def test_repo_calibration_carries_bounds():
    cal = _calibration()
    assert cal.get("bounds") == AC.bounds_payload(), cal.get("bounds")
    assert "2026-10-29" in cal.get("rules", ""), cal.get("rules")
    for key in ("NVDA", "TSLA"):
        prov = cal["provenance"][key]
        assert abs(cal["assets"][key]["mu"] - 0.30) < 1e-9, cal["assets"][key]
        assert prov.get("mu_clipped") is True, prov
        assert prov["mu_blend"] > 0.30, prov
    # 1364（JPX-Nikkei 400，broad）过去被 0% 地板截断，统一护栏后露出真实混合值
    prov = cal["provenance"]["1364"]
    assert cal["assets"]["1364"]["mu"] < 0.0, cal["assets"]["1364"]
    assert abs(prov["mu_blend"] - cal["assets"]["1364"]["mu"]) < 1e-4, prov
    assert prov.get("mu_clipped") is not True
    # 商品桶 σ 过去无上限（77%），现在截断到 60%
    prov = cal["provenance"]["D_CN_QDII_COMMODITY"]
    assert abs(cal["assets"]["D_CN_QDII_COMMODITY"]["vol"] - 0.60) < 1e-9
    assert prov.get("vol_clipped") is True and prov["vol_before_clip"] > 0.60, prov


# ---------------------------------------------------------------- 运行时重截断

def test_params_reclamps_old_calibration():
    """旧版（或无 bounds 的）校准文件：运行时按当前护栏重截断，并显式告警。"""
    saved_path = P.CALIBRATION_PATH
    saved_warnings = list(P.CALIBRATION_WARNINGS)
    saved_source = dict(P.PARAM_SOURCE)
    saved_prov = dict(P.CALIBRATION_PROVENANCE)
    tmpdir = tempfile.mkdtemp(prefix="pm-bounds-")
    tmp_json = os.path.join(tmpdir, "calibrated_params.json")
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": "2026-01-01T00:00:00+00:00",
            "bounds": {"version": 1, "mu_lo": -0.20, "mu_hi": 0.60,
                       "sigma_lo": 0.0, "sigma_hi": 1.50},
            "assets": {
                "NVDA": {"mu": 0.55, "vol": 0.90},
                "D_HSBC_SP500": {"mu": 0.10, "vol": 0.12},
            },
            "provenance": {},
        }, f)
    try:
        P.CALIBRATION_PATH = tmp_json
        P._apply_calibration()
        nvda = P.US_INTERNAL["NVDA"]
        assert abs(nvda["mu"] - 0.30) < 1e-12 and abs(nvda["vol"] - 0.60) < 1e-12
        sp500 = next(a for a in P.BROAD_ASSETS if a["key"] == "D_HSBC_SP500")
        assert abs(sp500["mu"] - 0.10) < 1e-12 and abs(sp500["vol"] - 0.12) < 1e-12
        assert any("旧参数护栏" in w for w in P.CALIBRATION_WARNINGS), P.CALIBRATION_WARNINGS
        assert P.CALIBRATION_SUMMARY["bounds"] == AC.bounds_payload()
        assert "NVDA" in P.CALIBRATION_SUMMARY["bounds_reclamped"]
    finally:
        P.CALIBRATION_PATH = saved_path
        P._apply_calibration()
        P.CALIBRATION_WARNINGS[:] = saved_warnings
        P.PARAM_SOURCE.clear()
        P.PARAM_SOURCE.update(saved_source)
        P.CALIBRATION_PROVENANCE = saved_prov
        try:
            os.remove(tmp_json)
            os.rmdir(tmpdir)
        except OSError:
            pass


def test_params_loads_repo_bounds_without_reclamp():
    """加载当前仓库校准时：bounds 版本一致、无需告警、summary 带快照。"""
    assert P.CALIBRATION_SUMMARY["bounds"]["version"] == AC.BOUNDS_VERSION
    assert P.CALIBRATION_SUMMARY["bounds_reclamped"] == [], P.CALIBRATION_SUMMARY
    assert not any("旧参数护栏" in w for w in P.CALIBRATION_WARNINGS), P.CALIBRATION_WARNINGS


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
