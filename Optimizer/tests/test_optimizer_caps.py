#!/usr/bin/env python3
"""「个股/主动」类单标的上限（ACTIVE_CAP = 10%）的规则单测。

覆盖 2026-10-13 实测踩到的两个坑：
1. 上限表如果只从"大类槽位"构建，会漏掉美股内部标的（实测被突破过，没人拦截）；
2. SLSQP 未收敛时返回的迭代点可能违反 bounds，必须在结果校验里显式查上界。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_optimizer_caps.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import numpy as np

import asset_classes as AC
import optimize_portfolio as OP


def test_cap_map_covers_internal_tickers():
    caps = OP._active_cap_map([])
    # 美股内部：8 只个股；大中华：STOCK / MIXED
    for key in ("AAPL", "NVDA", "UNH", "GOOG", "MSFT", "AMZN", "META", "TSLA", "STOCK", "MIXED"):
        assert caps.get(key) == AC.ACTIVE_CAP, f"{key} 未进入上限表"
        assert AC.asset_class(key) == "active"


def test_cap_map_covers_broad_slots():
    caps = OP._active_cap_map([{"key": "O_US_ENERGY"}, {"key": "O_GOLD"}, {"key": "SPMO"}])
    assert caps.get("O_US_ENERGY") == AC.ACTIVE_CAP      # 名字像大类，实为个股
    assert "O_GOLD" not in caps and "SPMO" not in caps   # broad / strategy 不设上限


def test_cap_map_follows_test_class():
    active = OP._active_cap_map([{"key": "T_NFLX", "test_class": "active"}])
    strat = OP._active_cap_map([{"key": "T_QUAL", "test_class": "strategy"}])
    assert active.get("T_NFLX") == AC.ACTIVE_CAP
    assert "T_QUAL" not in strat


def test_apply_caps_sets_upper_bounds():
    assets = [{"key": "O_US_ENERGY", "pool": "overseas"}, {"key": "O_GOLD", "pool": "cross"},
              {"key": "D_X", "pool": "domestic"}]
    upper = np.ones(3)
    warnings = []
    OP.apply_active_caps(upper, assets, OP._active_cap_map(assets), [2], 0.30, warnings)
    assert abs(upper[0] - AC.ACTIVE_CAP) < 1e-12     # 个股被压到上限
    assert abs(upper[1] - 1.0) < 1e-12               # 黄金不受限
    # 境内池只有一只非 active 标的时不该被改动，也不该报警
    assert abs(upper[2] - 1.0) < 1e-12
    assert warnings == []


def test_apply_caps_relaxes_on_domestic_conflict():
    # 境内池只有一只 active 标的，却要求 30% 境内权重 → 上限必须放宽，否则不可行
    assets = [{"key": "T_110035", "pool": "domestic", "test_class": "active"}]
    upper = np.ones(1)
    warnings = []
    OP.apply_active_caps(upper, assets, OP._active_cap_map(assets), [0], 0.30, warnings)
    assert upper[0] >= 0.30 - 1e-9, upper
    assert warnings and "境内池" in warnings[0]


def test_upper_array_handles_none():
    arr = OP._upper_array([(0.0, 0.1), (0.0, None)])
    assert arr[0] == 0.1 and np.isinf(arr[1])


def test_solution_bound_check_rejects_overweight():
    """结果校验：超过 bounds 上界的解必须被拒绝（复现上限被突破那次）。"""
    bounds = [(0.0, AC.ACTIVE_CAP)] * 3
    bad = np.array([0.1358, 0.08, 0.08])
    good = np.array([0.10, 0.10, 0.10])
    assert np.any(bad > OP._upper_array(bounds) + 1e-6)
    assert not np.any(good > OP._upper_array(bounds) + 1e-6)


def test_cap_hits_merge_note():
    OP.CAP_HITS.clear()
    OP._note_cap_hit("AAPL", 0.10, AC.ACTIVE_CAP)
    OP._note_cap_hit("NVDA", 0.10, AC.ACTIVE_CAP)
    OP._note_cap_hit("AAPL", 0.10, AC.ACTIVE_CAP)   # 同一标的重复记录只保留一次
    merged = {}
    for k, w, cap in OP.CAP_HITS:
        merged[k] = max(merged.get(k, 0.0), w)
    assert set(merged) == {"AAPL", "NVDA"}
    OP.CAP_HITS.clear()


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
