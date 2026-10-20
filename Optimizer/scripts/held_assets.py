#!/usr/bin/env python3
"""持仓纳入（Q1）：把「持仓里有、候选池里没有」的标的注入为顶层 what-if 腿。

契约：`tmp/agent-team/contract-2026-10-18.md` §1.3。本模块只做两件纯逻辑的事：

  ① `classify_held_assets`：把 extract["held_assets"] 分成 modeled / unmodeled。
     modeled = 该标的的敞口已经被现有模型覆盖（US/JP/GC 内部池 key、market_data.SOURCES
     的执行符号、本次开放基金池选中的代表基金代码、当前顶层资产 key/fund_code）；
     其余 = 未建模 → 需要按顶层 what-if 腿纳入，否则会被静默丢弃
     （「持仓里有、候选池里没有」的标的过去就是这样消失的；这里不写任何真实标的）。
  ② `inject_held_assets`：用调用方注入的 `resolve`（= test_tickers.resolve_test_assets）
     把未建模持仓估成 `H_` 前缀的顶层资产，`test_class` 固定 "active"（纯历史、10% 上限）。
     单只失败只记 skipped、不抛异常；全部失败也照常返回空注入（持仓纳入不能把主流程搞挂）。

不联网、不落盘：行情取数全部经注入的 resolve（唯一网络入口在 services）。
"""

import re

import asset_classes as AC
from market_data import SOURCES
from params import GC_INTERNAL, JP_INTERNAL, US_INTERNAL

# 注入资产的 key / name 前缀与估算 label（契约 §1.3 固定值，不得改名）
HELD_KEY_PREFIX = "H_"
HELD_NAME_PREFIX = "[持仓] "
HELD_LABEL = "持仓"

# 境内基金代码：与 Swift 侧 `AppStore.fundCode` 同一正则
# （6 位数字 + 可选份额/交易所后缀 `.CN_Fund` / `.SZ` / `.SH`）。
# 用途：把持仓 ticker 归一成取数用的 6 位代码（"123456.CN_Fund" ↔ "123456"）；
# 注意 A 股个股用 `.SS` 后缀（不匹配本正则），保持 Yahoo 口径。
_SIX_DIGIT_RE = re.compile(r"^(\d{6})(?:\.(?:CN_FUND|SZ|SH))?$", re.IGNORECASE)


def harmonize_held_ticker(raw):
    """持仓 ticker → 归一化后的代码（6 位境内代码取数字；其余用 ticker 大写）。

    >>> harmonize_held_ticker("123456.CN_Fund") == "123456"
    >>> harmonize_held_ticker("qqq") == "QQQ"
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    match = _SIX_DIGIT_RE.match(text)
    if match is not None:
        return match.group(1)
    return text.upper()


def _internal_pool_keys():
    """US/JP/GC 三个内部池的 key 全集（调用时读取模块级表 → 测试可打桩）。"""
    return set(US_INTERNAL) | set(JP_INTERNAL) | set(GC_INTERNAL)


def _modeled_identifiers(assets, hsbc_pool):
    """当前模型已经覆盖的标识集合（契约 §1.3 的 modeled 判定口径）。"""
    modeled = {str(key).strip().upper() for key in _internal_pool_keys() if str(key).strip()}
    # ADR 0006：四只缓冲期权成员都归 BUFFER，不能再被当成未建模持仓注入。
    modeled.update(str(sym).strip().upper() for sym in AC.BUFFER_FAMILY)

    # market_data.SOURCES 的**执行符号**（第二个元素：ETF/指数代码或境内基金代码）。
    for source in SOURCES.values():
        if not source:
            continue
        symbol = str(source[1]).strip().upper()
        if symbol:
            modeled.add(symbol)

    # 本次开放基金池里各类别选中的代表基金代码（6 位境内代码）。
    for candidates in (hsbc_pool or {}).values():
        for candidate in candidates or ():
            code = harmonize_held_ticker(candidate.get("code"))
            if code:
                modeled.add(code)

    # 本次实际参与第一阶段的顶层资产：key 与它的境内基金代码（静态锚定）。
    for asset in assets or ():
        key = str(asset.get("key") or "").strip().upper()
        if key:
            modeled.add(key)
        fund_code = harmonize_held_ticker(asset.get("fund_code"))
        if fund_code:
            modeled.add(fund_code)
    return modeled


def classify_held_assets(extract, assets, hsbc_pool):
    """把 extract["held_assets"] 分成 (modeled, unmodeled) 两个 list[dict]。

    每个元素是 held_assets 原始 dict 的副本 + `"harmonized_ticker"`：
    6 位境内代码从 `"123456.CN_Fund"` / `key` 里提取，其余用 ticker 大写。
    """
    held = list((extract or {}).get("held_assets") or [])
    modeled_ids = _modeled_identifiers(assets, hsbc_pool)
    modeled, unmodeled = [], []
    for raw in held:
        entry = dict(raw) if isinstance(raw, dict) else {"ticker": str(raw)}
        ticker = harmonize_held_ticker(entry.get("ticker") or entry.get("key"))
        entry["harmonized_ticker"] = ticker
        if ticker and ticker in modeled_ids:
            modeled.append(entry)
        else:
            unmodeled.append(entry)
    return modeled, unmodeled


def _unique_unmodeled(unmodeled):
    """按 harmonized ticker 去重（保序）；没有代码的单独返回为 skipped。"""
    order, entries, seen, missing = [], {}, set(), []
    for item in unmodeled:
        ticker = item.get("harmonized_ticker") or ""
        if not ticker:
            missing.append(("", "持仓缺少标的代码，无法纳入优化"))
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        entries[ticker] = item
        order.append(ticker)
    return order, entries, missing


def inject_held_assets(extract, assets, *, work_dir, hsbc_pool, existing_keys, corr_keys,
                       resolve, log=None):
    """把**未建模**持仓注入为顶层 what-if 资产（契约 §1.3）。

    参数
    ----
    extract      : 提取文件 dict（读 held_assets / include_unmodeled_holdings）
    assets       : 当前第一阶段资产列表（只读；不修改入参）
    work_dir     : 行情缓存目录（传给 resolve）
    hsbc_pool    : 本次开放基金池（modeled 判定用）
    existing_keys: 当前顶层资产 key 集合（resolve 据此避免重复注入）
    corr_keys    : 要算相关性的宽基 key 集合（传给 resolve）
    resolve      : 注入的解析器，签名与 test_tickers.resolve_test_assets 相同
    log          : 可选 log(message, level="info")

    返回 (assets2, corr_pairs, estimates, skipped)：
      - estimates / skipped 与 resolve 同构（estimates 每项带 "is_held": True、
        "asset_class"、"market"；skipped 是 [(ticker, reason)]）；
      - 单只失败不抛异常（写 skipped，由调用方生成告警）；**全部失败也照常返回空注入**；
      - include_unmodeled_holdings 缺失/False 或没有 held_assets → 原样返回
        `(assets, {}, [], [])`（= 旧行为，输出逐字节不变）。
    """
    extract = extract or {}
    held = list(extract.get("held_assets") or [])
    if not held or not extract.get("include_unmodeled_holdings"):
        return assets, {}, [], []

    _modeled, unmodeled = classify_held_assets(extract, assets, hsbc_pool)
    if not unmodeled:
        return assets, {}, [], []

    order, entries, skipped = _unique_unmodeled(unmodeled)
    if not order:
        return assets, {}, [], skipped

    resolved_assets, corr_pairs, resolve_skipped, estimates = [], {}, [], []
    try:
        resolved_assets, corr_pairs, resolve_skipped, estimates = resolve(
            ",".join(order),
            work_dir,
            existing_keys=set(existing_keys or ()),
            internal_keys=_internal_pool_keys(),
            log=log,
            corr_keys=corr_keys,
            test_class="active",
            key_prefix=HELD_KEY_PREFIX,
            name_prefix=HELD_NAME_PREFIX,
            label=HELD_LABEL,
        )
    except Exception as exc:  # noqa: BLE001 — 持仓纳入失败不得中断主流程
        resolve_skipped = [(ticker, f"估算失败: {type(exc).__name__}: {exc}")
                           for ticker in order]
        resolved_assets, corr_pairs, estimates = [], {}, []

    skipped.extend(resolve_skipped)
    for estimate in estimates:
        ticker = str(estimate.get("ticker") or "").upper()
        source_entry = entries.get(ticker) or {}
        estimate["is_held"] = True
        estimate["asset_class"] = source_entry.get("asset_class")
        estimate["market"] = source_entry.get("market")
        estimate.setdefault("name", HELD_NAME_PREFIX + ticker)
    if log is not None and resolved_assets:
        log(f"持仓纳入：{len(resolved_assets)} 只未建模持仓按顶层 what-if 腿注入"
            f"（{', '.join(a['key'] for a in resolved_assets)}）")
    if not resolved_assets:
        return assets, {}, [], skipped
    return list(assets) + list(resolved_assets), corr_pairs or {}, estimates, skipped
