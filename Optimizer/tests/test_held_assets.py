#!/usr/bin/env python3
"""持仓纳入（Q1）的离线单测：纯虚构 fixture、不联网、不读真实数据库/真实持仓。

契约：`tmp/agent-team/contract-2026-10-18.md` §1（A 段）。覆盖：
  ① 未建模持仓注入（资产数 / estimates / 权重合计 / 相关性合并）；
  ② modeled 不注入（内部池 key / SOURCES 执行符号 / 开放池选中基金 / 当前资产 fund_code）；
  ③ 无 held_assets 或开关 false → 原样返回（dict 级等价，且不做任何解析）；
  ④ 单只不可用 / 全部不可用 → skipped、不抛异常（流水线里 = 告警 + 不中断）；
  ⑤ 与「新标的测试」去重：同一 ticker 的测试跳过原因 = 「已作为持仓纳入」；
  ⑥ class=active 且 10% 单标的上限生效（assets dict 的 test_class + core 的上限表）；
  ⑦ 6 位境内代码归一（"123456.CN_Fund" ↔ "123456"）；
  ⑧ 装配/输出契约（§1.4/§1.5）：held 块、summary.held_assets、assets[].is_held、
     sleeve_params、相关性合并进求解、旧 extract 无 held 键（兼容）。

运行：Optimizer/.venv/bin/python3 Optimizer/tests/test_held_assets.py
祖先对照：把本文件拷进 `git archive HEAD` 树，并用
  PM_OPT_SCRIPTS=<祖先树>/Optimizer/scripts 运行 → 必红（祖先没有 held_assets.py）。
"""

import contextlib
import datetime
import json
import os
import random
import sys

import numpy as np

_SCRIPTS = os.path.abspath(os.environ.get("PM_OPT_SCRIPTS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
_PKG_ROOT = os.path.dirname(_SCRIPTS)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import held_assets as HA  # noqa: E402
import test_tickers as TT  # noqa: E402
import optimize_portfolio as OP  # noqa: E402
from portfolio_system.callers import new_asset_runner as NAR  # noqa: E402
from portfolio_system.callers.base_runner import RunRequest  # noqa: E402
from portfolio_system.callers.preset_runner import (  # noqa: E402
    PresetDeps, PresetRunner, SummaryTables, build_summary)
from portfolio_system.core.constraints import active_cap_map  # noqa: E402
from portfolio_system.core.warnings import CodedWarning  # noqa: E402

WORK_DIR = "/fixture-work"
DOM_W, OV_W, TOTAL, RF, TARGET = 0.40, 0.60, 1_000_000.0, 0.02, 0.04


# ══════════════════════════════════════════════════════════════════════════════
# 虚构 fixture：候选池表 / 行情序列都不碰仓库里的真实目录内容
# ══════════════════════════════════════════════════════════════════════════════

@contextlib.contextmanager
def _fictional_pools():
    """把 held_assets 引用的候选池表换成**虚构**表（不依赖真实 params 内容）。"""
    saved = (HA.US_INTERNAL, HA.JP_INTERNAL, HA.GC_INTERNAL, HA.SOURCES)
    HA.US_INTERNAL = {"FICTA": {"name": "虚构美股 A"}}
    HA.JP_INTERNAL = {"FICTJ": {"name": "虚构日股 J"}}
    HA.GC_INTERNAL = {"FICTC": {"name": "虚构大中华 C"}}
    HA.SOURCES = {"FICT_BROAD": ("yahoo", "FSYMB")}
    try:
        yield
    finally:
        HA.US_INTERNAL, HA.JP_INTERNAL, HA.GC_INTERNAL, HA.SOURCES = saved


def _prices(symbol, n=320):
    """确定性虚构行情序列（几何随机游走；按 symbol 固定种子）。"""
    seed = 20261018 + sum(ord(ch) for ch in str(symbol))
    rng = random.Random(seed)
    day = datetime.date(2024, 1, 2)
    price, out = 10.0, []
    for _ in range(n):
        while day.weekday() >= 5:
            day += datetime.timedelta(days=1)
        price *= 1.0 + rng.gauss(0.0006, 0.01)
        out.append((day, round(price, 6)))
        day += datetime.timedelta(days=1)
    return out


@contextlib.contextmanager
def _fake_market(unavailable=()):
    """monkeypatch 掉 test_tickers 的联网取数（cached_series / series_for_broad_key）。"""
    saved = (TT.cached_series, TT.series_for_broad_key)
    bad = {str(s).upper() for s in unavailable}

    def cached_series(work_dir, source, symbol, log=None):
        return [] if str(symbol).upper() in bad else _prices(symbol)

    def series_for_broad_key(work_dir, key, log=None):
        return _prices("BROAD:" + str(key))

    TT.cached_series = cached_series
    TT.series_for_broad_key = series_for_broad_key
    try:
        yield
    finally:
        TT.cached_series, TT.series_for_broad_key = saved


def _held(ticker, **over):
    entry = {"ticker": ticker, "name": "虚构持仓 " + ticker, "market": "US",
             "asset_class": "other", "pool": "overseas",
             "value_cny": 1234.5, "weight": 0.1}
    entry.update(over)
    return entry


# ══════════════════════════════════════════════════════════════════════════════
# ① 未建模持仓注入
# ══════════════════════════════════════════════════════════════════════════════

def test_unmodeled_held_assets_are_injected_as_what_if_legs():
    with _fictional_pools(), _fake_market():
        extract = {"held_assets": [_held("ZZFICT", asset_class="us_equity")],
                   "include_unmodeled_holdings": True}
        base = [{"key": "D1", "name": "虚构境内债", "pool": "domestic",
                 "mu": 0.03, "vol": 0.05}]
        assets, corr, estimates, skipped = HA.inject_held_assets(
            extract, base, work_dir=WORK_DIR, hsbc_pool={}, existing_keys={"D1"},
            corr_keys=["D1"], resolve=TT.resolve_test_assets)

        assert [a["key"] for a in assets] == ["D1", "H_ZZFICT"], assets
        leg = assets[-1]
        assert leg["name"] == "[持仓] ZZFICT", leg
        assert leg["test_class"] == "active", leg
        assert leg["pool"] == "overseas", leg
        assert leg["mu"] == estimates[0]["mu"] and leg["vol"] == estimates[0]["vol"]
        # estimates 与 resolve 同构 + 契约要求的加法键
        assert estimates[0]["key"] == "H_ZZFICT"
        assert estimates[0]["ticker"] == "ZZFICT"
        assert estimates[0]["is_held"] is True, estimates
        assert estimates[0]["asset_class"] == "us_equity", estimates
        assert estimates[0]["market"] == "US", estimates
        assert estimates[0]["label"] == "持仓", estimates
        for required in ("source", "pool", "mu", "vol", "n_days", "test_class",
                         "window_years", "mu_note", "vol_note"):
            assert required in estimates[0], (required, estimates[0])
        # 权重合计口径：资产数 = 原 1 + 注入 1；注入腿单独占一份权重
        assert len(assets) == 2 and len(estimates) == 1 and not skipped
        # 相关性合并：H_ 腿 vs 宽基 key
        assert ("H_ZZFICT", "D1") in corr, corr


# ══════════════════════════════════════════════════════════════════════════════
# ② modeled 不注入
# ══════════════════════════════════════════════════════════════════════════════

def test_modeled_holdings_are_not_injected():
    with _fictional_pools(), _fake_market():
        extract = {"held_assets": [
            _held("FICTA"),                # US 内部池 key
            _held("FICTJ"),                # JP 内部池 key
            _held("FSYMB"),                # market_data.SOURCES 的执行符号
            _held("123456.CN_Fund"),       # 本次开放池选中代表基金（6 位代码）
            _held("654321"),               # 当前顶层资产的 fund_code（静态锚定）
            _held("ZZFICT"),               # 未建模 → 应该注入
        ], "include_unmodeled_holdings": True}
        base = [{"key": "D1", "pool": "domestic", "mu": 0.03, "vol": 0.05,
                 "fund_code": "654321"}]
        hsbc_pool = {"D_FICT": [{"code": "123456", "name": "虚构代表基金"}]}

        modeled, unmodeled = HA.classify_held_assets(extract, base, hsbc_pool)
        assert [m["harmonized_ticker"] for m in modeled] == [
            "FICTA", "FICTJ", "FSYMB", "123456", "654321"], modeled
        assert [u["harmonized_ticker"] for u in unmodeled] == ["ZZFICT"], unmodeled

        assets, _corr, estimates, skipped = HA.inject_held_assets(
            extract, base, work_dir=WORK_DIR, hsbc_pool=hsbc_pool,
            existing_keys={"D1"}, corr_keys=["D1"], resolve=TT.resolve_test_assets)
        assert [a["key"] for a in assets] == ["D1", "H_ZZFICT"], assets
        assert [e["key"] for e in estimates] == ["H_ZZFICT"], estimates
        assert not skipped


# ══════════════════════════════════════════════════════════════════════════════
# ③ 无 held_assets / 开关 false → 原样返回（兼容）
# ══════════════════════════════════════════════════════════════════════════════

def test_no_held_assets_is_untouched():
    base = [{"key": "D1", "pool": "domestic", "mu": 0.03, "vol": 0.05}]

    def boom(*args, **kwargs):
        raise AssertionError("没有 held_assets 时不应调用解析器")

    for extract in ({}, {"include_unmodeled_holdings": True},
                    {"held_assets": []}):
        got_assets, corr, estimates, skipped = HA.inject_held_assets(
            extract, base, work_dir=WORK_DIR, hsbc_pool={}, existing_keys=set(),
            corr_keys=(), resolve=boom)
        assert got_assets is base, extract
        assert (corr, estimates, skipped) == ({}, [], []), extract

    # 有 held_assets 但开关缺失 / False → 旧行为
    extract = {"held_assets": [_held("ZZFICT")]}
    for flag in (None, False):
        payload = dict(extract)
        if flag is not None:
            payload["include_unmodeled_holdings"] = flag
        got_assets, corr, estimates, skipped = HA.inject_held_assets(
            payload, base, work_dir=WORK_DIR, hsbc_pool={}, existing_keys=set(),
            corr_keys=(), resolve=boom)
        assert got_assets is base, payload
        assert (corr, estimates, skipped) == ({}, [], []), payload


# ══════════════════════════════════════════════════════════════════════════════
# ④ 单只 / 全部不可用 → skipped、不中断
# ══════════════════════════════════════════════════════════════════════════════

def test_unavailable_holdings_are_skipped_not_raised():
    extract = {"include_unmodeled_holdings": True,
               "held_assets": [_held("ZZGOOD"), _held("ZZBAD")]}
    base = [{"key": "D1", "pool": "domestic", "mu": 0.03, "vol": 0.05}]
    with _fictional_pools(), _fake_market(unavailable={"ZZBAD"}):
        assets, _corr, estimates, skipped = HA.inject_held_assets(
            extract, base, work_dir=WORK_DIR, hsbc_pool={}, existing_keys={"D1"},
            corr_keys=["D1"], resolve=TT.resolve_test_assets)
    assert [a["key"] for a in assets] == ["D1", "H_ZZGOOD"], assets
    assert [e["key"] for e in estimates] == ["H_ZZGOOD"], estimates
    assert [t for t, _reason in skipped] == ["ZZBAD"], skipped
    assert "行情历史不足" in skipped[0][1], skipped
    # 单只失败不抛异常 → 主流程继续（返回值结构完整）
    assert len(skipped) == 1

    # 全部失败：照常返回空注入（不抛、不改入参）
    extract_all_bad = {"include_unmodeled_holdings": True,
                       "held_assets": [_held("ZZBAD"), _held("ZZNADA")]}
    with _fictional_pools(), _fake_market(unavailable={"ZZBAD", "ZZNADA"}):
        got_assets, corr, estimates, skipped = HA.inject_held_assets(
            extract_all_bad, base, work_dir=WORK_DIR, hsbc_pool={},
            existing_keys={"D1"}, corr_keys=["D1"], resolve=TT.resolve_test_assets)
    assert got_assets is base  # 全部失败 = 空注入，不改资产集合
    assert (corr, estimates) == ({}, [])
    assert [t for t, _reason in skipped] == ["ZZBAD", "ZZNADA"], skipped


# ══════════════════════════════════════════════════════════════════════════════
# ⑤ 与「新标的测试」去重（同一 ticker）
# ══════════════════════════════════════════════════════════════════════════════

class _FakeResolve:
    """受控桩：与 test_tickers.resolve_test_assets 同签名；existing_keys 命中即跳过。"""

    def __init__(self):
        self.calls = []

    def __call__(self, raw, work_dir, existing_keys, internal_keys, log=None, corr_keys=None,
                 test_class="active",
                 key_prefix="T_", name_prefix="[测试] ", label="测试"):
        self.calls.append({"raw": raw, "existing_keys": set(existing_keys),
                           "key_prefix": key_prefix, "name_prefix": name_prefix,
                           "label": label, "test_class": test_class})
        assets, skipped = [], []
        for ticker in [t.strip().upper() for t in str(raw).split(",") if t.strip()]:
            if ticker in existing_keys:
                skipped.append((ticker, "已是第一阶段大类资产"))
                continue
            assets.append({"key": key_prefix + ticker, "name": name_prefix + ticker,
                           "pool": "overseas", "mu": 0.08, "vol": 0.18,
                           "test_class": test_class})
        return assets, {}, skipped, []


def _skip_warning(ticker, reason):
    return CodedWarning(f"测试标的 {ticker} 已跳过: {reason}", "test.ticker_skipped")


def test_test_ticker_duplicating_held_leg_is_skipped_as_held():
    runner = NAR.NewAssetRunner(optimizer=None)
    warnings = []
    assets, _corr, skipped, _estimates = runner.inject_test_assets(
        [{"key": "H_ZZSAME", "pool": "overseas", "mu": 0.08, "vol": 0.18}],
        raw_tickers="ZZSAME,ZZOTHER", test_class="active", work_dir=WORK_DIR,
        resolve=_FakeResolve(), internal_keys=(), corr_keys=(),
        class_rules={}, skip_warning=_skip_warning, warnings=warnings,
        held_keys={"H_ZZSAME"}, held_tickers={"ZZSAME"})
    assert [a["key"] for a in assets] == ["H_ZZSAME", "T_ZZOTHER"], assets
    assert skipped == [("ZZSAME", "已作为持仓纳入")], skipped
    assert "已作为持仓纳入" in str(warnings[0]), warnings

    # held_tickers 的 ticker 也真的并进了 existing_keys（契约 §1.4）
    fake = _FakeResolve()
    runner.inject_test_assets(
        [{"key": "H_ZZSAME"}], raw_tickers="ZZOTHER,QWERTY", test_class="active",
        work_dir=WORK_DIR, resolve=fake, internal_keys=(), corr_keys=(),
        class_rules={}, skip_warning=_skip_warning, warnings=[],
        held_keys={"H_ZZSAME"}, held_tickers={"ZZSAME"})
    assert {"H_ZZSAME", "ZZSAME"} <= fake.calls[-1]["existing_keys"], fake.calls


# ══════════════════════════════════════════════════════════════════════════════
# ⑥ class=active 且 10% 上限生效
# ══════════════════════════════════════════════════════════════════════════════

def test_injected_leg_is_active_class_and_capped_at_ten_percent():
    with _fictional_pools(), _fake_market():
        extract = {"include_unmodeled_holdings": True,
                   "held_assets": [_held("ZZFICT")]}
        assets, _corr, estimates, _skipped = HA.inject_held_assets(
            extract, [{"key": "D1", "pool": "domestic", "mu": 0.03, "vol": 0.05}],
            work_dir=WORK_DIR, hsbc_pool={}, existing_keys={"D1"},
            corr_keys=["D1"], resolve=TT.resolve_test_assets)
    leg = assets[-1]
    assert leg["test_class"] == "active" and estimates[0]["test_class"] == "active"
    caps = active_cap_map(assets)
    assert abs(caps["H_ZZFICT"] - 0.10) < 1e-12, caps
    assert "D1" not in caps, caps     # 非 active 的宽基腿不受 10% 上限


# ══════════════════════════════════════════════════════════════════════════════
# ⑦ 6 位境内代码归一 + resolve 参数化（默认值逐字节不变）
# ══════════════════════════════════════════════════════════════════════════════

def test_six_digit_domestic_code_is_harmonized():
    assert HA.harmonize_held_ticker("123456.CN_Fund") == "123456"
    assert HA.harmonize_held_ticker("123456.CN_FUND") == "123456"
    assert HA.harmonize_held_ticker("123456.SZ") == "123456"
    assert HA.harmonize_held_ticker("123456.SH") == "123456"
    assert HA.harmonize_held_ticker(" 123456 ") == "123456"
    assert HA.harmonize_held_ticker("zzfict") == "ZZFICT"
    # A 股个股（.SS 后缀）不是 6 位境内基金，保持 Yahoo 口径
    assert HA.harmonize_held_ticker("654321.SS") == "654321.SS"
    assert HA.harmonize_held_ticker(None) == "" and HA.harmonize_held_ticker("") == ""

    # classify：同一个 6 位代码无论带不带 .CN_Fund 后缀都算 modeled
    with _fictional_pools():
        extract = {"held_assets": [_held("123456.CN_Fund"), _held("123456")]}
        modeled, unmodeled = HA.classify_held_assets(
            extract, [], {"D_FICT": [{"code": "123456", "name": "虚构基金"}]})
    assert [m["harmonized_ticker"] for m in modeled] == ["123456", "123456"]
    assert unmodeled == []


def test_resolve_defaults_unchanged_and_prefix_parameterized():
    with _fictional_pools(), _fake_market():
        default = TT.resolve_test_assets(
            "ZZFICT", WORK_DIR, existing_keys={"D1"}, internal_keys=set(),
            corr_keys=["D1"])
        explicit = TT.resolve_test_assets(
            "ZZFICT", WORK_DIR, existing_keys={"D1"}, internal_keys=set(),
            corr_keys=["D1"], test_class="active",
            key_prefix="T_", name_prefix="[测试] ", label="测试")
        prefixed = TT.resolve_test_assets(
            "ZZFICT", WORK_DIR, existing_keys={"D1"}, internal_keys=set(),
            corr_keys=["D1"], test_class="active",
            key_prefix="H_", name_prefix="[持仓] ", label="持仓")

    # 默认参数下逐字节不变：资产 / 相关性 / 跳过 / estimates 全部相等
    def _norm(result):
        assets, corr, skipped, estimates = result
        return (assets, sorted((tuple(k), v) for k, v in corr.items()),
                skipped, estimates)

    assert _norm(default) == _norm(explicit), (default, explicit)
    assert default[0][0]["key"] == "T_ZZFICT" and default[0][0]["name"] == "[测试] ZZFICT"
    assert "label" not in default[3][0], default[3]     # 默认路径不加加法键
    assert prefixed[0][0]["key"] == "H_ZZFICT"
    assert prefixed[0][0]["name"] == "[持仓] ZZFICT"
    assert prefixed[3][0]["label"] == "持仓"


# ══════════════════════════════════════════════════════════════════════════════
# ⑧ 装配 / 输出契约（§1.4/§1.5）：PresetRunner 编排路径 + build_summary
# ══════════════════════════════════════════════════════════════════════════════

BASE_ASSETS = [
    {"key": "D1", "name": "虚构境内债", "mu": 0.03, "vol": 0.05, "pool": "domestic"},
    {"key": "O_GOLD", "name": "虚构黄金", "mu": 0.05, "vol": 0.15, "pool": "overseas"},
]
HSBC = {"funds": [{"code": "F1"}], "fetched_at": "FIXTURE",
        "total_count": 1, "open_count": 1}
AVAIL = {"funds": {"F1": {"status": "open"}}, "checked_at": "FIXTURE",
         "open_count": 1, "stats": {"hsbc_fallback": 0}, "elapsed_seconds": 0.0,
         "source": "fixture", "base_url": "fixture"}
POOL = {"D1": [{"code": "F1", "name": "虚构基金"}]}
POOL_META = {"kept": 1, "categories": []}

EXTRACT_HELD = {
    "pool_mode": "fixture",
    "domestic_weight": DOM_W,
    "overseas_weight": OV_W,
    "domestic_value": TOTAL * DOM_W,
    "overseas_value": TOTAL * OV_W,
    "total_value": TOTAL,
    "rf": RF,
    "us_equity": {"holdings": [{"ticker": "ZZFICT", "weight": 1.0}]},
    "held_assets": [_held("ZZFICT", asset_class="us_equity"),
                    _held("ZZBAD", asset_class="cn_bond", market="CN")],
    "include_unmodeled_holdings": True,
    # Lead 裁决（2026-10-18）：sleeve_params 由该开关门控（App 侧写入），
    # 旧提取文件没有它 → 不产生任何加法键。
    "emit_sensitivity_anchor": True,
}

EXTRACT_OLD = {k: v for k, v in EXTRACT_HELD.items()
               if k not in ("held_assets", "include_unmodeled_holdings",
                            "emit_sensitivity_anchor")}

SLEEVE_PARAMS = {"O_US_CORE": [0.06, 0.12]}


def _fake_held_resolve(raw, work_dir, existing_keys, internal_keys, log=None, corr_keys=None,
                       test_class="active",
                       key_prefix="T_", name_prefix="[测试] ", label="测试"):
    """受控桩：只认识 ZZFICT（成功）与 ZZZBAD（行情不可用）。"""
    assets, estimates, skipped = [], [], []
    for ticker in [t.strip().upper() for t in str(raw).split(",") if t.strip()]:
        if ticker == "ZZBAD":
            skipped.append((ticker, "行情历史不足 30 天（虚构 fixture）"))
            continue
        key = key_prefix + ticker
        assets.append({"key": key, "name": name_prefix + ticker, "fund_code": None,
                       "pool": "overseas", "mu": 0.08, "vol": 0.18,
                       "test_class": test_class})
        estimates.append({"key": key, "ticker": ticker, "source": "yahoo",
                          "pool": "overseas", "mu": 0.08, "vol": 0.18,
                          "n_days": 300, "test_class": test_class,
                          "window_years": 10, "mu_note": "history_only",
                          "vol_note": "ok",
                          **({"label": label} if label != "测试" else {})})
    return assets, {(assets[0]["key"], "D1"): 0.2} if assets else {}, skipped, estimates


def _solved(assets, weights):
    weights = np.asarray(weights, dtype=float)
    ret = float(weights[:2] @ np.array([0.03, 0.05])) if len(weights) >= 2 else 0.04
    vol = 0.08
    return {
        "assets": list(assets), "keys": [a["key"] for a in assets],
        "stage1_weights": weights, "stage1_ret": ret, "stage1_vol": vol,
        "stage1_sharpe": (ret - RF) / vol, "target": TARGET,
        "internal": None, "internal_tickers": None, "stage2_error": None,
        "japan_selection": None, "gc_selection": None,
        "final": {"ret": ret, "vol": vol, "sharpe": (ret - RF) / vol,
                  "floor": ret - 1.96 * vol},
        "sleeve_params": dict(SLEEVE_PARAMS), "sleeve_iterations": 1,
        "sleeve_converged": True, "sleeve_history": [],
        "deliverable": True, "final_source": "delivered",
    }


def _deps(**over):
    captured = over.pop("captured", {})

    def _stub_inject_test_assets(assets, **kwargs):
        captured["test_kwargs"] = kwargs
        return assets, None, [], []

    values = dict(
        calibration_warnings=[],
        us_core_params=lambda weights: (0.06, 0.12),
        build_investable_assets=lambda *a, **k: [dict(x) for x in BASE_ASSETS],
        build_open_pool=lambda *a, **k: (POOL, POOL_META),
        ensure_pipeline=lambda *a, **k: (HSBC, AVAIL),
        ensure_work_dir=lambda path=None: WORK_DIR,
        live_anchors_from_extract=lambda extract: [],
        inject_test_assets=_stub_inject_test_assets,
        resolve_test_assets=_fake_held_resolve,
        internal_keys=(), corr_keys=("D1", "O_GOLD"), class_rules={},
        test_skip_warning=lambda t, r: CodedWarning(
            "测试标的 %s 已跳过: %s" % (t, r), "test.ticker_skipped"),
        solve_portfolio=lambda assets, corr, **k: (
            captured.__setitem__("solve", {"assets": [a["key"] for a in assets],
                                           "corr": dict(corr)}) or _solved(assets, 
                                               [0.3, 0.5] if len(assets) == 2 else [0.3, 0.5, 0.2])),
        broad_corr={("D1", "O_GOLD"): 0.1},
        parse_test_tickers=lambda raw: [t for t in str(raw).split(",") if t],
        class_param_help="fixture",
        cap_hits=[], cap_hit_warning=lambda hits: CodedWarning("cap fixture", "active_cap.hit"),
        warning_items_fn=lambda ws: [{"code": getattr(w, "code", "other"),
                                      "message": str(w)} for w in ws],
        # 持仓纳入（Q1）：真实实现 + 真实告警工厂（装配层接线）
        inject_held_assets=HA.inject_held_assets,
        classify_held_assets=HA.classify_held_assets,
        held_skip_warning=OP._held_skip_warning,
        us_equity_outside_catalog_warning=OP._us_equity_outside_catalog_warning,
    )
    values.update(over)
    return PresetDeps(**values), captured


def _run(deps, extract=None):
    request = RunRequest(
        assets=(), target_return=TARGET, rf=RF, domestic_weight=DOM_W,
        overseas_weight=OV_W, total_assets=TOTAL,
        extract=dict(EXTRACT_HELD if extract is None else extract),
        deps=deps, work_dir=WORK_DIR, warnings_out=[])
    return PresetRunner(optimizer=None).run(request)


def _tables():
    return SummaryTables(broad_assets=[dict(a) for a in BASE_ASSETS], broad_corr={},
                         us_internal={}, jp_internal={}, gc_internal={},
                         build_cov=lambda rows, corr: np.zeros((len(rows), len(rows))))


def test_pipeline_injects_held_and_emits_warnings():
    with _fictional_pools():
        deps, captured = _deps()
        outcome = _run(deps)
    payload = outcome.payload

    assert list(payload["stage1"]["weights"]) == ["D1", "O_GOLD", "H_ZZFICT"], payload
    solve = captured["solve"]
    assert solve["assets"] == ["D1", "O_GOLD", "H_ZZFICT"], solve
    # 相关性**始终**合并（即使没有新标的测试）
    assert solve["corr"].get(("H_ZZFICT", "D1")) == 0.2, solve["corr"]
    assert solve["corr"].get(("D1", "O_GOLD")) == 0.1, solve["corr"]

    held = payload["held"]
    assert held["counts"] == {"held": 2, "modeled": 0, "injected": 1, "skipped": 1}, held
    assert [e["ticker"] for e in held["included"]] == ["ZZFICT"]
    assert [e["is_held"] for e in held["included"]] == [True]
    assert held["skipped"] == [{"ticker": "ZZBAD",
                                "reason": "行情历史不足 30 天（虚构 fixture）"}], held
    assert ("H_ZZFICT", "D1") in [tuple(row[:2]) for row in held["corr"]], held["corr"]

    # 告警：held.skipped（逐只）+ held.us_equity_outside_catalog（us_equity 不在内部目录）
    items = payload["warning_items"]
    codes = [it["code"] for it in items]
    assert "held.skipped" in codes, codes
    assert "held.us_equity_outside_catalog" in codes, codes
    skipped_msgs = [it["message"] for it in items if it["code"] == "held.skipped"]
    assert skipped_msgs == ["持仓标的 ZZBAD 未纳入优化: 行情历史不足 30 天（虚构 fixture）"], \
        skipped_msgs
    us_msg = [it["message"] for it in items
              if it["code"] == "held.us_equity_outside_catalog"]
    assert len(us_msg) == 1 and "ZZFICT" in us_msg[0], us_msg
    # 注册表接线（契约 §1.4：A 登记 3 个 code）
    for code in ("held.skipped", "held.us_equity_outside_catalog",
                 "sensitivity.anchor_mismatch"):
        assert code in OP.WARNING_CODES, code
    assert OP._held_skip_warning("ZZ", "由于虚构原因").code == "held.skipped"

    # 新标的注入也拿到 held 的 key / ticker 用于去重
    assert captured["test_kwargs"]["held_keys"] == {"H_ZZFICT"}, captured["test_kwargs"]
    assert captured["test_kwargs"]["held_tickers"] == {"ZZFICT"}, captured["test_kwargs"]

    # summary 契约（§1.5）
    summary = build_summary(payload, tables=_tables(), rf=RF, hsbc_pool=POOL)
    assert [a["key"] for a in summary["assets"]] == ["O_GOLD", "D1", "H_ZZFICT"], summary
    held_row = next(a for a in summary["assets"] if a["key"] == "H_ZZFICT")
    assert held_row["is_held"] is True, held_row
    assert held_row["name"] == "[持仓] ZZFICT（Yahoo · 境外池 · what-if，不进执行清单）", held_row
    assert held_row["weight"] == 0.2, held_row
    assert summary["held_assets"]["included"] == held["included"], summary["held_assets"]
    assert summary["held_assets"]["skipped"] == held["skipped"], summary["held_assets"]
    assert payload["sleeve_params"] == SLEEVE_PARAMS, payload
    assert summary["sleeve_params"] == SLEEVE_PARAMS, summary
    codes = [it["code"] for it in summary["warning_items"]]
    assert "summary.missing_weights" not in codes, codes   # 持仓行必须进清单
    assert "summary.weight_sum_mismatch" not in codes, codes


def test_pipeline_without_held_keeps_old_payload_shape():
    """旧 extract（无 held_assets、无 emit 开关）：detail/summary 不多任何加法键。"""
    with _fictional_pools():
        deps, captured = _deps()
        outcome = _run(deps, extract=EXTRACT_OLD)
    payload = outcome.payload
    assert "held" not in payload, payload
    assert "sleeve_params" not in payload, payload
    assert captured["solve"]["assets"] == ["D1", "O_GOLD"], captured["solve"]
    assert captured["solve"]["corr"] == {("D1", "O_GOLD"): 0.1}, captured["solve"]
    summary = build_summary(payload, tables=_tables(), rf=RF, hsbc_pool=POOL)
    assert "held_assets" not in summary, summary     # 旧 extract → 缺键（Swift nil）
    assert "sleeve_params" not in summary, summary   # 门控关闭 → 缺键（旧文件兼容）
    legacy = {"generated_at", "portfolio", "assets", "benchmark", "test_assets",
              "target_return", "target_gap", "objective", "warnings", "source_detail"}
    assert set(summary) == legacy | {"warning_items", "deliverable", "final_source"}, \
        sorted(summary)
    assert not any(a.get("is_held") for a in summary["assets"]), summary["assets"]


def test_anchor_emit_flag_gates_sleeve_params():
    """extract 带 emit_sensitivity_anchor=true 时才出 sleeve_params（Q2 锚定需要）。"""
    extract = dict(EXTRACT_OLD)
    extract["emit_sensitivity_anchor"] = True
    with _fictional_pools():
        deps, _captured = _deps()
        outcome = _run(deps, extract=extract)
    payload = outcome.payload
    assert payload["sleeve_params"] == SLEEVE_PARAMS, payload
    assert "held" not in payload, payload
    summary = build_summary(payload, tables=_tables(), rf=RF, hsbc_pool=POOL)
    assert summary["sleeve_params"] == SLEEVE_PARAMS, summary
    assert "held_assets" not in summary, summary


def test_pipeline_held_all_failed_does_not_break():
    """全部持仓未建模且都取不到行情：只跳过 + 告警，优化照常出结果。"""
    extract = {k: v for k, v in EXTRACT_HELD.items()}
    extract["held_assets"] = [_held("ZZBAD", asset_class="other")]
    with _fictional_pools():
        deps, _captured = _deps()
        outcome = _run(deps, extract=extract)
    payload = outcome.payload
    assert payload["held"]["counts"] == {"held": 1, "modeled": 0, "injected": 0,
                                         "skipped": 1}, payload["held"]
    assert payload["held"]["included"] == []
    assert list(payload["stage1"]["weights"]) == ["D1", "O_GOLD"], payload
    codes = [it["code"] for it in payload["warning_items"]]
    assert "held.skipped" in codes and "held.us_equity_outside_catalog" not in codes, codes


# ══════════════════════════════════════════════════════════════════════════════
# runner
# ══════════════════════════════════════════════════════════════════════════════

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
