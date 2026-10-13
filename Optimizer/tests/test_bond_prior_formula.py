#!/usr/bin/env python3
"""债券动态先验公式单测（不联网）。

公式（用户 2026-10-13 定稿）：先验 =（回归 μ + 当期锚定收益率）/2，
回归 μ = a×当期 **10 年期**国债收益率 + b（自变量固定 10 年期），
锚定期限与久期匹配：CDB 用中国 5 年期、TLT 用美国 20 年期（10Y/30Y 插值合成）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import asset_classes as AC

ANCHORS = {"cn_10y": 0.016654, "cn_5y": 0.013974, "us_10y": 0.0524, "us_30y": 0.0560}
FITS = {
    "D_HSBC_CDB": {"a": 0.8534810, "b": 0.0086641, "r2": 0.5435, "n_years": 3, "source": "007485"},
    "O_US_TLT":   {"a": 3.3636809, "b": -0.0853867, "r2": 0.5298, "n_years": 15, "source": "TLH"},
}


def test_cdb_formula_matches_hand_calc():
    prior, d = AC.bond_prior("D_HSBC_CDB", ANCHORS, FITS)
    reg_mu = 0.8534810 * ANCHORS["cn_10y"] + 0.0086641        # 回归自变量是 10 年期
    assert abs(d["reg_mu"] - reg_mu) < 1e-12
    assert abs(prior - (reg_mu + ANCHORS["cn_5y"]) / 2) < 1e-12
    assert d["anchor_name"] == "cn_5y" and d["reg_yield_name"] == "cn_10y"


def test_tlt_uses_synthesised_20y():
    prior, d = AC.bond_prior("O_US_TLT", ANCHORS, FITS)
    synth = (ANCHORS["us_10y"] + ANCHORS["us_30y"]) / 2
    assert abs(d["anchor_value"] - synth) < 1e-12
    assert "插值" in d["anchor_label"]
    assert abs(prior - (3.3636809 * ANCHORS["us_10y"] - 0.0853867 + synth) / 2) < 1e-12


def test_missing_inputs_fall_back():
    assert AC.bond_prior("D_HSBC_CDB", {}, FITS)[1]["reason"] == "missing_anchor"
    assert AC.bond_prior("D_HSBC_CDB", ANCHORS, {})[1]["reason"] == "missing_fit"
    assert AC.bond_prior("SPLG", ANCHORS, FITS)[1]["reason"] == "not_formula_key"


def test_override_plumbing_reaches_build_mu():
    """注入的动态先验必须真的进入 μ 的混合（而不是被静态表盖掉）。"""
    static_prior = AC.PRIOR["D_HSBC_CDB"]
    AC.set_prior_overrides({})
    try:
        mu_static, _ = AC.build_mu("D_HSBC_CDB", 0.0337)
        prior, _ = AC.bond_prior("D_HSBC_CDB", ANCHORS, FITS)
        AC.set_prior_overrides({"D_HSBC_CDB": prior})
        mu_dyn, _ = AC.build_mu("D_HSBC_CDB", 0.0337)
        assert AC.prior_for("D_HSBC_CDB") == prior
        assert abs(mu_static - (0.5 * 0.0337 + 0.5 * static_prior)) < 1e-9
        assert abs(mu_dyn - (0.5 * 0.0337 + 0.5 * prior)) < 1e-9
        assert mu_dyn < mu_static            # 动态先验(1.84%) < 静态(3.5%) → μ 更低
    finally:
        AC.set_prior_overrides({})


def test_formula_keys_are_registered_and_have_fallback():
    for key, spec in AC.BOND_PRIOR_FORMULA.items():
        assert key in AC.ASSET_CLASS, key
        assert key in AC.PRIOR, f"{key} 缺静态兜底值"
        assert spec["fallback"] == AC.PRIOR[key], f"{key} 兜底值与 PRIOR 不一致"
        assert spec["reg_yield"] in AC.ANCHOR_SPECS
        assert spec["anchor"] in AC.ANCHOR_SPECS


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
