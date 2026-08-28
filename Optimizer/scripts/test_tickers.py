#!/usr/bin/env python3
"""「新标的测试」: 把临时标的作为新增资产注入第一阶段优化.

参数口径 (与 calibrate_params.py 同源, 经 market_data.py):
- μ = 100% 历史年化收益 (用户指定, 不做先验混合); 样本 < 30 天剔除
- σ = 历史年化波动 (上限 0.60 防数据毛刺)
- 相关性 = 与各宽基大类代表序列的日收益皮尔逊相关 (重叠 < 180 天回退 0.30);
  多个测试标的之间直接互相计算

标的分类:
- 6 位纯数字 → 天天基金净值 (归境内池, 占用境内池 dom_w 约束; what-if,
  不经汇丰在售校验, 也不会出现在境内基金执行清单)
- 其余 → Yahoo 代码 (归境外池, 自由分配), 如 AAPL / 0700.HK / 7203.T / 600519.SS
"""

import re

from market_data import (
    annualized_cagr,
    annualized_vol,
    cached_series,
    daily_return_map,
    pairwise_corr,
    series_for_broad_key,
)

VOL_CAP = 0.60
_CORR_MIN_PERIODS = 180
_CORR_DEFAULT = 0.30

# D_GREATER_CN (大中华权益) 是沪深+港股混合大类, 无单一代理;
# 取 A股 与 恒指 两个代理相关的均值近似。
_D_GREATER_CN_PROXIES = [("yahoo", "000001.SS"), ("yahoo", "^HSI")]


def parse_test_tickers(raw):
    """逗号/全角逗号/分号/空白分隔 → 去重保序大写列表."""
    if not raw:
        return []
    parts = [p.strip().upper() for p in re.split(r"[,，;；\s]+", raw) if p.strip()]
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def classify_source(ticker):
    """6 位纯数字 → ("fund", 代码) 归境内池; 其余 → ("yahoo", 代码) 归境外池."""
    if re.fullmatch(r"\d{6}", ticker):
        return "fund", ticker
    return "yahoo", ticker


def resolve_test_assets(raw, work_dir, existing_keys, internal_keys, log=None):
    """解析并估算测试标的, 返回 (assets, corr_pairs, skipped, estimates).

    - assets: 追加进第一阶段 assets 的大类资产 dict (key 前缀 T_)
    - corr_pairs: {("T_X", 宽基key): corr}, 合并进 build_broad_cov 的相关性表
    - skipped: [(代码, 原因)]
    - estimates: 写入结果 JSON 的估算元数据 (Swift 测试结果弹窗展示)
    """
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
        prices = cached_series(work_dir, source, symbol, log=_log)
        if len(prices) < 30:
            if source == "fund":
                hint = " (天天基金无该 6 位代码净值历史; A股个股请用 600519.SS 形式)"
            else:
                hint = " (Yahoo 无该代码历史; 港股请用 4 位数字如 0700.HK, A股请加后缀 .SS/.SZ)"
            skipped.append((ticker, "行情历史不足 30 天" + hint))
            continue
        mu = annualized_cagr(prices)
        vol = annualized_vol(prices)
        if mu is None or vol is None:
            skipped.append((ticker, "收益/波动估算失败 (数据无效)"))
            continue
        vol = min(vol, VOL_CAP)
        pool = "domestic" if source == "fund" else "overseas"
        key = "T_" + ticker
        assets.append({
            "key": key,
            "name": "[测试] " + ticker,
            "fund_code": None,
            "pool": pool,
            "mu": round(mu, 4),
            "vol": round(vol, 4),
        })
        src_label = "eastmoney" if source == "fund" else "yahoo"
        estimates.append({
            "key": key, "ticker": ticker, "source": src_label, "pool": pool,
            "mu": round(mu, 4), "vol": round(vol, 4), "n_days": len(prices),
        })
        chosen.append((ticker, key, daily_return_map(prices)))
        _log(f"测试标的 {ticker}: n={len(prices)} mu={mu:.2%} vol={vol:.2%} pool={pool}")

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
        for broad_key in existing_keys:
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
            t1, k1, r1 = chosen[i]
            t2, k2, r2 = chosen[j]
            corr_pairs[(k1, k2)] = pairwise_corr(r1, r2, _CORR_MIN_PERIODS, _CORR_DEFAULT)

    return assets, corr_pairs, skipped, estimates
