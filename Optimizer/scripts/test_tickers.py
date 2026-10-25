#!/usr/bin/env python3
"""「新标的测试」: 把临时标的作为新增资产注入第一阶段优化.

参数口径与 calibrate_params.py 完全同源（规则见 asset_classes.py）:
- 类别由调用方指定（`test_class`，宽基/策略/个股三选一，缺省 active）：
  * broad    宽基/大类   : μ = 0.5×历史 + 0.5×先验，截断 0%~20%，窗口全历史
  * strategy 策略指数     : μ = 0.67×历史 + 0.33×先验，截断 −5%~30%，窗口 20 年
  * active   个股/主动    : μ = 100% 历史，截断 −10%~40%，窗口 10 年
- **测试标的没有人工先验**（用户临时输入的代码不在表里），因此：
  * broad/strategy 只能退化成"纯历史年化"（会显式报警，口径与组合内同类标的不同）
  * active 天然就是纯历史，不需要先验
- σ = 历史年化波动（**没有人工下限**）；σ > 150% 视为数据毛刺 → 丢弃
- 相关性 = 与各宽基大类代表序列的日收益皮尔逊相关 (重叠 < 180 天回退 0.30);
  多个测试标的之间直接互相计算

标的分类:
- 6 位纯数字 → 天天基金净值 (归境内池, 占用境内池 dom_w 约束; what-if,
  不经汇丰在售校验, 也不会出现在境内基金执行清单)
- 其余 → Yahoo 代码 (归境外池, 自由分配), 如 AAPL / 0700.HK / 7203.T / 600519.SS
"""

import os as _os
import re
import sys
# P4（2026-10-17）：包已上提到 Optimizer/portfolio_system（scripts 的**兄弟目录**）——
# 这里把包根（= scripts 的父目录）加进 sys.path。bundle 内目录同构，路径同样成立。
_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

# P3-b3 步骤③：**联网取数**走 services（唯一网络入口）；纯数学函数仍直接来自 market_data
# （它们不碰 I/O，将来可随 core 化一起搬走）。
from market_data import (
    annualized_cagr,
    annualized_vol,
    daily_return_map,
    pairwise_corr,
)
from portfolio_system.services.data_fetcher import cached_series, series_for_broad_key
import asset_classes as AC
from asset_identity import canonical_ticker, classify_source as _classify_source

_CORR_MIN_PERIODS = 180
_CORR_DEFAULT = 0.30

# 契约 §1.2 的默认 label（estimates 的 label 只在非默认值时才写 → 默认路径逐字节不变）。
_DEFAULT_LABEL = "测试"

# D_GREATER_CN (大中华权益) 是沪深+港股混合大类, 无单一代理;
# 取 A股 与 恒指 两个代理相关的均值近似。
_D_GREATER_CN_PROXIES = [("yahoo", "000001.SS"), ("yahoo", "^HSI")]


def normalize_test_class(raw):
    """把用户输入的类别名收敛到 broad/strategy/active；空值/未知值 → active。"""
    if not raw:
        return "active"
    s = str(raw).strip().lower()
    alias = {"broad": "broad", "宽基": "broad", "大类": "broad", "wide": "broad",
             "strategy": "strategy", "策略": "strategy", "策略指数": "strategy",
             "active": "active", "个股": "active", "主动": "active"}
    return alias.get(s, "active")


def _window_text(years):
    return "全历史" if years is None else f"近 {years} 年"


def parse_test_tickers(raw):
    """逗号/全角逗号/分号/空白分隔 → 去重保序大写列表."""
    if not raw:
        return []
    # 与取数/持仓同一套规范化：去后缀、大写（"123456.CN_Fund" → "123456"）。
    parts = [canonical_ticker(p) for p in re.split(r"[,，;；\s]+", raw) if p.strip()]
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def classify_source(ticker):
    """6 位境内基金 → ("fund", 代码) 归境内池; 其余 → ("yahoo", 代码) 归境外池。

    规则唯一来源 = asset_identity（与 Swift AssetIdentity 同一张矩阵）。
    """
    return _classify_source(ticker)


def resolve_test_assets(raw, work_dir, existing_keys, internal_keys, log=None, corr_keys=None,
                        test_class="active",
                        key_prefix="T_", name_prefix="[测试] ", label="测试"):
    """解析并估算测试标的, 返回 (assets, corr_pairs, skipped, estimates).

    - assets: 追加进第一阶段 assets 的大类资产 dict (key = key_prefix + ticker)
    - corr_pairs: {("T_X", 宽基key): corr}, 合并进 build_broad_cov 的相关性表
    - skipped: [(代码, 原因)]
    - estimates: 写入结果 JSON 的估算元数据 (Swift 测试结果弹窗展示)

    corr_keys: 要计算相关性的宽基 key 集合, 默认 = existing_keys (本次实际参与
    第一阶段的大类)。基准对比 (_build_benchmark) 会用到全量 BROAD_ASSETS 的行,
    所以调用方可以传更大的集合 (例如所有 BROAD_ASSETS 的 key), 否则测试标的与
    被过滤掉的大类之间的相关性会缺失, 组合 Beta 会被低估。

    test_class: 标的类别 broad / strategy / active (见 asset_classes.py 与
    CLASS_PARAM_HELP 的中文说明), 缺省 active。

    key_prefix / name_prefix / label: 注入前缀与日志用词（契约 §1.2 参数化）。
    默认值（"T_"/"[测试] "/"测试"）下行为与输出**逐字节不变**；"持仓纳入"复用本函数时
    传 ("H_", "[持仓] ", "持仓")。`label` 只在非默认值时作为**加法键**写进 estimates，
    否则默认路径的结果 JSON 会多一个键（破坏逐字节兼容）。
    """
    test_class = normalize_test_class(test_class)
    def _log(msg, level="info"):
        if log:
            log(msg, level=level)

    assets, corr_pairs, skipped, estimates = [], {}, [], []
    chosen = []  # (ticker, key, rets)

    for ticker in parse_test_tickers(raw):
        if ticker in existing_keys:
            skipped.append((ticker, "已是第一阶段大类资产"))
            continue
        if ticker in internal_keys:
            skipped.append((ticker, "已是美股内部标的 (第二阶段本就全池参与, 加入无变化)"))
            continue
        source, symbol = classify_source(ticker)
        years = AC.window_years_for_class(test_class)
        prices = AC.slice_window(cached_series(work_dir, source, symbol, log=_log), years)
        if len(prices) < 30:
            if source == "fund":
                hint = " (天天基金无该 6 位代码净值历史; A股个股请用 600519.SS 形式)"
            else:
                hint = " (Yahoo 无该代码历史; 港股请用 4 位数字如 0700.HK, A股请加后缀 .SS/.SZ)"
            skipped.append((ticker, f"行情历史不足 30 天（窗口：{_window_text(years)}）" + hint))
            continue
        mu_hist = annualized_cagr(prices)
        vol_hist = annualized_vol(prices)
        if mu_hist is None or vol_hist is None:
            skipped.append((ticker, "收益/波动估算失败 (数据无效)"))
            continue
        # 测试标的没有登记先验：broad/strategy 会退化成纯历史（note 里带出来，调用方报警）
        mu, mu_note = AC.mu_for_class(test_class, mu_hist)
        vol, vol_note = AC.vol_for_class(test_class, vol_hist)
        if mu is None or vol is None:
            reason = ("σ 超过 150%，按数据毛刺丢弃" if vol_note == "sigma_glitch"
                      else "既有行情也无法构造 μ/σ")
            skipped.append((ticker, reason))
            continue
        pool = "domestic" if source == "fund" else "overseas"
        key = key_prefix + ticker
        assets.append({
            "key": key,
            "name": name_prefix + ticker,
            "fund_code": None,
            "pool": pool,
            "mu": round(mu, 4),
            "vol": round(vol, 4),
            "test_class": test_class,   # 供单标的上限判定（active 才套 10%）
        })
        src_label = "eastmoney" if source == "fund" else "yahoo"
        estimate = {
            "key": key, "ticker": ticker, "source": src_label, "pool": pool,
            "mu": round(mu, 4), "vol": round(vol, 4), "n_days": len(prices),
            "test_class": test_class, "window_years": years,
            "mu_note": mu_note, "vol_note": vol_note,
            "mu_hist": round(mu_hist, 6), "vol_hist": round(vol_hist, 6),
        }
        if label != _DEFAULT_LABEL:
            # 加法键：只有「持仓纳入」这类非测试调用才写，默认路径逐字节不变。
            estimate["label"] = label
        estimates.append(estimate)
        chosen.append((ticker, key, daily_return_map(prices)))
        _log(f"{label}标的 {ticker} [{test_class}]: n={len(prices)} "
             f"hist μ={mu_hist:.2%}/σ={vol_hist:.2%} → μ={mu:.2%}/σ={vol:.2%} pool={pool}")

    # 每个测试标的 vs 每个宽基 key 的相关性行
    broad_rets_cache = {}

    def _broad_rets(broad_key):
        if broad_key in broad_rets_cache:
            return broad_rets_cache[broad_key]
        if broad_key == "D_GREATER_CN":
            rets_list = []
            for typ, sym in _D_GREATER_CN_PROXIES:
                rets_list.append(daily_return_map(cached_series(work_dir, typ, sym, log=_log)))
            broad_rets_cache[broad_key] = rets_list
            return rets_list
        rets = daily_return_map(series_for_broad_key(work_dir, broad_key, log=_log))
        broad_rets_cache[broad_key] = rets
        return rets

    for ticker, key, rets in chosen:
        for broad_key in (corr_keys if corr_keys is not None else existing_keys):
            if broad_key == "D_GREATER_CN":
                prox = _broad_rets(broad_key)
                if prox and all(prox):
                    cs = [pairwise_corr(rets, r, _CORR_MIN_PERIODS, _CORR_DEFAULT) for r in prox]
                    v = round(sum(cs) / len(cs), 4)
                else:
                    v = _CORR_DEFAULT
            else:
                br = _broad_rets(broad_key)
                if not br:
                    v = _CORR_DEFAULT
                else:
                    v = pairwise_corr(rets, br, _CORR_MIN_PERIODS, _CORR_DEFAULT)
            corr_pairs[(key, broad_key)] = v

    # 测试标的互相之间
    for i in range(len(chosen)):
        for j in range(i + 1, len(chosen)):
            _t1, k1, r1 = chosen[i]
            _t2, k2, r2 = chosen[j]
            corr_pairs[(k1, k2)] = pairwise_corr(r1, r2, _CORR_MIN_PERIODS, _CORR_DEFAULT)

    return assets, corr_pairs, skipped, estimates
