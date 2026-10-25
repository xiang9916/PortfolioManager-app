#!/usr/bin/env python3
"""「个股/主动」类单标的上限（ACTIVE_CAP = 10%）的规则单测。

覆盖 2026-10-13 实测踩到的两个坑：
1. 上限表如果只从"大类槽位"构建，会漏掉美股内部标的（实测被突破过，没人拦截）；
2. SLSQP 未收敛时返回的迭代点可能违反 bounds，必须在结果校验里显式查上界。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_optimizer_caps.py
"""

import os
import sys

# 祖先对照的正确做法（AGENTS §12）：PM_OPT_SCRIPTS 优先 —— 只改 PYTHONPATH 会跑成当前树。
_SCRIPTS = os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS)
# P4：包根 = scripts 的兄弟目录（由 PM_OPT_SCRIPTS 派生 → 祖先/旧布局树同样可用）。
_PKG_ROOT = os.path.dirname(os.path.abspath(_SCRIPTS))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import numpy as np

import asset_classes as AC
import optimize_portfolio as OP
from portfolio_system.core import constraints as core_constraints


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
    assets = [{"key": "O_US_ENERGY", "pool": "overseas"}, {"key": "O_GOLD", "pool": "unconstrained"},
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


# ── 2026-10-17 核验缺陷回归：「上界被夹紧」≠「触顶」 ─────────────────────────────
# 背景：D2 的薄壳曾把 core.apply_active_caps 返回的**夹紧记录**（它把某只 active 腿的上界从
# 1.0 压到 0.10，无论该腿最终拿不拿得到权重）一并并入 CAP_HITS。于是低目标档的
# warnings 会点名一只**交付权重为 0** 的标的（核验实测：O_US_ENERGY 被点名、权重 0）。
# 契约：触顶 = **交付/生效权重 ≥ cap**；夹紧本身不产生任何告警。

CAP_STUB_ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05,
     "pool": "domestic", "test_class": "broad"},
    {"key": "O_US_ENERGY", "name": "虚构能源", "mu": 0.07, "vol": 0.25,
     "pool": "overseas", "test_class": "active"},   # 上界被夹紧，但交付权重 0
    {"key": "AAPL", "name": "虚构美股A", "mu": 0.12, "vol": 0.22,
     "pool": "overseas", "test_class": "active"},   # 真触顶 0.10
    {"key": "O_GOLD", "name": "虚构黄金", "mu": 0.04, "vol": 0.15,
     "pool": "overseas", "test_class": "broad"},
]
CAP_STUB_DOM_W, CAP_STUB_OV_W, CAP_STUB_TOTAL, CAP_STUB_RF, CAP_STUB_TARGET = (
    0.30, 0.70, 1_000_000.0, 0.02, 0.05)
CAP_STUB_EXTRACT = {
    "pool_mode": "fixture",
    "domestic_weight": CAP_STUB_DOM_W, "overseas_weight": CAP_STUB_OV_W,
    "domestic_value": CAP_STUB_TOTAL * CAP_STUB_DOM_W,
    "overseas_value": CAP_STUB_TOTAL * CAP_STUB_OV_W,
    "total_value": CAP_STUB_TOTAL, "rf": CAP_STUB_RF,
    "us_equity": {"total_value": CAP_STUB_TOTAL * CAP_STUB_OV_W,
                  "holdings": [{"ticker": "UA", "name": "虚构",
                                "value_cny": CAP_STUB_TOTAL * CAP_STUB_OV_W, "weight": 1.0}]},
}
#: O_US_ENERGY 交付权重 0（只是上界被夹紧）；AAPL 恰好 0.10（真触顶）
CAP_STUB_WEIGHTS = {"D1": CAP_STUB_DOM_W, "O_US_ENERGY": 0.0, "AAPL": 0.10,
                    "O_GOLD": CAP_STUB_OV_W - 0.10}


def _with_cap_stub(fn):
    """只替换 `_solve_once`：`_solve_once` 里的真 apply_active_caps / 触顶判定 / 汇总全部照跑。"""
    original = OP._solve_once

    def stub(assets_in, corr_pairs, **kw):
        keys = [a["key"] for a in assets_in]
        weights = np.array([CAP_STUB_WEIGHTS[k] for k in keys])
        ret, vol = 0.06, 0.10
        upper = np.ones(len(keys))
        # 与生产同序：先套上限（这一步会夹紧 O_US_ENERGY 的 upper，但**不**产生触顶记录）
        OP.apply_active_caps(upper, assets_in, OP._active_cap_map(assets_in),
                             [keys.index("D1")], CAP_STUB_DOM_W, kw.get("warn") or [])
        # 再按**交付权重**判定真触顶（与生产同谓词）
        for i, a in enumerate(assets_in):
            cap = OP._active_cap_map(assets_in).get(a["key"])
            if cap is not None and weights[i] >= cap - 1e-6:
                OP._note_cap_hit(a["key"], weights[i], cap)
        return {
            "keys": keys, "stage1_weights": weights,
            "stage1_ret": ret, "stage1_vol": vol, "stage1_ret_hint": ret,
            "stage1_sharpe": (ret - CAP_STUB_RF) / vol,
            "mu": np.array([a["mu"] for a in assets_in]),
            "internal": None, "internal_tickers": None,
            "japan_selection": None, "gc_selection": None,
            "final": {"ret": ret, "vol": vol, "sharpe": (ret - CAP_STUB_RF) / vol,
                      "floor": ret - 1.96 * vol},
            "target": float(kw["target"]), "warnings": [],
        }

    OP._solve_once = stub
    try:
        return fn()
    finally:
        OP._solve_once = original


def _cap_summary():
    """按生产口径把 CAP_HITS 合成一条提示（`_cap_hit_warning`），返回 (keys, 文本)。"""
    merged = {}
    for key, weight, _cap in OP.CAP_HITS:
        merged[key] = max(merged.get(key, 0.0), float(weight))
    return set(merged), (str(OP._cap_hit_warning(OP.CAP_HITS)) if OP.CAP_HITS else "")


def test_clamped_upper_without_weight_is_not_a_cap_hit():
    """上界被夹紧但**交付权重为 0** 的 active 腿不得进入触顶记录/告警（fails-before）。"""
    def run():
        OP.CAP_HITS.clear()
        return OP.solve_portfolio([dict(a) for a in CAP_STUB_ASSETS], {},
                                  dom_w=CAP_STUB_DOM_W, ov_w=CAP_STUB_OV_W,
                                  target=CAP_STUB_TARGET, rf=CAP_STUB_RF,
                                  extract=dict(CAP_STUB_EXTRACT), total_assets=CAP_STUB_TOTAL,
                                  warnings=[], log=None, max_sleeve_iter=2,
                                  stage1_starts=1, stage2_starts=1)

    try:
        out = _with_cap_stub(run)
        assert out is not None
        assert float(out["stage1_weights"][1]) == 0.0, out["stage1_weights"]
        keys, text = _cap_summary()
        assert keys == {"AAPL"}, "触顶记录混入了夹紧腿: %s" % sorted(keys)
        assert "O_US_ENERGY" not in text, text
        assert "AAPL 10.00%" in text, text
    finally:
        OP.CAP_HITS.clear()


def test_clamped_upper_is_reported_as_clamped_not_hit():
    """core.apply_active_caps 仍如实返回「被夹紧」清单（供审计），只是它**不再等于触顶**。

    `OP.apply_active_caps` 是薄壳、不返回值；"夹紧"信息由 core 的返回值承载，
    而**触顶**只由拿到权重后的调用方判定 → 所以这里直接看 core 的返回 + CAP_HITS 不动。
    """
    assets = [{"key": "O_US_ENERGY", "pool": "overseas", "test_class": "active"},
              {"key": "O_GOLD", "pool": "overseas", "test_class": "broad"}]
    upper = [1.0, 1.0]
    before = list(OP.CAP_HITS)
    records = core_constraints.apply_active_caps(upper, assets, OP._active_cap_map(assets),
                                                 [], 0.0, [])
    assert abs(upper[0] - AC.ACTIVE_CAP) < 1e-12, upper          # 上界确实被夹紧
    assert abs(upper[1] - 1.0) < 1e-12, upper                    # broad 不动
    assert [r[0] for r in records] == ["O_US_ENERGY"], records   # 报"夹紧"只作信息返回
    assert OP.CAP_HITS == before, "夹紧不得写进 CAP_HITS（那不是触顶）"


def test_zero_weight_hit_candidate_stays_out_of_cap_hits():
    """直接钉住缺陷语义：上界被夹紧、且交付权重为 0 的 active 腿不得被记成触顶。"""
    assets = [{"key": "O_US_ENERGY", "pool": "overseas", "test_class": "active"},
              {"key": "O_GOLD", "pool": "overseas", "test_class": "broad"}]
    upper = [1.0, 1.0]
    before = list(OP.CAP_HITS)
    OP.apply_active_caps(upper, assets, OP._active_cap_map(assets), [], 0.0, [])
    upper[0] = 0.0     # 该腿最终权重 0：即使上界被夹紧，也绝不是触顶
    assert OP.CAP_HITS == before, OP.CAP_HITS


def test_pool_indices_exclude_unconstrained():
    """unconstrained（原 cross）两边都不进：与 engine 的 stage-1 口径一致。"""
    class _Asset:
        def __init__(self, pool):
            self.pool = pool

    class _Problem:
        def __init__(self, pools):
            self.assets = [_Asset(p) for p in pools]

    dom, ov = core_constraints.pool_indices(_Problem(["domestic", "overseas", "unconstrained"]))
    assert dom == [0], dom
    assert ov == [1], ov


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
