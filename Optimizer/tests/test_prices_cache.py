#!/usr/bin/env python3
"""prices_cache 版本自愈单测（纯虚构、离线、不联网）。

背景：缓存文件存的是 `[date, close]`，日期标签由取数口径决定。2026-10-18 修正
东财时间戳（改按北京时间取日）后，旧缓存里的日期是错的；「命中即复用」会让修正
对老用户不生效。正常关闭 App 会删整个 work_dir，但崩溃 / 强制退出 / 跨版本升级的
残留必须靠缓存目录的版本标记兜底（`market_data._prices_cache_dir`）。

这些用例在「只按符号命中、不做版本校验」的旧实现上必然失败（会被旧缓存喂回错日期）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_prices_cache.py
"""

import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import market_data as M

_STALE = [
    (datetime.date(2023, 1, 2), 1.00),   # 旧口径：按 UTC 取日，整体早一天
    (datetime.date(2023, 1, 3), 1.01),
]
_FIXED = [
    (datetime.date(2023, 1, 3), 1.00),   # 新口径：按北京时间取日
    (datetime.date(2023, 1, 4), 1.01),
]


def _cache_path(work_dir, source, symbol):
    safe = M.re.sub(r"[^A-Za-z0-9_.=\-]", "_", symbol)
    return os.path.join(work_dir, "prices_cache", f"{source}_{safe}.json")


def _write_raw_cache(work_dir, source, symbol, rows):
    path = _cache_path(work_dir, source, symbol)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([[d.isoformat(), v] for d, v in rows], f)


def _write_marker(work_dir, text):
    marker = os.path.join(work_dir, "prices_cache", ".cache_version")
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(marker, "w", encoding="utf-8") as f:
        f.write(text)


def _read_marker(work_dir):
    marker = os.path.join(work_dir, "prices_cache", ".cache_version")
    try:
        with open(marker, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _call_with_fake_fetch(work_dir, symbol, payload, source="yahoo"):
    """跑一次 cached_series，并把取数函数换成返回 payload 的假实现（记录调用次数）。"""
    calls = []
    orig = M.yahoo_history if source == "yahoo" else M.eastmoney_nav

    def fake(ticker, *a, **kw):
        calls.append(ticker)
        return list(payload)

    if source == "yahoo":
        M.yahoo_history = fake
    else:
        M.eastmoney_nav = fake
    try:
        rows = M.cached_series(work_dir, source, symbol)
    finally:
        if source == "yahoo":
            M.yahoo_history = orig
        else:
            M.eastmoney_nav = orig
    return rows, calls


def _tmp_work():
    return tempfile.mkdtemp(prefix="pm-prices-cache-test-")


# ---------------------------------------------------------------- 基本行为

def test_fresh_dir_gets_version_marker():
    """首次使用：建目录、写版本标记、落缓存。"""
    work = _tmp_work()
    try:
        rows, calls = _call_with_fake_fetch(work, "AAPL", _FIXED)
        assert rows == _FIXED, rows
        assert calls == ["AAPL"], calls
        assert _read_marker(work) == str(M.PRICES_CACHE_SCHEMA), _read_marker(work)
        assert os.path.exists(_cache_path(work, "yahoo", "AAPL"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_matching_version_reuses_cache_without_fetch():
    """版本一致 → 命中缓存，不发请求（取数函数一旦被调用就算失败）。"""
    work = _tmp_work()
    try:
        _write_marker(work, str(M.PRICES_CACHE_SCHEMA))
        _write_raw_cache(work, "yahoo", "MSFT", _FIXED)
        rows, calls = _call_with_fake_fetch(work, "MSFT", _STALE)  # 若被调用会返回旧日期
        assert rows == _FIXED, rows
        assert calls == [], calls
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- 版本自愈

def test_legacy_cache_without_marker_is_wiped():
    """旧版本残留（没有标记文件）→ 整体清空，旧日期不得复用。"""
    work = _tmp_work()
    try:
        _write_raw_cache(work, "yahoo", "GOOG", _STALE)
        assert _read_marker(work) is None
        rows, calls = _call_with_fake_fetch(work, "GOOG", _FIXED)
        assert rows == _FIXED, rows
        assert calls == ["GOOG"], calls
        assert _read_marker(work) == str(M.PRICES_CACHE_SCHEMA)
        # 缓存文件已被新口径覆盖
        with open(_cache_path(work, "yahoo", "GOOG"), encoding="utf-8") as f:
            assert json.load(f)[0][0] == "2023-01-03"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_version_mismatch_wipes_cache():
    """标记版本旧于当前口径 → 清空重取。"""
    work = _tmp_work()
    try:
        _write_marker(work, str(M.PRICES_CACHE_SCHEMA - 1))
        _write_raw_cache(work, "yahoo", "NVDA", _STALE)
        rows, calls = _call_with_fake_fetch(work, "NVDA", _FIXED)
        assert rows == _FIXED, rows
        assert calls == ["NVDA"], calls
        assert _read_marker(work) == str(M.PRICES_CACHE_SCHEMA)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_corrupt_marker_is_treated_as_mismatch():
    """标记文件损坏（非整数）→ 按不匹配处理，不能把异常抛给调用方。"""
    work = _tmp_work()
    try:
        _write_marker(work, "not-a-number")
        _write_raw_cache(work, "yahoo", "TSLA", _STALE)
        rows, calls = _call_with_fake_fetch(work, "TSLA", _FIXED)
        assert rows == _FIXED, rows
        assert calls == ["TSLA"], calls
        assert _read_marker(work) == str(M.PRICES_CACHE_SCHEMA)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- 东财路径同源

def test_eastmoney_path_also_version_gated():
    """fund 源走同一条缓存路径，同样受版本标记保护（本次修复的正是基金日期）。"""
    work = _tmp_work()
    try:
        _write_raw_cache(work, "fund", "000001", _STALE)
        rows, calls = _call_with_fake_fetch(work, "000001", _FIXED, source="fund")
        assert rows == _FIXED, rows
        assert calls == ["000001"], calls
        assert _read_marker(work) == str(M.PRICES_CACHE_SCHEMA)
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
