#!/usr/bin/env python3
"""缓冲期权篮子的离线单测（ADR 0006）：纯虚构行情、不联网、不读真实持仓。

覆盖：
  ① 共同窗口 = 四只都存续之后；
  ② 每季度初再平衡（拉回各 25%）；
  ③ 单日缺口取交集；
  ④ 任一成员整段缺失 -> 整条篮子不可用；
  ⑤ 四只 ticker 归并到 BUFFER（us_core_params）；
  ⑥ 名称、单名先验与 S&P500 相关表口径。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_buffer_basket.py
祖先对照：把本文件拷进 git archive HEAD 树，PM_OPT_SCRIPTS=<祖先>/Optimizer/scripts
运行 -> 必红（祖先没有 build_buffer_basket / BUFFER_FAMILY）。
"""

import datetime
import os
import sys

_SCRIPTS = os.path.abspath(os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
_PKG_ROOT = os.path.dirname(_SCRIPTS)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import asset_classes as AC  # noqa: E402
import held_assets as HA  # noqa: E402
import market_data as M  # noqa: E402
import params as P  # noqa: E402
from portfolio_system.core import stage2  # noqa: E402

START = datetime.date(2025, 1, 2)
APR1 = datetime.date(2025, 4, 1)
APR2 = datetime.date(2025, 4, 2)


def _weekdays(start, n):
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += datetime.timedelta(days=1)
    return out


def _flat(days, price=100.0):
    return [(d, price) for d in days]


def _rising_then_drop(days):
    """1/2 起涨到 4/1 的 200，4/2 跌回 100；用于验证季度再平衡。"""
    out = []
    for d in days:
        if d < APR1:
            out.append((d, 100.0 + 100.0 * (d - START).days / (APR1 - START).days))
        elif d == APR1:
            out.append((d, 200.0))
        else:
            out.append((d, 100.0))
    return out


def _data(days):
    return {
        "MMAX": _rising_then_drop(days),
        "MAXJ": _flat(days),
        "SMAX": _flat(days),
        "DMAX": _flat(days),
    }


def _build(data, members=None, fetch=None):
    members = members or AC.BUFFER_FAMILY
    return M.build_buffer_basket(members, fetch or (lambda s: data[s]))


# ---------------------------------------------------------------- 窗口与再平衡

def test_common_window_starts_when_all_members_exist():
    """四只不同起始日 -> 序列从最晚那只的首日开始，不向前拼接。"""
    days = _weekdays(START, 90)
    late = _weekdays(datetime.date(2025, 2, 3), 70)
    early = [(d, 100.0) for d in days if d < late[0]]
    long = early + _flat(late)
    data = {
        "MMAX": _flat(late),
        "MAXJ": long,
        "SMAX": long,
        "DMAX": long,
    }
    series, meta = _build(data)
    assert series is not None, meta
    assert series[0][0] == late[0], (series[0], late[0])
    assert meta["window"] == [late[0].isoformat(), late[-1].isoformat()], meta
    assert meta["n_days"] == len(late), meta
    assert abs(series[0][1] - 1.0) < 1e-12, series[0]


def test_quarterly_rebalance_resets_to_equal_weight():
    """A 在 Q1 翻倍；若不在 4/1 拉回 25%，4/2 的跌幅会被放大。"""
    days = _weekdays(START, 130)
    series, meta = _build(_data(days))
    assert series is not None, meta
    levels = dict(series)
    assert abs(levels[APR1] - 1.25) < 1e-9, levels.get(APR1)
    # 再平衡后 A 只占 25%：1.25 里 A 从 200 跌到 100，只损失 0.15625
    assert abs(levels[APR2] - 1.09375) < 1e-9, levels.get(APR2)
    assert meta["rebalance"] == "quarterly", meta
    assert meta["rebalance_days"][:2] == ["2025-04-01", "2025-07-01"], meta


def test_single_day_gap_uses_intersection():
    """某一天只有一只缺收盘价 -> 四只一起跳过这一天（交集）。"""
    days = _weekdays(START, 130)
    data = _data(days)
    data["MAXJ"] = [(d, v) for d, v in data["MAXJ"] if d != APR1]
    series, meta = _build(data)
    assert series is not None, meta
    dates = [d for d, _ in series]
    assert APR1 not in dates, "缺口日应被交集剔除"
    assert APR2 in dates, "其余日期照常"
    assert meta["n_days"] == len(days) - 1, meta
    assert meta["rebalance_days"][0] == "2025-04-02", meta


# ---------------------------------------------------------------- 缺失与兜底

def test_missing_member_drops_whole_basket():
    """任一成员整段缺失 -> 不拿 3/4 顶替，整条篮子不可用。"""
    days = _weekdays(START, 130)
    data = _data(days)
    data["DMAX"] = []
    series, meta = _build(data)
    assert series is None, series
    assert meta["reason"] == "missing_member", meta
    assert meta["missing"] == ["DMAX"], meta


def test_fetch_error_is_reported_as_missing():
    days = _weekdays(START, 130)
    data = _data(days)

    def fetch(symbol):
        if symbol == "SMAX":
            raise RuntimeError("fixture")
        return data[symbol]

    series, meta = _build(data, fetch=fetch)
    assert series is None, series
    assert meta["reason"] == "missing_member", meta
    assert meta["missing"] == ["SMAX"], meta
    assert "fixture" in meta["errors"]["SMAX"], meta


def test_short_common_window_is_unusable():
    days = _weekdays(START, 10)
    series, meta = _build(_data(days))
    assert series is None, series
    assert meta["reason"] == "short_window", meta
    assert meta["n_days"] == 10, meta


# ---------------------------------------------------------------- 成员映射与契约

def test_members_fold_to_buffer_key():
    assert P.fold_internal_weights({"SMAX": 0.2, "MAXJ": 0.3, "SPY": 0.5}) == {
        "BUFFER": 0.5, "SPY": 0.5}, "四只成员必须先归并到同一个 BUFFER key"


def test_us_core_params_folds_buffer_members():
    saved = (P.US_INTERNAL, P.US_INTERNAL_CORR)
    P.US_INTERNAL = {
        "BUFFER": {"name": "fixture", "mu": 0.08, "vol": 0.03},
        "SPY": {"name": "fixture", "mu": 0.06, "vol": 0.06},
    }
    P.US_INTERNAL_CORR = {("BUFFER", "SPY"): 1.0}
    try:
        mu, vol = P.us_core_params({"SMAX": 0.5, "SPY": 0.5})
    finally:
        P.US_INTERNAL, P.US_INTERNAL_CORR = saved
    assert abs(mu - 0.07) < 1e-12, mu
    assert abs(vol - 0.045) < 1e-12, vol


def test_name_and_correlation_contract():
    assert P.US_INTERNAL["BUFFER"]["name"] == "缓冲期权（DMAX/MMAX/MAXJ/SMAX 等权）"
    # 单名先验必须在校准 JSON 整表重建之后仍存活（UNH 实测 0.08 是 2025 年个股事件）
    assert P.US_INTERNAL_CORR[("BUFFER", "UNH")] == 0.50, P.US_INTERNAL_CORR.get(("BUFFER", "UNH"))
    assert P.US_INTERNAL_CORR[("BUFFER", "NVDA")] == 0.45, P.US_INTERNAL_CORR.get(("BUFFER", "NVDA"))
    assert P._BUFFER_SINGLE_NAME_PRIORS[("BUFFER", "TSLA")] == 0.40
    assert stage2.SP500_INTERNAL_CORR["BUFFER"] == 0.93
    assert P.US_INTERNAL_CORR[("BUFFER", "SPY")] > 0.90, P.US_INTERNAL_CORR.get(("BUFFER", "SPY"))


def test_held_assets_models_every_buffer_member():
    modeled, unmodeled = HA.classify_held_assets(
        {"held_assets": [{"ticker": sym} for sym in AC.BUFFER_FAMILY]}, [], {})
    assert [e["ticker"] for e in modeled] == list(AC.BUFFER_FAMILY), modeled
    assert unmodeled == [], unmodeled


# ---------------------------------------------------------------- runner

def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  OK " + name)
        except AssertionError as exc:
            failed += 1
            print("  FAIL " + name + ": " + str(exc)[:800])
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc)[:800])
    print("\n%d/%d 通过" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
