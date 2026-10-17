#!/usr/bin/env python3
"""market_data 的东财日期口径单测（纯虚构、离线、不联网）。

覆盖 2026-10-17 修掉的口径 bug：东财 pingzhongdata 的 `x` 是**北京时间 00:00**
的毫秒时间戳，旧实现 `datetime.fromtimestamp(ts/1000, timezone.utc).date()`
按 UTC 取日 → 境内基金日期整体早一天，和 Yahoo 序列配对时相关性被系统性带偏。
现统一走 `market_data._eastmoney_date`（按 UTC+8 取日）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_market_data_dates.py
（venv 里没有 pytest，所以写成自带 runner 的脚本；函数名保持 test_* 以便将来接 pytest。）
"""

import datetime
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import market_data as M

_UTC = datetime.timezone.utc


def _beijing_ms(day, hour=0, minute=0, second=0):
    """北京时间某日某刻 → 东财风格毫秒时间戳（构造虚构时间戳用）。"""
    dt = datetime.datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=M._CN_TZ)
    return int(dt.timestamp() * 1000)


def _old_utc_date(ts_ms):
    """旧实现（按 UTC 取日），只用于在测试里演示错位。"""
    return datetime.datetime.fromtimestamp(ts_ms / 1000, _UTC).date()


# ---------------------------------------------------------------- 常量与时区

def test_cn_tz_is_utc_plus_8():
    assert isinstance(M._CN_TZ, datetime.timezone)
    assert M._CN_TZ.utcoffset(None) == datetime.timedelta(hours=8)


# ---------------------------------------------------------------- 固定毫秒

def test_known_timestamps_beijing_dates():
    """东财 `x` = 北京时间 00:00；跨年/跨月/首个真实样点都必须取北京日期。"""
    cases = [
        # 首个真实样点：UTC 2009-08-25（北京 08-26）—— 旧码会早一天
        (1251216000000, datetime.date(2009, 8, 26)),
        # 跨年：北京 2024-01-01 00:00 = UTC 2023-12-31 16:00
        (1704038400000, datetime.date(2024, 1, 1)),
        # 跨月：北京 2024-03-01 00:00 = UTC 2024-02-29 16:00（顺带压到闰日）
        (1709222400000, datetime.date(2024, 3, 1)),
    ]
    for ts_ms, want in cases:
        got = M._eastmoney_date(ts_ms)
        assert isinstance(got, datetime.date), (ts_ms, got)
        assert got == want, (ts_ms, got, want)
        # 同一条时间戳按旧码取 UTC 日 → 恰好早一天（bug 的机械复现）
        assert _old_utc_date(ts_ms) == want - datetime.timedelta(days=1), ts_ms


# ---------------------------------------------------------------- 非整点 / 闰日

def test_non_whole_hour_and_leap_day():
    """取的是「北京日历日」，与时分成无关；闰日/闰日前夜不跳日。"""
    # 北京 2024-02-29 00:00 = UTC 2024-02-28 16:00（闰日当天）
    leap = 1709136000000
    assert M._eastmoney_date(leap) == datetime.date(2024, 2, 29)
    # 北京 2023-03-01 00:00（非闰年 2 月最后一天之后）不能落到 02-29
    assert M._eastmoney_date(_beijing_ms(datetime.date(2023, 3, 1))) == datetime.date(2023, 3, 1)
    # 非整点：北京 00:00:01 仍是当天；07:59:59 仍是当天；08:00:00 仍是当天
    day = datetime.date(2024, 6, 15)
    assert M._eastmoney_date(_beijing_ms(day, second=1)) == day
    assert M._eastmoney_date(_beijing_ms(day, 7, 59, 59)) == day
    assert M._eastmoney_date(_beijing_ms(day, 8, 0, 0)) == day
    # 而旧码在「北京 0–8 点」这一区间会整体退到前一天
    assert _old_utc_date(_beijing_ms(day, 0, 0, 1)) == day - datetime.timedelta(days=1)
    assert _old_utc_date(_beijing_ms(day, 7, 59, 59)) == day - datetime.timedelta(days=1)
    assert _old_utc_date(_beijing_ms(day, 8, 0, 0)) == day


# ---------------------------------------------------------------- 回归：错位一天

def _fictional_series(n_days=300, seed=20261017):
    """两条**本应高相关**的虚构序列（共同因子 + 少量各自噪声）。

    基金侧价格挂在北京时间 00:00 的东财风格毫秒时间戳上，让 `_eastmoney_date`
    参与真实解析路径；Yahoo 侧直接给日期。返回 (fund_prices, yahoo_prices)。
    """
    rng = random.Random(seed)
    days, d = [], datetime.date(2023, 1, 2)
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += datetime.timedelta(days=1)
    common = [rng.gauss(0.0, 0.01) for _ in range(n_days)]
    fund_close = yahoo_close = 1.0
    fund_prices, yahoo_prices = [], []
    for i, day in enumerate(days):
        if i > 0:
            fund_close *= 1.0 + common[i] + rng.gauss(0.0, 0.001)
            yahoo_close *= 1.0 + common[i] + rng.gauss(0.0, 0.001)
            fund_prices.append((M._eastmoney_date(_beijing_ms(day)), fund_close))
            yahoo_prices.append((day, yahoo_close))
    return fund_prices, yahoo_prices


def test_one_day_shift_breaks_correlation():
    """错位一天 → 本应高相关的两条虚构序列被算成低相关；修正后回到高相关。

    这就是校准矩阵里「基金 × Yahoo」那批相关对被污染的最小复现：日收益本身
    没变，只是基金序列的**日期标签**整体早一天，于是配对到前一天的收益。
    """
    fund_prices, yahoo_prices = _fictional_series()
    rets_fund = M.daily_return_map(fund_prices)
    rets_yahoo = M.daily_return_map(yahoo_prices)
    assert rets_fund and rets_yahoo, "虚构序列太短，日收益字典为空"

    fixed = M.pairwise_corr(rets_fund, rets_yahoo)   # 修正后（北京日期）
    # 旧码口径：同一批毫秒时间戳按 UTC 取日 → 基金日期整体早一天
    buggy = M.pairwise_corr(
        M.daily_return_map([(_old_utc_date(_beijing_ms(d)), v) for d, v in fund_prices]),
        rets_yahoo,
    )
    assert fixed >= 0.95, f"修正后相关性应回到高相关，实际 {fixed}"
    assert abs(buggy) < 0.30, f"错位一天应把相关性打到近零，实际 {buggy}"
    assert fixed - buggy > 0.60, (fixed, buggy)


# ---------------------------------------------------------------- 真实解析路径

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_eastmoney_nav(payload):
    """用假响应喂 eastmoney_nav，断言两条净值路径都走北京日期。"""
    orig = M.urllib.request.urlopen
    M.urllib.request.urlopen = lambda *a, **kw: _FakeResponse(payload)
    try:
        return M.eastmoney_nav("000001")
    finally:
        M.urllib.request.urlopen = orig


def test_eastmoney_nav_uses_beijing_dates():
    ts = 1251216000000          # 北京 2009-08-26 00:00（UTC 2009-08-25）
    want = datetime.date(2009, 8, 26)
    # 路径一：累计净值 Data_ACWorthTrend（list 形式）
    got = _fake_eastmoney_nav(
        f"var Data_ACWorthTrend = [[{ts}, 1.50], [{ts + 86400000}, 1.55]];"
        f"var Data_netWorthTrend = [];".encode()
    )
    assert [d for d, _ in got] == [want, want + datetime.timedelta(days=1)], got
    # 路径二：Data_netWorthTrend 回退（dict 形式，真实数据即 {"x":..,"y":..}）
    got = _fake_eastmoney_nav(
        f"var Data_netWorthTrend = ["
        f'{{"x": {ts}, "y": 1.50, "equityReturn": 0.0, "unitMoney": ""}}];'.encode()
    )
    assert [d for d, _ in got] == [want], got


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
