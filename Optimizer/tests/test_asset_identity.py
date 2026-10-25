#!/usr/bin/env python3
"""资产身份规范化（Python 侧）：与 Swift 的 AssetIdentity 同一张规则矩阵。

覆盖：后缀大小写 / .SS 例外 / 裸 6 位 / 去重；并验证 held_assets 与 test_tickers
都委托给同一个实现（不再各留一份正则）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_asset_identity.py
"""

import os
import sys

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)
_PKG_ROOT = os.path.dirname(os.path.abspath(_SCRIPTS))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import asset_identity as AI
import held_assets as HA
import test_tickers as TT


def test_canonical_trims_and_uppercases():
    assert AI.canonical_ticker(" test ") == "TEST"
    assert AI.canonical_ticker(None) == ""
    assert AI.canonical_ticker("   ") == ""


def test_fund_code_accepts_case_insensitive_suffix():
    for raw in ("123456", "123456.CN_Fund", "123456.CN_FUND", "123456.cn_fund",
                " 123456.sz ", "123456.SH"):
        assert AI.fund_code(raw) == "123456", raw
        assert AI.canonical_ticker(raw) == "123456", raw


def test_fund_code_rejects_a_share_and_non_six_digit():
    assert AI.fund_code("654321.SS") is None
    assert AI.canonical_ticker("654321.SS") == "654321.SS"
    assert AI.fund_code("ABCD") is None
    assert AI.fund_code("ABCD.SZ") is None
    assert AI.canonical_ticker("ABCD.SZ") == "ABCD.SZ"


def test_classify_source():
    assert AI.classify_source("123456.CN_FUND") == ("fund", "123456")
    assert AI.classify_source("abcd") == ("yahoo", "ABCD")
    assert AI.classify_source("654321.SS") == ("yahoo", "654321.SS")


def test_held_harmonizer_matches_canonical():
    for raw in ("123456.CN_Fund", "123456.CN_FUND", "123456.SZ", " 123456 "):
        assert HA.harmonize_held_ticker(raw) == "123456", raw
    assert HA.harmonize_held_ticker("qqq") == "QQQ"


def test_parse_test_tickers_canonicalizes_and_dedupes():
    assert TT.parse_test_tickers("123456.CN_FUND, 123456, abcd，ABCD") == ["123456", "ABCD"]


def test_test_ticker_source_classification_matches_identity():
    assert TT.classify_source("123456.CN_FUND") == ("fund", "123456")
    assert TT.classify_source("abcd") == ("yahoo", "ABCD")


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
