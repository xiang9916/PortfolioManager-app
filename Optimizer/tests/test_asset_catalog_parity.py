#!/usr/bin/env python3
"""跨语言资产目录契约测试（仓库 lint，不联网）。

两条事实来源必须一致：
  ① Swift Sources/PortfolioCore/DataSources/AssetCatalog.swift 的 modeled 层
     与 Python Optimizer/scripts/market_data.py 的 SOURCES 逐 key 相同（kind + symbol）；
  ② Python 自己的身份表（ASSET_CLASS / RF_PREMIUM / PRIOR / ...）只引用存在的 key，
     BROAD_ASSETS 的池词表只允许 domestic / overseas / unconstrained。

这不是祖先对照测试：它 lint 的是当前 checkout 的两份文件，与求解行为无关。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_asset_catalog_parity.py
"""

import os
import re
import sys
from pathlib import Path

_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)
_PKG_ROOT = os.path.dirname(os.path.abspath(_SCRIPTS))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import asset_classes as AC
import market_data as MD
import params as P

_REPO = Path(__file__).resolve().parents[2]
_SWIFT = _REPO / "Sources" / "PortfolioCore" / "DataSources" / "AssetCatalog.swift"
_REF_RE = re.compile(r'AssetRef\("([^"]+)",\s*\.(yahoo|fund),\s*"([^"]+)"')


def _catalog_tiers():
    text = _SWIFT.read_text(encoding="utf-8")
    modeled_start = text.index("static let modeled")
    legacy_start = text.index("static let legacy")
    refs_start = text.index("static let refs")
    modeled = {k: (kind, sym) for k, kind, sym in _REF_RE.findall(text[modeled_start:legacy_start])}
    legacy = {k: (kind, sym) for k, kind, sym in _REF_RE.findall(text[legacy_start:refs_start])}
    return modeled, legacy


def _diff(left, right):
    return {
        "swift_only": {k: left[k] for k in sorted(set(left) - set(right))},
        "python_only": {k: right[k] for k in sorted(set(right) - set(left))},
        "mismatch": {k: (left[k], right[k]) for k in sorted(set(left) & set(right))
                     if left[k] != right[k]},
    }


def test_swift_catalog_parse_covers_both_tiers():
    modeled, legacy = _catalog_tiers()
    # 解析数过少说明正则/文件结构变了，直接失败而不是静默通过。
    assert len(modeled) >= 40, len(modeled)
    assert len(legacy) >= 10, len(legacy)


def test_modeled_matches_python_sources():
    modeled, _ = _catalog_tiers()
    sources = {k: (kind, sym) for k, (kind, sym) in MD.SOURCES.items()}
    assert modeled == sources, _diff(modeled, sources)


def test_legacy_is_disjoint_and_unknown_to_python():
    modeled, legacy = _catalog_tiers()
    assert not (set(modeled) & set(legacy)), set(modeled) & set(legacy)
    assert not (set(legacy) & set(MD.SOURCES)), set(legacy) & set(MD.SOURCES)
    keys = list(modeled) + list(legacy)
    assert len(keys) == len(set(keys)), "目录 key 不得重复"


def test_python_identity_tables_reference_known_keys():
    sources = set(MD.SOURCES)
    internal = set(P.US_INTERNAL) | set(P.JP_INTERNAL) | set(P.GC_INTERNAL)
    assert set(AC.ASSET_CLASS) == sources, set(AC.ASSET_CLASS) ^ sources
    assert internal <= sources, internal - sources
    assert internal <= set(AC.ASSET_CLASS), internal - set(AC.ASSET_CLASS)
    assert set(MD.PROXY_MAP) <= sources, set(MD.PROXY_MAP) - sources
    for table_name in ("RF_PREMIUM", "PRIOR", "SPECIAL_FIXED", "CLASS_OVERRIDES",
                       "BOND_PRIOR_FORMULA", "CONSERVATIVE_CI"):
        table = getattr(AC, table_name)
        unknown = set(table) - set(AC.ASSET_CLASS)
        assert not unknown, (table_name, unknown)


def test_broad_assets_alignment_and_pool_vocabulary():
    assert {a["key"] for a in P.BROAD_ASSETS} <= set(MD.SOURCES)
    for a in P.BROAD_ASSETS:
        if a.get("fund_code"):
            assert MD.SOURCES[a["key"]][1] == a["fund_code"], a
    pools = {a.get("pool") for a in P.BROAD_ASSETS}
    assert pools <= {"domestic", "overseas", "unconstrained"}, pools
    assert "cross" not in pools


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
