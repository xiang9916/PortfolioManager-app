#!/usr/bin/env python3
"""asset_classes.py 的规则单测（纯函数，不联网）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_asset_classes.py
（venv 里没有 pytest，所以写成自带 runner 的脚本；函数名保持 test_* 以便将来接 pytest。）
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import asset_classes as AC


# ---------------------------------------------------------------- 类别表自检

def test_audit_table_clean():
    errs, stats = AC.audit_table()
    assert errs == [], f"类别表自检失败: {errs}"
    assert stats["assets"] == len(AC.ASSET_CLASS)
    assert set(stats["by_class"]) <= {"broad", "strategy", "active"}


def test_no_vol_floor_table():
    """σ 的人工下限表已整体退役（2026-10-14 用户定稿）。"""
    assert not hasattr(AC, "VOL_FLOOR"), "不应再有波动率下限表"
    for cls, rule in AC.CLASS_RULES.items():
        assert "use_vol_floor" not in rule, cls


def test_active_has_no_prior():
    for key, cls in AC.ASSET_CLASS.items():
        if cls == "active":
            assert key not in AC.PRIOR, f"{key} 是 active，不该有先验"


def test_broad_and_strategy_have_prior():
    for key, cls in AC.ASSET_CLASS.items():
        if cls in ("broad", "strategy") and key not in AC.SPECIAL_FIXED:
            assert key in AC.PRIOR, f"{key} 是 {cls}，应有先验"


# ---------------------------------------------------------------- μ 构造

def test_broad_blend_and_clamp():
    # D_HSBC_SP500: 0.5×20% + 0.5×9% = 14.5%
    mu, note = AC.build_mu("D_HSBC_SP500", 0.20)
    assert abs(mu - 0.145) < 1e-9 and note is None
    # 上限 20%：0.5×60% + 0.5×9% = 34.5% → 20%
    mu, _ = AC.build_mu("D_HSBC_SP500", 0.60)
    assert abs(mu - 0.20) < 1e-9
    # 下限 0%：0.5×(−30%) + 0.5×9% = −10.5% → 0%
    mu, _ = AC.build_mu("D_HSBC_SP500", -0.30)
    assert abs(mu - 0.0) < 1e-9


def test_btc_has_wider_clamp():
    mu, _ = AC.build_mu("O_BTC", 1.20)   # 0.5×120% + 0.5×15% = 67.5% → 60%
    assert abs(mu - 0.60) < 1e-9, mu
    mu, _ = AC.build_mu("O_BTC", -1.00)  # 0.5×(−100%) + 0.5×15% = −42.5% → −20%
    assert abs(mu + 0.20) < 1e-9, mu


def test_strategy_blend_and_clamp():
    # SPMO: 2/3×20% + 1/3×9.5% = 16.5%
    mu, note = AC.build_mu("SPMO", 0.20)
    assert abs(mu - (2 / 3 * 0.20 + 1 / 3 * 0.095)) < 1e-9 and note is None
    mu, _ = AC.build_mu("SPMO", 1.00)    # 上限 30%
    assert abs(mu - 0.30) < 1e-9
    mu, _ = AC.build_mu("SPMO", -0.60)   # 下限 −5%
    assert abs(mu + 0.05) < 1e-9


def test_active_is_pure_history_no_prior():
    mu, note = AC.build_mu("NVDA", 0.55)
    assert abs(mu - 0.40) < 1e-9 and note is None      # 上限 40%
    mu, note = AC.build_mu("NVDA", 0.123)
    assert abs(mu - 0.123) < 1e-9 and note is None      # 不被先验稀释
    mu, note = AC.build_mu("NVDA", -0.45)
    assert abs(mu + 0.10) < 1e-9                        # 下限 −10%


def test_missing_history_fallback_matrix():
    # broad + 有写死 + 无行情 → 用先验并报警
    mu, note = AC.build_mu("D_HSBC_SP500", None)
    assert abs(mu - 0.09) < 1e-9 and note == "prior_fallback"
    # active + 无行情 → 不能兜底（丢弃）
    mu, note = AC.build_mu("NVDA", None)
    assert mu is None and note == "no_data"
    vol, vnote = AC.build_vol("NVDA", None)
    assert vol is None and vnote == "no_data"
    # broad + 无行情 → 构造不出 σ（没有人工下限，不编造）→ 调用方丢弃
    vol, vnote = AC.build_vol("D_HSBC_SP500", None)
    assert vol is None and vnote == "no_data"


def test_history_only_when_no_prior():
    # 模拟"新发现的 broad 标的但没有登记先验"：把先验临时摘掉
    saved = AC.PRIOR.pop("O_SG_EQ")
    try:
        mu, note = AC.build_mu("O_SG_EQ", 0.05)
        assert abs(mu - 0.05) < 1e-9 and note == "history_only"
    finally:
        AC.PRIOR["O_SG_EQ"] = saved


# ---------------------------------------------------------------- σ 构造

def test_vol_is_pure_history_for_every_class():
    # 历史波动多低就是多低：DIVLOWVOL 曾有人工下限 14%，现在 10% 就取 10%
    vol, note = AC.build_vol("DIVLOWVOL", 0.10)
    assert abs(vol - 0.10) < 1e-9 and note is None
    vol, note = AC.build_vol("DIVLOWVOL", 0.25)
    assert abs(vol - 0.25) < 1e-9 and note is None
    # 债券同理（国开债曾有人工下限 2%）
    vol, note = AC.build_vol("D_HSBC_CDB", 0.0135)
    assert abs(vol - 0.0135) < 1e-9 and note is None


def test_no_sigma_upper_cap():
    # 用户要求去掉 0.6 上限：历史 80% 就保留 80%
    vol, note = AC.build_vol("D_CN_QDII_COMMODITY", 0.80)
    assert abs(vol - 0.80) < 1e-9 and note is None


def test_low_vol_asset_keeps_its_own_sigma():
    vol, note = AC.build_vol("GOOG", 0.05)
    assert abs(vol - 0.05) < 1e-9 and note is None
    # 曾经被 15% 下限抬起来的宽基，现在按真实历史波动
    vol, note = AC.build_vol("D_HSBC_SP500", 0.10)
    assert abs(vol - 0.10) < 1e-9 and note is None


def test_sigma_glitch_dropped():
    vol, note = AC.build_vol("O_BTC", 1.80)
    assert vol is None and note == "sigma_glitch"
    vol, note = AC.build_vol("GOOG", 2.00)
    assert vol is None and note == "sigma_glitch"


def test_fixed_instruments():
    assert AC.build_mu("D_HSBC_MMF", 0.5) == (0.02, "fixed")
    assert AC.build_vol("D_HSBC_MMF", 0.5) == (0.01, "fixed")


# ---------------------------------------------------------------- 窗口与说明

def test_slice_window_takes_tail():
    import datetime
    today = datetime.date(2026, 1, 1)
    prices = [(today - datetime.timedelta(days=365 * i), float(i)) for i in range(30, 0, -1)]
    out = AC.slice_window(prices, 5)
    assert out and out[-1] == prices[-1]
    assert (out[-1][0] - out[0][0]).days <= int(5 * 365.25) + 1
    # 窗口比历史还长 → 原样返回
    assert AC.slice_window(prices, 99) == prices
    # 全历史类别 → 原样返回
    assert AC.slice_window(prices, None) == prices


def test_window_years_by_class():
    assert AC.window_years("SPY") is None       # broad 全历史
    assert AC.window_years("SPMO") == 20       # strategy 20 年
    assert AC.window_years("NVDA") == 10       # active 10 年


def test_describe_and_help_cover_three_classes():
    for cls in ("broad", "strategy", "active"):
        text = AC.describe_class(cls)
        assert text and "μ" in text and "σ" in text
    for token in ("broad", "strategy", "active", "默认 active"):
        assert token in AC.CLASS_PARAM_HELP, token


def test_unregistered_key_is_flagged():
    mu, note = AC.build_mu("不存在的标的", 0.1)
    assert mu is None and note == "unregistered"


# ---------------------------------------------------------------- RF + 溢价（2026-10-13）

def test_rf_is_not_hardcoded():
    """本币无风险利率必须来自数据源：程序里不写死利率常量。"""
    assert not hasattr(AC, "RF_BY_CCY"), "不应再有写死的 RF 表"
    import inspect
    src = inspect.getsource(AC)
    # 允许出现"0.5% 粒度"这类注释数字，但不允许把利率塞进 _RF 的初始值
    assert AC._RF == {} or "set_rf" in src
    AC.reset_rf()
    assert AC.rf_for("usd") is None and AC.prior_for("O_US_CORE") == AC.PRIOR["O_US_CORE"]


def test_prior_follows_rf():
    """先验 = 本币 RF + 溢价；RF 变了先验必须跟着变（这才是"动态"）。"""
    try:
        AC.set_rf({"usd": 0.0524, "cny": 0.0167})
        a = AC.prior_for("O_US_CORE")
        AC.set_rf({"usd": 0.0624, "cny": 0.0167})
        b = AC.prior_for("O_US_CORE")
        assert abs((b - a) - 0.01) < 1e-9, (a, b)
        # 币种隔离：改美元利率不影响人民币标的
        c = AC.prior_for("CSI300")
        AC.set_rf({"usd": 0.0724, "cny": 0.0167})
        assert abs(AC.prior_for("CSI300") - c) < 1e-12
        AC.set_rf({"cny": 0.0267})
        assert abs(AC.prior_for("CSI300") - (c + 0.01)) < 1e-9
    finally:
        AC.reset_rf()


def test_premium_and_fallback_are_half_point():
    """写死的数字（溢价与兜底先验）必须是 0.5% 粒度。"""
    for key, (prem, ccy) in AC.RF_PREMIUM.items():
        assert ccy in ("usd", "cny"), key
        assert abs(round(prem / 0.005) * 0.005 - prem) < 1e-9, (key, prem)
    for key, v in AC.PRIOR.items():
        assert abs(round(v / 0.005) * 0.005 - v) < 1e-9, (key, v)


def test_proxy_and_internal_pool_cleanup():
    """HK 内部池（父大类已不存在）必须已清除；短样本标的要有长历史代理。"""
    for key in ("2800", "2828", "3031", "3067", "3110", "3070"):
        assert key not in AC.ASSET_CLASS, key
    import market_data as M
    assert M.PROXY_MAP.get("HK_DIV") is not None      # 香港高股息桶的真实标的历史仅 129 天
    assert M.PROXY_MAP.get("DIVLOWVOL100") is not None  # 境内红利低波 100 桶仅 532 天
    # 大中华权益一级大类没有自己的行情 → 用恒生指数长历史作代表（2026-10-14）
    assert M.SOURCES.get("D_GREATER_CN") == ("yahoo", "^HSI")


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            if hasattr(AC, "reset_rf"):
                AC.reset_rf()          # 每个用例从"无 RF 注入"的干净状态开始
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
