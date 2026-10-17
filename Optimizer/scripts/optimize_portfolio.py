#!/usr/bin/env python3
"""Two-stage portfolio optimization."""

import argparse
import json
import sys
from datetime import datetime, timezone

import numpy as np

try:
    from scipy.optimize import minimize
except ImportError:
    print("scipy is required.", file=sys.stderr)
    sys.exit(2)

from params import (
    BROAD_ASSETS,
    BROAD_CORR,
    CALIBRATION_WARNINGS,
    CALIBRATION_PROVENANCE,
    describe_param_source,
    param_origin,
    RF,
    TARGET_RETURN,
    US_INTERNAL,
    US_INTERNAL_CORR,
    JP_INTERNAL,
    JP_INTERNAL_CORR,
    GC_INTERNAL,
    GC_INTERNAL_CORR,
    build_broad_cov,
    build_open_pool_from_status,
    us_core_params,
)
from optimize_common import (
    portfolio_stats,
    solve_min_var,
    solve_max_sharpe,
    max_feasible_return,
    stage1_bounds,
    project_psd,
    build_corr_matrix,
    upper_bounds_array as _upper_array,   # 名字保留 _upper_array：test_optimizer_caps 直接引用
    _respects_bounds,
)
# P3-b3：基金状态流水线改走 services（唯一子进程入口）；脚本层不再直接 import fund_pipeline。
from portfolio_system.services.pipeline import (
    build_investable_assets,
    ensure_pipeline,
    ensure_work_dir,
    live_anchors_from_extract,
)
from portfolio_system.services.portfolio_source import load_extract
from test_tickers import parse_test_tickers, resolve_test_assets
import asset_classes as AC

# D2：active 上限表与告警载体已搬进 core（portfolio_system.core.constraints / core.warnings）。
# 本模块只保留「薄壳 + 模块级 CAP_HITS 汇总」，原有公开名与调用签名一律不变。
from portfolio_system.core.constraints import (
    active_cap_map as _core_active_cap_map,
    apply_active_caps as _core_apply_active_caps,
)
from portfolio_system.core.warnings import CodedWarning as _Warn   # noqa: F401

# P3-b1：袖内/阶段二纯数学已搬进 core；这里保留同名薄壳与再导出（公开名与私有名都保留）。
from portfolio_system.core.constraints import max_return_given_mu as _max_return_given_mu
from portfolio_system.core.stage2 import (
    internal_pool_optimize as _core_internal_pool_optimize,
    optimize_within_pool as _optimize_within_pool,
    pool_cap_norm as _pool_cap_norm,
    pool_max_return as _pool_max_return,
    stage2_covariance as _core_stage2_covariance,
    stage2_gc_optimize as _core_stage2_gc_optimize,
    stage2_japan_optimize as _core_stage2_japan_optimize,
    stage2_optimize as _core_stage2_optimize,
)
# P3-b2：固定点迭代/两层引擎已搬进 core；这里保留同名薄壳与再导出（公开名与私有名都保留）。
from portfolio_system.core import engine as _core_engine
from portfolio_system.core.engine import (
    is_stage1_fallback_prompt as _is_stage1_fallback_prompt,
    solve_stage1 as _solve_stage1,
)
from portfolio_system.core.engine import STAGE1_FALLBACK_MARK as _STAGE1_FALLBACK_MARK
from portfolio_system.core.fixpoint import (
    compute_sleeve_floors as _core_compute_sleeve_floors,
    floor_next_params as _core_floor_next_params,
    floor_seed as _core_floor_seed,
    required_sleeve_mu as _core_required_sleeve_mu,
    reseed_sleeves as _core_reseed_sleeves,
    resolved_sleeves as _resolved_sleeves,
    seed_levels_for_target as _core_seed_levels_for_target,
    sleeve_failure_reason as _core_sleeve_failure_reason,
    sleeve_levels_for_feasibility as _core_sleeve_levels_for_feasibility,
    sleeve_tangency_all as _core_sleeve_tangency_all,
    stage1_weight as _stage1_weight,
    undeliverable_warning as _core_undeliverable_warning,
    unresolved_weighted_sleeves as _unresolved_weighted_sleeves,
    validate_sleeve_params as _validate_sleeve_params,
)
from portfolio_system.core.sleeve_params import (  # noqa: F401
    build_pool_specs,
    FLOOR_SLEEVE_KEYS as _FLOOR_SLEEVE_KEYS,
    MAX_SLEEVE_ITER as _MAX_SLEEVE_ITER,
    SLEEVE_DAMPING as _SLEEVE_DAMPING,
    SLEEVE_KEYS as _SLEEVE_KEYS,
    SLEEVE_RESOLVED_FIELD as _SLEEVE_RESOLVED_FIELD,
    SLEEVE_TOL as _SLEEVE_TOL,
    SleeveHooks,
    SleeveUniverse,
)
from portfolio_system.core.warnings import warning_items as _warning_items
from portfolio_system.core.sleeve_params import (
    damp_sleeve_params as _damp_sleeve_params,
    inject_sleeve_params as _inject_sleeve_params,
    sleeve_params_us as _core_sleeve_params_us,
    sleeve_reseed_params as _core_sleeve_reseed_params,
    sleeve_tangency as _core_sleeve_tangency,
)



# 「个股/主动」类单标的组合权重上限（asset_classes.ACTIVE_CAP = 10%）触发记录，
# 最后合并成一条报警，避免每次跑都刷屏。
CAP_HITS = []

# P3-b3：SP500 内部相关性表是**纯数据**，唯一来源已搬进 core/stage2.py（这里只再导出）。
from portfolio_system.core.stage2 import (  # noqa: E402
    SP500_INTERNAL_CORR, SP500_INTERNAL_CORR_DEFAULT)


def _note_cap_hit(key, weight, cap):
    CAP_HITS.append((key, float(weight), float(cap)))


#: 内部池清单（params 的 US/JP/GC_INTERNAL 的 key 全集）—— 上限表**必须**覆盖内部标的。
#: D2 起 core 侧 active_cap_map 不再 import params，改由本模块（调用方）显式传入。
_INTERNAL_POOL_KEYS = tuple(US_INTERNAL) + tuple(JP_INTERNAL) + tuple(GC_INTERNAL)


#: P3-b2：内部池清单改为**调用时**计算（模块级 INTERNAL 表打桩仍兼容）。
def _internal_pool_keys():
    return tuple(US_INTERNAL) + tuple(JP_INTERNAL) + tuple(GC_INTERNAL)


def _universe():
    """把当前模块级内部池表装配成 core 的 SleeveUniverse（调用时读取 → 变量打桩兼容）。"""
    return SleeveUniverse(
        us_internal=US_INTERNAL, us_internal_corr=US_INTERNAL_CORR,
        jp_internal=JP_INTERNAL, jp_internal_corr=JP_INTERNAL_CORR,
        gc_internal=GC_INTERNAL, gc_internal_corr=GC_INTERNAL_CORR,
        sp500_internal_corr=SP500_INTERNAL_CORR,
        sp500_internal_corr_default=SP500_INTERNAL_CORR_DEFAULT,
        pool_specs=_SLEEVE_POOL_SPECS,
        internal_pool_keys=_internal_pool_keys(),
    )


def _active_cap_map(assets):
    """key → 组合权重上限。仅对 class == active 的标的生效（D2 起为 core 的薄壳）。

    必须覆盖**所有**标的：大类槽位（如 O_US_ENERGY=XOM）、美股/日本/香港/大中华内部标的
    （AAPL、NVDA…）以及 test_class=active 的测试标的 —— 漏掉内部标的会让上限形同虚设
    （2026-10-13 实测：曾出现单标的被拿到超过 10% 上限、却没人拦截的情况）。
    """
    return _core_active_cap_map(assets, _internal_pool_keys())


def apply_active_caps(upper, assets, cap_map, dom_idx, dom_w, warnings):
    """把 active 类单标的上限写进 stage-1 上界；与池权重约束冲突时放宽到可行并报警。

    可行性条件：每个池的 upper 之和必须 ≥ 该池的目标权重，否则优化必然不可行
    （典型情形：境内池只有一只可用标的，却要求它 ≤ 10%，而境内目标权重 30%）。

    实现（含文案逐字与告警 code）已搬进 core/constraints；本函数为薄壳。
    ⚠️ **不得**把 core 返回的「上界被夹紧」记录记成触顶：夹紧只说明某个 active 腿的上界
    被压到 cap（无论它最终拿不拿得到权重），而触顶 = **交付/生效权重 ≥ cap − ε**。
    真触顶由调用方在拿到权重后判定（`_solve_once` / 阶段二内部各自 `_note_cap_hit`）；
    把夹紧当触顶会让结果页点名一只**权重为 0**的标的（2026-10-17 核验实测的缺陷）。
    """
    _core_apply_active_caps(upper, assets, cap_map, dom_idx, dom_w, warnings)


def log_step(step, message, level="info", log_path=None):
    """Append a JSONL step record for the Swift sidecar / AI to read."""
    if not log_path:
        return
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "step": step,
             "message": message, "level": level}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# portfolio_stats is imported from optimize_common; it accepts rf as a keyword arg.
# Callers in this module pass rf=RF (module-level, dynamically overridden).


# 基金申赎管线 (实时抓汇丰 + 天天基金校验 + 开放池构建) 统一在 fund_pipeline.py;
# 可投大类构建 (build_investable_assets) 也在那里, 与 sensitivity_analysis.py 共用同一口径.


def stage2_covariance(fixed_assets, internal_tickers, broad_cov, broad_keys, broad_vols):
    """Build covariance for fixed assets + internal US holdings.

    P3-b1：实现已搬进 core/stage2.py；本函数是薄壳 —— 内部池表在**调用时**读取模块级变量
    （测试会 monkeypatch OP.US_INTERNAL / OP.US_INTERNAL_CORR，不能在 import 期快照）。
    """
    return _core_stage2_covariance(fixed_assets, internal_tickers, broad_cov, broad_keys, broad_vols,
                                   us_internal=US_INTERNAL, us_internal_corr=US_INTERNAL_CORR,
                                   sp500_internal_corr=SP500_INTERNAL_CORR,
                                   sp500_internal_corr_default=SP500_INTERNAL_CORR_DEFAULT)


def stage2_optimize(stage1_weights, extract, total_assets, assets, broad_cov, broad_keys, broad_vols,
                    cap_map=None, target=None, rf=None, n_starts=80):
    """Optimize internal US equity weights with total US core weight fixed and no per-ticker caps.

    P3-b1：实现已搬进 core/stage2.py；本函数是薄壳 —— 模块级 TARGET_RETURN / RF 与内部池表
    在**调用时**解析（既有 monkeypatch 语义不变），触顶记录仍写回本模块的 CAP_HITS。
    """
    return _core_stage2_optimize(
        stage1_weights, extract, total_assets, assets, broad_cov, broad_keys, broad_vols,
        TARGET_RETURN if target is None else target,
        RF if rf is None else rf,
        cap_map=cap_map, n_starts=n_starts,
        us_internal=US_INTERNAL, us_internal_corr=US_INTERNAL_CORR,
        sp500_internal_corr=SP500_INTERNAL_CORR,
        sp500_internal_corr_default=SP500_INTERNAL_CORR_DEFAULT,
        cap_hits_out=CAP_HITS)


def select_domestic_funds(stage1_weights, assets, hsbc_pool, total_assets):
    """For each allocated domestic broad category, pick the representative open fund.

    This is a simple deterministic selection from the 基金搜索易 open-fund pool:
    prefer the category's original anchor fund if it is in the pool, otherwise use
    the first open fund in that category. All open funds remain available as
    alternatives; a full per-fund internal optimizer can be added later.
    """
    selection = {}
    for i, a in enumerate(assets):
        if a["pool"] != "domestic":
            continue
        key = a["key"]
        weight = float(stage1_weights[i])
        if weight <= 1e-6:
            continue
        candidates = hsbc_pool.get(key, [])
        if not candidates:
            continue
        anchor = a.get("fund_code")
        chosen = None
        if anchor:
            for c in candidates:
                if c["code"] == anchor:
                    chosen = c
                    break
        if chosen is None:
            chosen = candidates[0]
        selection[key] = {
            "category_weight": weight,
            "amount": weight * total_assets,
            "selected": chosen,
            "candidate_count": len(candidates),
            "candidates": candidates,
        }
    return selection


# 袖内收益下限的绝对容差（P1）：SLSQP 末位噪声 + 离散选标。
# ── 容差的唯一来源（2026-10-17 收口）────────────────────────────────────────────
# P2（2026-10-17）：容差常量的唯一来源已搬进 portfolio_system/core/constants.py，
# 这里只做再导出，保持既有 import 路径与名字不变（不许再出现字面量）。
from portfolio_system.core.constants import (  # noqa: E402
    CHECK_SUM_EPS, DELIVERY_EPS, SLEEVE_FLOOR_EPS, SLEEVE_ROOM_EPS)
from portfolio_system.core.constants import SOLVER_EPS as _SOLVER_EPS  # noqa: E402








def _internal_pool_optimize(stage1_weights, assets, internal_pool, internal_corr, broad_key, total_assets,
                            seed=31, cap_map=None, rf=None, floor=None, warnings=None):
    """Generic second-level optimization inside a broad equity bucket.

    P3-b1：实现已搬进 core/stage2.py；本函数是薄壳（RF 兜底与 CAP_HITS 回写在调用方）。
    """
    return _core_internal_pool_optimize(
        stage1_weights, assets, internal_pool, internal_corr, broad_key, total_assets,
        RF if rf is None else rf, seed=seed, cap_map=cap_map, floor=floor, warnings=warnings,
        cap_hits_out=CAP_HITS)


def stage2_japan_optimize(stage1_weights, assets, total_assets, cap_map=None, rf=None,
                          floor=None, warnings=None):
    """Optimize Japan equity internal factor weights.（floor：P1 袖内收益下限）

    P3-b1：实现已搬进 core/stage2.py；日本内部池表在**调用时**读取（monkeypatch 语义不变）。
    """
    return _core_stage2_japan_optimize(
        stage1_weights, assets, total_assets, JP_INTERNAL, JP_INTERNAL_CORR,
        RF if rf is None else rf, cap_map=cap_map, floor=floor, warnings=warnings,
        cap_hits_out=CAP_HITS)


def stage2_gc_optimize(stage1_weights, assets, total_assets, cap_map=None, rf=None,
                       floor=None, warnings=None):
    """Optimize Greater China equity internal (沪深300/红利低波/主动/混合/指数/港股宽基/高股息/科技).

    P3-b1：实现已搬进 core/stage2.py；大中华内部池表在**调用时**读取（monkeypatch 语义不变）。
    """
    return _core_stage2_gc_optimize(
        stage1_weights, assets, total_assets, GC_INTERNAL, GC_INTERNAL_CORR,
        RF if rf is None else rf, cap_map=cap_map, floor=floor, warnings=warnings,
        cap_hits_out=CAP_HITS)


def build_result_summary(output, hsbc_pool=None):
    """Build a clean Finance result file: per-asset weights/return/vol/sharpe + totals."""
    # 境外/跨池大类的执行代码（标的名前加 [代码] 前缀，便于直接下单；
    # 无单一成交代码的大类保持 None，不加前缀）
    exec_code = {
        "O_BTC": "BTC",
        "O_HYLB": "HYLB",
        "O_SG_EQ": "G3B.SI",
        "O_US_TLT": "TLH",
        "O_US_REIT": "REZ",
        "O_US_ENERGY": "XOM",
    }
    # 大中华二级风格桶 → 境内基金池类别（风格桶本身无单一成交代码，
    # 执行代表基金取自对应类别的去重/最低费率代表，与境内大类同一套哲学）
    gc_bucket_pool = {
        "CSI300": "D_HSBC_CSI300",
        "DIVLOWVOL": "D_HSBC_DIVLOWVOL",
        "DIVLOWVOL100": "D_CN_DIVLOWVOL100",
        "STOCK": "D_CN_STOCK",
        "MIXED": "D_CN_MIXED",
        "INDEX": "D_CN_INDEX",
        "HK_BROAD": "D_CN_HK",
        "HK_DIV": "D_CN_HK",
        "HK_TECH": "D_HSBC_HSTECH",
    }
    assets = []
    # Domestic categories with selected representative funds
    for key, sel in output.get("domestic_selection", {}).items():
        a = next((x for x in BROAD_ASSETS if x["key"] == key), None)
        if a is None:
            continue
        mu = a["mu"]
        vol = a["vol"]
        assets.append({
            "key": key,
            "name": f"[{sel['selected']['code']}] {a['name']} → {sel['selected']['name']}",
            "weight": sel["category_weight"],
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - RF) / vol if vol else 0.0,
        })
    # US core internal split
    stage2 = output.get("stage2") or {}
    for ticker, w in stage2.get("internal_weights_total", {}).items():
        if w <= 1e-8:
            continue
        info = US_INTERNAL.get(ticker)
        if info is None:
            continue
        mu = info["mu"]
        vol = info["vol"]
        assets.append({
            "key": ticker,
            "name": f"[{ticker}] {info['name']}",
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - RF) / vol if vol else 0.0,
        })
    # Japan equity internal factor split
    jp = output.get("japan_selection")
    if jp is not None:
        for ticker, w in jp.get("internal_weights_total", {}).items():
            if w <= 1e-8:
                continue
            info = JP_INTERNAL.get(ticker)
            if info is None:
                continue
            mu = info["mu"]
            vol = info["vol"]
            assets.append({
                "key": f"JP:{ticker}",
                "name": f"[{ticker}] 日本·{info['name']}",
                "weight": w,
                "expected_return": mu,
                "volatility": vol,
                "sharpe": (mu - RF) / vol if vol else 0.0,
            })

    # Greater China equity internal factor split
    # 风格桶无单一成交代码：前缀用对应境内基金池的代表基金代码
    gc = output.get("gc_selection")
    if gc is not None:
        gc_used = {}  # pool_key -> 已用 code 列表，同池多桶避免重复同一代表
        for ticker, w in gc.get("internal_weights_total", {}).items():
            if w <= 1e-8:
                continue
            info = GC_INTERNAL.get(ticker)
            if info is None:
                continue
            mu = info["mu"]
            vol = info["vol"]
            rep = None
            if hsbc_pool:
                pool_key = gc_bucket_pool.get(ticker)
                cands = hsbc_pool.get(pool_key) or []
                used = gc_used.setdefault(pool_key, [])
                rep = next((c for c in cands if c["code"] not in used), None)
                if rep is not None:
                    used.append(rep["code"])
            name = (f"[{rep['code']}] 大中华·{info['name']} → {rep['name']}"
                    if rep is not None else f"大中华·{info['name']}")
            assets.append({
                "key": f"GC:{ticker}",
                "name": name,
                "weight": w,
                "expected_return": mu,
                "volatility": vol,
                "sharpe": (mu - RF) / vol if vol else 0.0,
            })

    # Other overseas broad assets
    for key, w in output.get("stage1", {}).get("weights", {}).items():
        if key in [a["key"] for a in assets] or key in ("O_US_CORE", "O_JP_EQ", "O_HK_HSTECH", "D_GREATER_CN"):
            continue
        a = next((x for x in BROAD_ASSETS if x["key"] == key), None)
        if a is None:
            continue
        mu = a["mu"]
        vol = a["vol"]
        code = a.get("fund_code") or exec_code.get(key)
        name = f"[{code}] {a['name']}" if code else a["name"]
        assets.append({
            "key": key,
            "name": name,
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - RF) / vol if vol else 0.0,
        })
    # 测试标的 (T_ 前缀): 它们是"新标的测试"临时注入的大类, 不在 BROAD_ASSETS 里,
    # 但同样占用第一阶段权重。以前这里直接 continue 掉, 后果是:
    # (a) 权重合计 < 1(看起来"没买满"), (b) 用户看到的清单里永远没有新标的,
    # 于是"加入新标的值不值得"这个问题的答案被整段吞掉 (2026-10-13 修)。
    test_meta = {t["key"]: t for t in (output.get("test") or {}).get("assets", [])}
    listed = {a["key"] for a in assets}
    for key, w in output.get("stage1", {}).get("weights", {}).items():
        if key in listed:
            continue
        m = test_meta.get(key)
        if m is None:
            continue
        mu = float(m["mu"])
        vol = float(m["vol"])
        src = "天天基金" if m.get("source") == "eastmoney" else "Yahoo"
        pool = "境内池" if m.get("pool") == "domestic" else "境外池"
        assets.append({
            "key": key,
            "name": f"[测试] {m['ticker']}（{src} · {pool} · what-if，不进执行清单）",
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - RF) / vol if vol else 0.0,
            "is_test": True,
        })
    assets.sort(key=lambda x: -x["weight"])
    for a in assets:
        a["weight"] = round(float(a["weight"]), 6)
        a["expected_return"] = round(float(a["expected_return"]), 6)
        a["volatility"] = round(float(a["volatility"]), 6)
        a["sharpe"] = round(float(a["sharpe"]), 6)
    final = output.get("final") or {}
    # 自检: 报告清单必须覆盖第一阶段所有权重。历史上测试标的(T_)被静默丢弃过一次,
    # 于是"清单合计 82%、指标却按 100% 算"这种错能一路滑到界面上 —— 这里把它变成显式提示。
    # 注意有些 key 是**故意**不出现在清单里的: 它们被拆成了第二阶段的子行
    # (O_US_CORE→美股内部标的、O_JP_EQ/O_HK_HSTECH/D_GREATER_CN→各内部桶、境内大类→选中基金)。
    expanded = set(output.get("domestic_selection", {}).keys())
    for sel_key, broad_key in (("stage2", "O_US_CORE"), ("japan_selection", "O_JP_EQ"),
                               ("gc_selection", "D_GREATER_CN")):
        if output.get(sel_key) is not None:
            expanded.add(broad_key)
    emitted = {a["key"] for a in assets}
    missing = [k for k, w in output.get("stage1", {}).get("weights", {}).items()
               if w > 1e-6 and k not in emitted and k not in expanded]
    total_w = sum(float(a["weight"]) for a in assets)
    # 优化器自身的提示(候选被剔除、池口径、数据源计数…)必须原样带上:
    # 它们解释了"为什么这次的可投大类和上次不一样", 是解读结果的前提。
    summary_warnings = list(output.get("warnings", []))
    if missing:
        summary_warnings.append(_Warn(
            "自检: 以下权重没有出现在结果清单里（映射漏项）: " + ", ".join(sorted(missing)),
            "summary.missing_weights"))
    if abs(total_w - 1.0) > CHECK_SUM_EPS:
        summary_warnings.append(_Warn(
            f"自检: 结果清单权重合计为 {total_w:.4f}，不等于 1 —— 请对照第一阶段权重核对映射是否漏项。",
            "summary.weight_sum_mismatch"))
    summary = {
        "generated_at": output.get("availability", {}).get("checked_at"),
        "portfolio": {
            "expected_return": round(float(final.get("ret", 0.0)), 6),
            "volatility": round(float(final.get("vol", 0.0)), 6),
            "sharpe": round(float(final.get("sharpe", 0.0)), 6),
            "worst_year_95": round(float(final.get("floor", 0.0)), 6),
        },
        "assets": assets,
        "benchmark": _build_benchmark(output, final),
        "test_assets": (output.get("test") or {}).get("assets"),
        "target_return": output.get("target_return"),
        "target_gap": output.get("target_gap"),
        "objective": output.get("objective"),
        "deliverable": bool(output.get("deliverable", True)),
        "final_source": output.get("final_source", "delivered"),
        "warnings": summary_warnings,
        # 加法式告警明细（code + message）：Swift 只认 code，不再匹配文案。
        # 必须用**最终** summary_warnings 生成 —— 它包含本函数内追加的自检类告警，
        # 以及调用前已并入 output["warnings"] 的 CAP_HITS 类告警。
        "warning_items": _warning_items(summary_warnings),
        "source_detail": None,
    }
    return summary


def _build_benchmark(output, final):
    """Benchmark = 境内池 100% 沪深300ETF + 海外池 100% 标普500ETF."""
    try:
        pool = output.get("pool", {})
        dw = float(pool.get("domestic_weight", 0.0))
        ow = float(pool.get("overseas_weight", 0.0))
        core = output.get("us_core_aggregate", {})
        broad = []
        for a in BROAD_ASSETS:
            item = dict(a)
            if a["key"] == "O_US_CORE":
                item["mu"] = core.get("mu", a.get("mu"))
                item["vol"] = core.get("vol", a.get("vol"))
            broad.append(item)
        # 测试标的也要进这张协方差表: 组合 Beta 用的是"组合权重 × 基准权重"的协方差,
        # 漏掉测试标的等于把它们当空仓, Beta 被低估、Alpha 被高估 (2026-10-13 修)。
        test_block = output.get("test") or {}
        test_meta = {t["key"]: t for t in test_block.get("assets", [])}
        for key, m in test_meta.items():
            broad.append({"key": key, "name": key, "mu": m["mu"], "vol": m["vol"],
                          "pool": m.get("pool")})
        keys = [a["key"] for a in broad]
        mu = np.array([a["mu"] if a["mu"] is not None else 0.0 for a in broad])
        vols = np.array([a["vol"] if a["vol"] is not None else 0.0 for a in broad])
        corr_pairs = dict(BROAD_CORR)
        for i, j, v in test_block.get("corr", []) or []:
            corr_pairs[(i, j)] = float(v)
        cov = build_broad_cov([{"key": k, "vol": v} for k, v in zip(keys, vols)], corr_pairs)

        w_bench = np.zeros(len(keys))
        if "D_GREATER_CN" in keys:
            w_bench[keys.index("D_GREATER_CN")] = dw
        elif "D_HSBC_CSI300" in keys:
            w_bench[keys.index("D_HSBC_CSI300")] = dw
        if "D_HSBC_SP500" in keys:
            w_bench[keys.index("D_HSBC_SP500")] = ow

        bench_ret = float(w_bench @ mu)
        bench_vol = float(np.sqrt(w_bench @ cov @ w_bench))

        # Portfolio broad weights (stage1) for beta approximation.
        stage1 = output.get("stage1", {}).get("weights", {})
        w_p = np.zeros(len(keys))
        for i, k in enumerate(keys):
            w_p[i] = float(stage1.get(k, 0.0))
        cov_pb = float(w_p @ cov @ w_bench)
        var_b = float(w_bench @ cov @ w_bench)
        beta = cov_pb / var_b if var_b > 0 else 0.0

        ret_p = float(final.get("ret", 0.0))
        alpha = ret_p - (RF + beta * (bench_ret - RF))

        return {
            "weights": {"domestic_CSI300": dw, "overseas_SP500": ow},
            "expected_return": round(bench_ret, 6),
            "volatility": round(bench_vol, 6),
            "sharpe": round((bench_ret - RF) / bench_vol, 6) if bench_vol > 0 else 0.0,
            "worst_year_95": round(bench_ret - 1.96 * bench_vol, 6),
            "portfolio_alpha": round(alpha, 6),
            "portfolio_beta": round(beta, 6),
            "volatility_reduction": round(bench_vol - float(final.get("vol", 0.0)), 6),
            "worst_year_improvement": round(float(final.get("floor", 0.0)) - (bench_ret - 1.96 * bench_vol), 6),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- 完整求解（阶段一 + 袖内固定点）
#
# 2026-10-14 修正（用户实测发现）：阶段一过去把「袖」——由内部标的动态组成的大类
# （美股核心 O_US_CORE、日本权益 O_JP_EQ、大中华权益 D_GREATER_CN）——当成"当前持仓那一篮子"
# （O_US_CORE 的 μ/σ 按 live 权重聚合），而阶段二会在袖内**重新选标的**。
# 于是阶段一的模型 ≠ 交付组合：目标落在切点收益以下时，阶段一正好贴着目标交付，阶段二却把
# 交付收益顶出目标一大截；只扫阶段一的敏感性分析因此与结果页对不上（同一目标点两处结论矛盾）。
# 现在把袖内 μ/σ 迭代到固定点：**阶段一假设的袖内参数 = 阶段二实际交付的那一篮子**，
# 于是"目标 ≥ T"这条约束对交付组合同样有效，敏感性扫描也与主结果同源。
SLEEVE_KEYS = _SLEEVE_KEYS   # P3-b2：常量唯一来源在 core/sleeve_params.py
# P1（2026-10-16）：只有这两只袖走 _internal_pool_optimize —— 需要下限传导的路径。
# O_US_CORE 的 stage2_optimize 本来就带组合级 ret>=target 约束，不接受也不该接受袖内下限。
FLOOR_SLEEVE_KEYS = _FLOOR_SLEEVE_KEYS
MAX_SLEEVE_ITER = _MAX_SLEEVE_ITER
# 收敛判据放在"袖参数"上：单只袖的 μ/σ 变化都小于 0.4 个百分点就认为阶段一与交付组合同源。
# （袖内选标是离散的：谁入选、谁触 10% 上限会跳变，SLSQP 也有末位噪声，判据过严会永远不收敛。）
SLEEVE_TOL = _SLEEVE_TOL
# 袖内选标是"离散"的（谁入选、谁触上限会跳变），朴素迭代容易来回震荡 →
# 用阻尼（新参数 = 旧 + λ×(新−旧)）把步长压小。
SLEEVE_DAMPING = _SLEEVE_DAMPING
#
# 2026-10-15 修正（用户实测发现）：**"袖全空"曾被误判成收敛**。
# 目标设到高于"当前持仓篮子模型"能支撑的水平时，第一轮 `_solve_stage1` 就会判定目标不可行
# 并回退到最大可行收益 —— 回退解是"各池全押最高收益标的"的角点组合，通常不给袖任何权重，
# 于是阶段二整段跳过、袖参数"没有变化"（delta=0）→ 循环宣布已收敛，把这个角点当成交付组合
# （收益低于用户设的目标、波动大幅抬升）。这个 bug 极其隐蔽：只要角点恰好残留一丝袖权重
# （高于阶段二的 1e-6 早退阈值），阶段二就会跑、delta 就非 0、循环就能继续，几轮后袖的 μ
# 被迭代上来、目标重新可行 —— 换句话说，**交付结果一度取决于那一丝权重是否恰好为零**
# （用户"加一个新标的就塌"的实测，就是它把那一丝抹平了）。
# 现在两道闸：① 回退轮不再参与收敛判定，且回退后按各袖自己的候选池重新播种（乐观初值）
# 再迭代一轮；② 每轮回退都记进 history（fallback/reseeded），全程都不可行时显式报告
# "目标在收敛模型下仍不可行、已按最大可行收益交付"。


def _sleeve_params_us(internal, internal_tickers):
    """阶段二选出的美股袖 → (μ, σ)；σ 用袖内相关性矩阵算真实组合波动（P3-b1 薄壳）。"""
    return _core_sleeve_params_us(internal, internal_tickers,
                                  us_internal=US_INTERNAL, us_internal_corr=US_INTERNAL_CORR)




# 袖 → (内部候选池, 内部相关性, 随机种子)：与阶段二同一套池子，供"空袖重新播种"使用。
# 种子沿用阶段二各自的取值（美股 19 / 日本 31 / 大中华 43），保证可复现。
_SLEEVE_POOL_SPECS = build_pool_specs(US_INTERNAL, US_INTERNAL_CORR, JP_INTERNAL,
                                       JP_INTERNAL_CORR, GC_INTERNAL, GC_INTERNAL_CORR)


# 袖 → 该袖"是否被解出"要看的结果字段（阶段二没跑就是 None）。
_SLEEVE_RESOLVED_FIELD = _SLEEVE_RESOLVED_FIELD




def _sleeve_reseed_params(key, rf):
    """某只袖"自己能达到的最优篮子"参数（P3-b1 薄壳：池表在调用时读取）。"""
    return _core_sleeve_reseed_params(key, rf, _SLEEVE_POOL_SPECS)


def _reseed_sleeves(sleeve, solved, rf, tol=1e-5):
    """把本轮没被解出的袖按候选池重新播种（P3-b2 薄壳：池表在调用时读取）。"""
    return _core_reseed_sleeves(sleeve, solved, rf, _SLEEVE_POOL_SPECS, tol=tol)






def _sleeve_failure_reason(key):
    """袖没被阶段二解出时的可诊断原因（P3-b2 薄壳：池表在调用时读取）。"""
    return _core_sleeve_failure_reason(key, _SLEEVE_POOL_SPECS)




def _undeliverable_warning(solved, sleeve):
    """本轮交付统计其实只是阶段一模型统计时的显式提示（P3-b2 薄壳：池表在调用时读取）。"""
    return _core_undeliverable_warning(solved, sleeve, _SLEEVE_POOL_SPECS)




def _sleeve_tangency(key, rf, cache=None):
    """袖"不加下限"的参考参数 (μ,σ) = 它自己的最大夏普篮子（P3-b1 薄壳：池表在调用时读取）。"""
    return _core_sleeve_tangency(key, rf, _SLEEVE_POOL_SPECS, cache=cache)


def _required_sleeve_mu(solved, tan_mu, target, dom_w, ov_w):
    """二分求让阶段一可达上限 ≥ 目标所需的最小统一袖内 μ（P3-b2 薄壳：池表在调用时读取）。"""
    return _core_required_sleeve_mu(solved, tan_mu, target, dom_w, ov_w, _SLEEVE_POOL_SPECS)


def _floor_next_params(solved, key, t, rf):
    """下限袖下一轮的假设参数（P3-b2 薄壳：池表与 _floor_seed 在调用时读取）。"""
    return _core_floor_next_params(solved, key, t, rf, _SLEEVE_POOL_SPECS,
                                   floor_seed_fn=_floor_seed)


def _floor_seed(key, t, rf):
    """下限对应的可执行篮子参数 (μ,σ)（P3-b2 薄壳：池表在调用时读取）。"""
    return _core_floor_seed(key, t, rf, _SLEEVE_POOL_SPECS)


def _compute_sleeve_floors(solved, assumed, target, rf, prev_floors, damping, dom_w, ov_w, cache):
    """回退轮之后重算袖内收益下限（P3-b2 薄壳：池表在调用时读取）。"""
    return _core_compute_sleeve_floors(solved, assumed, target, rf, prev_floors, damping,
                                       dom_w, ov_w, cache, _SLEEVE_POOL_SPECS)


# 阶段一"目标不可行 → 回退到最大可行收益"提示的公共标记：生产端（_solve_stage1）与过滤端
# （solve_portfolio）共用同一段文案，避免两处漂移。这类提示的数字随袖参数阻尼推进逐轮变化，
# 修复前会逐轮累积成 ~10 行（AGENTS.md §12 记为"已知噪声"）；solve_portfolio 现在合并成一条。
# ── 稳定告警码（P0 2026-10-16）──────────────────────────────────────────────────
# Swift 侧按 code 分类，不再匹配文案；文案可改，code 不变（新增只往表里加）。
WARNING_CODES = {
    "target.stage1_fallback": "阶段一按当前模型判定目标不可行 → 回退到最大可行收益（逐轮，已合并）",
    "target.infeasible": "组合层目标不可行：已按最大可行收益交付、收益低于目标",
    "target.budget_exhausted": "结论受迭代预算限制（目标未必真的不可行）",
    "target.sleeve_unreachable": "袖内下限口径够不到目标，且组合层复核后仍不可达",
    "sleeve.not_converged": "袖内固定点未完全收敛（已取误差最小的一轮）",
    "sleeve.undeliverable": "本轮交付统计不可交付（阶段二没给篮子，final 只是阶段一模型统计）",
    "sleeve.floor_infeasible": "袖内求解器给不出满足下限的篮子",
    "sleeve.floor_postcheck_failed": "袖内下限解后校验失败（不交付该篮子）",
    "sleeve.levels_lifted": "组合层复核抬升了袖内水平（该轮不是回退轮）",
    "sleeve.excluded_no_basket": "袖在阶段二给不出篮子：已按 0 权重剔除后重解（不拿模型统计当交付）",
    "sleeve.stage2_error": "袖内（美股核心）阶段二原因诊断",
    "sleeve.us_unavailable": "美股核心袖阶段二不可用，统计回退到阶段一",
    "active_cap.pool_conflict": "active 单标的上限与境内池权重冲突，已放宽",
    "active_cap.hit": "active 单标的被压到 10% 上限（合并成一条）",
    "summary.missing_weights": "自检：有权重没出现在结果清单里（映射漏项）",
    "summary.weight_sum_mismatch": "自检：结果清单权重合计 ≠ 1",
    "fund.available_none": "境内没有可申购基金候选",
    "test.ticker_skipped": "测试标的被跳过 / 不可用",
    "other": "未分类告警（新增文案时请补 code）",
}


# _Warn 位于模块头部的 import：D2 起用 core.warnings.CodedWarning 作为**唯一载体**
# （str 子类 + .code，构造签名 (message, code="other") 与旧类逐字相同）。
# JSON 序列化、字符串拼接与相等比较都与普通 str 完全一致 → 既有输出字节不变；
# Swift 侧改用加法式的 warning_items[].code 分类（warnings 保持字符串数组不变）。




def _test_ticker_skip_warning(ticker, reason):
    """「新标的测试」里某只标的被跳过时的告警（消息与旧文案逐字一致，只补 code）。"""
    return _Warn(f"测试标的 {ticker} 已跳过: {reason}", "test.ticker_skipped")


def _cap_hit_warning(cap_hits):
    """active 单标的上限命中提示（合并成一条，带稳定 code）。"""
    merged = {}
    for k, w, cap in cap_hits:
        merged[k] = max(merged.get(k, 0.0), float(w))
    return _Warn(
        f"active（个股/主动）类单标的上限 {AC.ACTIVE_CAP:.0%} 生效，以下标的被压到上限: "
        + "、".join(f"{k} {w:.2%}" for k, w in sorted(merged.items(), key=lambda kv: -kv[1])),
        "active_cap.hit")


_STAGE1_FALLBACK_MARK = _STAGE1_FALLBACK_MARK






def _solve_once(assets, corr_pairs, *, dom_w, ov_w, target, rf, extract, total_assets,
                log=None, stage1_starts=160, stage2_starts=80, warn=None, sleeve_floors=None,
                sleeve_exclude=None):
    """一轮完整求解：阶段一 → 阶段二（P3-b2 薄壳）。

    模块级**变量**（内部池表 / CAP_HITS）与 _solve_once 本身都在**调用时**读取 →
    既有 monkeypatch 语义不变；实现已搬进 core/engine.py。
    """
    return _core_engine.solve_once(
        assets, corr_pairs, dom_w=dom_w, ov_w=ov_w, target=target, rf=rf, extract=extract,
        total_assets=total_assets, universe=_universe(), cap_hits_out=CAP_HITS, log=log,
        stage1_starts=stage1_starts, stage2_starts=stage2_starts, warn=warn,
        sleeve_floors=sleeve_floors, sleeve_exclude=sleeve_exclude)


def _seed_levels_for_target(solved, tan_mu, target, dom_w, ov_w):
    """回退角点清零袖后的播种水平（P3-b2 薄壳：池表在调用时读取）。"""
    return _core_seed_levels_for_target(solved, tan_mu, target, dom_w, ov_w, _SLEEVE_POOL_SPECS)


def _sleeve_tangency_all(rf, cache):
    """全部袖的切点 μ（带缓存）（P3-b2 薄壳：池表在调用时读取）。"""
    return _core_sleeve_tangency_all(rf, cache, _SLEEVE_POOL_SPECS)


def _sleeve_levels_for_feasibility(solved, tan_mu, target, dom_w, ov_w):
    """组合层可达性复核（P3-b2 薄壳：池表在调用时读取）。"""
    return _core_sleeve_levels_for_feasibility(solved, tan_mu, target, dom_w, ov_w,
                                               _SLEEVE_POOL_SPECS)


def solve_portfolio(assets, corr_pairs, *, dom_w, ov_w, target, rf, extract, total_assets,
                    warnings=None, log=None, max_sleeve_iter=MAX_SLEEVE_ITER, sleeve_tol=SLEEVE_TOL,
                    stage1_starts=160, stage2_starts=80, sleeve_start=None,
                    damping=SLEEVE_DAMPING):
    """完整求解（袖内参数固定点迭代）；实现已搬进 core/engine.py（P3-b2）。

    薄壳在**调用时**读取模块级变量与五个可打桩的函数（_solve_once / _sleeve_params_us /
    _compute_sleeve_floors / _seed_levels_for_target / _floor_seed）→ 既有测试的函数级打桩
    继续生效（经 SleeveHooks 显式注入）。
    """
    return _core_engine.solve_portfolio(
        assets, corr_pairs, dom_w=dom_w, ov_w=ov_w, target=target, rf=rf, extract=extract,
        total_assets=total_assets, universe=_universe(), cap_hits_out=CAP_HITS,
        warnings=warnings, log=log, max_sleeve_iter=max_sleeve_iter, sleeve_tol=sleeve_tol,
        stage1_starts=stage1_starts, stage2_starts=stage2_starts, sleeve_start=sleeve_start,
        damping=damping,
        hooks=SleeveHooks(solve_once=_solve_once, sleeve_params_us=_sleeve_params_us,
                          compute_sleeve_floors=_compute_sleeve_floors,
                          seed_levels_for_target=_seed_levels_for_target,
                          floor_seed=_floor_seed))


def _inject_test_assets(args, assets, work_dir, warnings):
    """新标的测试：临时标的作为新增资产注入第一阶段（在可投性过滤之后，不受其约束）。

    返回 (assets', test_corr_pairs, test_skipped, test_estimates)。
    全部标的不可用时打印错误 JSON 并以退出码 3 结束（与原 main 内联行为一致）。
    """
    test_estimates, test_skipped = [], []
    if not args.test_tickers:
        return assets, None, test_skipped, test_estimates
    test_assets, test_corr_pairs, test_skipped, test_estimates = resolve_test_assets(
        args.test_tickers,
        work_dir,
        existing_keys={a["key"] for a in assets},
        internal_keys=set(US_INTERNAL.keys()),
        # 相关性要覆盖全量大类 (不只是本次可投的那几个): 基准对比用
        # D_HSBC_SP500 / D_GREATER_CN 算组合 Beta, 缺了这些行 Beta 会被低估。
        corr_keys={a["key"] for a in BROAD_ASSETS},
        test_class=args.test_class,
        log=lambda msg, level="info": log_step(
            "test_tickers", msg, level=level, log_path=args.log_path),
    )
    for t, reason in test_skipped:
        warnings.append(_test_ticker_skip_warning(t, reason))
    # 测试标的没有登记先验：broad/strategy 会退化成纯历史，必须显式说明口径不同
    for e in test_estimates:
        if e.get("mu_note") == "history_only":
            warnings.append(
                f"测试标的 {e['ticker']} 标为「{AC.CLASS_RULES[e['test_class']]['label']}」，"
                f"但该类的人工先验只对登记在册的标的有效；测试标的使用纯历史年化 "
                f"μ={e['mu']:.2%}（组合内同类标的的口径是"
                f"{AC.CLASS_RULES[e['test_class']]['mu_hist_weight']:.0%} 历史 + "
                f"{1-AC.CLASS_RULES[e['test_class']]['mu_hist_weight']:.0%} 先验，两者不可直接横比）。")
    if not test_assets:
        detail = "; ".join(f"{t}: {r}" for t, r in test_skipped) or "无有效标的"
        print(json.dumps(
            {"error": "所有测试标的均不可用", "detail": detail, "warnings": warnings},
            ensure_ascii=False))
        sys.exit(3)
    assets = assets + test_assets
    for a in test_assets:
        if a["pool"] == "domestic":
            warnings.append(
                f"测试标的 {a['name']} 按境内池约束参与优化 (what-if 口径, "
                f"不经汇丰在售校验, 也不出现在境内基金执行清单)。")
    log_step(
        "test_tickers",
        f"新增 {len(test_assets)} 个测试资产: "
        + ", ".join(a["key"] for a in test_assets),
        log_path=args.log_path)
    return assets, test_corr_pairs, test_skipped, test_estimates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("extract_json")
    parser.add_argument("--total-assets", type=float, default=None)
    parser.add_argument(
        "--target-return",
        type=float,
        default=None,
        help="目标预期收益率覆盖 (如 0.20=20%%)；缺省用 params.TARGET_RETURN",
    )
    parser.add_argument(
        "--test-tickers",
        dest="test_tickers",
        default=None,
        help="逗号分隔的临时测试标的 (如 AAPL,0700.HK,510300)；"
             "按 --test-class 指定的类别估算 μ/σ 与相关性，作为新增资产参与优化 (T_ 前缀)；"
             "6 位纯数字按境内池处理。",
    )
    parser.add_argument(
        "--test-class",
        dest="test_class",
        default="active",
        choices=["broad", "strategy", "active"],
        # argparse 的 help 走 %-格式化，说明文字里的 % 必须转义成 %%
        help=AC.CLASS_PARAM_HELP.replace("%", "%%"),
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="联网管线临时文件目录 (hsbc_funds.json / availability.json)；缺省用系统临时目录共享目录",
    )
    parser.add_argument("--check-timeout", type=float, default=300.0,
                        help="申赎状态校验子进程总超时秒数")
    parser.add_argument("--hsbc-timeout", type=float, default=60.0,
                        help="基金在售列表抓取单请求超时秒数")
    parser.add_argument("--request-timeout", type=float, default=12.0,
                        help="申赎状态单只请求超时秒数")
    parser.add_argument("--json", dest="out_path", help="Write detailed JSON output to file")
    parser.add_argument("--result-json", dest="result_path", help="Write clean summary JSON to file")
    parser.add_argument("--log", dest="log_path", help="JSONL step log path")
    args = parser.parse_args()

    # App 侧滑块传入的目标收益率覆盖 params.py 的默认值.
    # TARGET_RETURN 是模块级全局, 下方 stage1/stage2 的约束 lambda 在调用时
    # 按全局名查找, 此处重绑后全部生效.
    global TARGET_RETURN
    if args.target_return is not None:
        TARGET_RETURN = float(args.target_return)
        log_step("target_return", f"目标收益率覆盖为 {TARGET_RETURN:.0%}", log_path=args.log_path)

    extract = load_extract(args.extract_json)
    log_step("load_extract", f"已加载提取结果 total={extract.get('total_value')}", log_path=args.log_path)

    # RF (无风险利率) 动态注入: App 侧按池比例加权中美10年国债收益率算出 rf,
    # 通过 extract_live.json 的 "rf" 字段传入（params.py 里不写死利率）。
    # RF 是模块级全局, 同 TARGET_RETURN 模式: global 重绑后约束 lambda 生效.
    global RF
    if extract.get("rf") is not None:
        RF = float(extract["rf"])
        log_step("rf", f"无风险利率覆盖为 {RF:.2%} (动态中美10年国债加权)", log_path=args.log_path)
    else:
        log_step("rf", "提取文件未带 rf，本次夏普按 rf=0 计算", log_path=args.log_path)

    # 提取阶段的提示由 `pm-cli extract` 当场打印；**不再**从提取文件里转发到结果页 ——
    # 提取文件是缓存，转发的旧提示会在改口径后长期滞留（2026-10-14 实测：去掉的两条
    # 资金池口径提示仍从本地缓存的 extract_app.json 里冒出来）。
    warnings = []
    CAP_HITS.clear()
    # 参数口径的提示（缺行情用人工兜底、σ 毛刺丢弃、相关性兜底…）一并带给结果页
    warnings.extend(CALIBRATION_WARNINGS)

    # live_anchors: 用户实际持有的境内基金代码 (从 extract_live.json domestic_holdings 提取),
    # 用 classify_domestic_fund(name) 归类, 覆盖 params.py 写死的 fund_code 作为各类锚定.
    # 效果: select_domestic_funds 优先保留用户已持有基金而非推荐换仓到同类最低费率基金.
    live_anchors = live_anchors_from_extract(extract)
    if live_anchors:
        log_step("live_anchors", f"动态锚定 {len(live_anchors)} 个境内基金 (覆盖 params.py 静态锚定)", log_path=args.log_path)

    # ---- 联网管线: 每次优化完整执行 (抓汇丰搜索易 → 天天基金实时校验 → 开放池构建) ----
    # 产物写入 work_dir 临时文件 (hsbc_funds.json / availability.json / check_plan.json),
    # 敏感性分析复用同一目录, 不重新联网; 优化器关闭时由 Swift 侧整体删除。
    work_dir = ensure_work_dir(args.work_dir)
    try:
        hsbc_data, availability = ensure_pipeline(
            work_dir,
            live_anchors=live_anchors,
            hsbc_request_timeout=args.hsbc_timeout,
            request_timeout=args.request_timeout,
            check_total_timeout=args.check_timeout,
            log=lambda step, message, level="info": log_step(
                step, message, level=level, log_path=args.log_path),
        )
    except RuntimeError as exc:
        print(json.dumps(
            {"error": "联网管线失败（基金在售/申赎状态数据源）", "detail": str(exc)},
            ensure_ascii=False))
        sys.exit(2)
    avail_funds = availability["funds"]
    hsbc_funds_live = hsbc_data.get("funds", [])
    # 汇丰原始在售状态, 供口径冲突提示 (汇丰开放 vs 天天基金暂停)
    hsbc_status = {str(f.get("code", "")).strip(): f for f in hsbc_funds_live}

    # 直接取分组早停校验 selection 中每类选中的开放代表基金
    # (组内顺序 = 锚定优先 → 费率升序 → A/C 备选垫后; 无数据的互认基金已按汇丰口径兜底)
    hsbc_pool, pool_meta = build_open_pool_from_status(
        hsbc_funds_live, availability, live_anchors=live_anchors)
    # 基金数据源的抓取/校验计数只写进运行日志（结果页不再展示管线遥测，2026-10-14 用户定稿）
    stats = availability.get("stats", {})
    log_step("pipeline_counts",
             f"在售 {hsbc_data.get('total_count', '?')} 只 / 开放申购 {hsbc_data.get('open_count', '?')} 只；"
             f"实时校验请求 {stats.get('checked', '?')} 只 / 开放 {availability.get('open_count', '?')} 只 / "
             f"兜底 {stats.get('hsbc_fallback', 0)} 只",
             log_path=args.log_path)
    log_step("build_open_pool",
             f"开放基金池：{len(hsbc_pool)} 个类别、代表基金 {pool_meta['kept']} 只"
             f"（{stats.get('checked', '?')} 只候选实时校验, 耗时 {availability.get('elapsed_seconds', '?')}s）",
             log_path=args.log_path)

    dom_w = float(extract["domestic_weight"])
    ov_w = float(extract["overseas_weight"])

    internal_weights = {h["ticker"]: h["weight"] for h in extract["us_equity"]["holdings"]}
    core_mu, core_vol = us_core_params(internal_weights)

    assets = build_investable_assets(
        hsbc_pool, pool_meta, avail_funds, hsbc_status, warnings,
        core_mu=core_mu, core_vol=core_vol, drop_note="已从第一阶段剔除")
    log_step("build_assets", f"构建大类资产 {len(assets)} 项", log_path=args.log_path)
    if not any(a["pool"] == "domestic" for a in assets):
        warnings.append(_Warn("没有可申购的境内基金", "fund.available_none"))
        print(
            json.dumps(
                {"error": "没有可申购的境内基金", "warnings": warnings,
                 "warning_items": _warning_items(warnings)},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    # ---- 新标的测试: 临时标的作为新增资产注入第一阶段 (在可投性过滤之后, 不受其约束) ----
    assets, test_corr_pairs, test_skipped, test_estimates = _inject_test_assets(
        args, assets, work_dir, warnings)

    # 缺 μ/σ 的标的（校准文件里没有它、也没有写死值可用）不能进优化：显式剔除并报警，
    # 不静默用 0 顶上（0 波动会直接霸占权重）。σ 已无人工下限，所以这类标的只能缺席。
    unusable = [a["key"] for a in assets if a.get("mu") is None or a.get("vol") is None]
    if unusable:
        warnings.append(
            "以下标的取不到 μ/σ（校准文件里没有、也没有写死值），本次不参与优化: " + "、".join(unusable))
        assets = [a for a in assets if a.get("mu") is not None and a.get("vol") is not None]

    # ---- 完整求解：阶段一（大类）+ 袖内固定点迭代（2026-10-14）----
    # 袖内 μ/σ 迭代到"阶段一假设 == 阶段二交付"的固定点，因此下面报告的 final
    # 就是阶段一那条约束（收益 ≥ 目标）真正作用到的组合。
    total_assets = args.total_assets or float(extract["total_value"])
    solved_all = solve_portfolio(
        assets,
        {**BROAD_CORR, **(test_corr_pairs if args.test_tickers else {})},
        dom_w=dom_w, ov_w=ov_w, target=TARGET_RETURN, rf=RF,
        extract=extract, total_assets=total_assets, warnings=warnings,
        log=lambda step, msg, level="info": log_step(step, msg, level=level, log_path=args.log_path),
    )
    if solved_all is None:
        print(json.dumps({"error": "Optimization infeasible", "warnings": warnings}, ensure_ascii=False, indent=2))
        sys.exit(1)

    assets = solved_all["assets"]
    keys, mu, vols, cov = solved_all["keys"], solved_all["mu"], solved_all["vols"], solved_all["cov"]
    w1 = solved_all["stage1_weights"]
    ret1, vol1, sharpe1 = solved_all["stage1_ret"], solved_all["stage1_vol"], solved_all["stage1_sharpe"]
    target = solved_all["target"]
    TARGET_RETURN = target          # 回退后的靶值要留给后续（历史 Bug D1）
    cap_map = solved_all["cap_map"]
    internal = solved_all["internal"]
    internal_tickers = solved_all["internal_tickers"]
    stage2_error = solved_all["stage2_error"]
    japan_selection = solved_all["japan_selection"]
    gc_selection = solved_all["gc_selection"]
    final = solved_all["final"]
    log_step("stage1_result", f"第一阶段完成 收益={ret1:.2%} 波动={vol1:.2%} 夏普={sharpe1:.3f}",
             log_path=args.log_path)
    log_step("stage2_internal", "第二阶段：美国权益内部标的优化", log_path=args.log_path)

    domestic_selection = select_domestic_funds(w1, assets, hsbc_pool, total_assets)
    log_step("select_domestic", "境内基金选择完成", log_path=args.log_path)

    target_gap = None
    if target is not None and final is not None:
        target_gap = float(final.get("ret", 0.0)) - float(target)
        if target_gap > 0.002:
            warnings.append(
                f"目标收益 {target:.2%} 是收益**下限**（不是锚）：求解目标是最大化夏普，"
                f"实际交付 {final.get('ret', 0.0):.2%}（高出 {target_gap:.2%}）。"
                f"目标低于切点收益时结果不随滑杆变化，把目标提到切点收益以上才会沿前沿上移。")

    stage1_detail = {
        "weights": {keys[i]: float(w1[i]) for i in range(len(keys)) if w1[i] > 1e-6},
        "ret": ret1,
        "vol": vol1,
        "sharpe": sharpe1,
        "floor": ret1 - 1.96 * vol1,
    }

    output = {
        "pool": {
            "mode": extract["pool_mode"],
            "domestic_weight": dom_w,
            "overseas_weight": ov_w,
            "domestic_value": float(extract["domestic_value"]),
            "overseas_value": float(extract["overseas_value"]),
            "total_assets_used": total_assets,
        },
        "us_core_aggregate": {
            "mu": core_mu,
            "vol": core_vol,
            "internal_from_file": internal_weights,
        },
        "pipeline": {
            "work_dir": str(work_dir),
            "hsbc": {
                "fetched_at": hsbc_data.get("fetched_at"),
                "total_count": hsbc_data.get("total_count"),
                "open_count": hsbc_data.get("open_count"),
            },
            "availability": {
                "checked_at": availability.get("checked_at"),
                "checked_codes": len(avail_funds),
                "open_count": availability.get("open_count"),
                "hsbc_fallback": availability.get("stats", {}).get("hsbc_fallback"),
                "elapsed_seconds": availability.get("elapsed_seconds"),
            },
            "open_pool": {
                "categories": len(hsbc_pool),
                "representatives": pool_meta["kept"],
                "category_detail": pool_meta["categories"],
            },
        },
        "availability": {
            "source": availability.get("source"),
            "base_url": availability.get("base_url"),
            "checked_at": availability.get("checked_at"),
            "funds": avail_funds,
        },
        "stage1": stage1_detail,
        "sleeve": {
            "params": solved_all["sleeve_params"],
            "iterations": solved_all["sleeve_iterations"],
            "converged": solved_all["sleeve_converged"],
            "history": solved_all["sleeve_history"],
        },
        "stage2": None,
        "domestic_selection": domestic_selection,
        "japan_selection": japan_selection,
        "gc_selection": gc_selection,
        "final": final,
        "target_return": TARGET_RETURN,
        "target_gap": target_gap,
        "objective": "max_sharpe",
        # 加法式：结果统计来源（delivered = 真交付；stage1_model = 不可交付的模型统计）
        "deliverable": bool(solved_all.get("deliverable", True)),
        "final_source": solved_all.get("final_source", "delivered"),
        "warnings": warnings,
    }

    if CAP_HITS:
        warnings.append(_cap_hit_warning(CAP_HITS))

    if args.test_tickers:
        output["test"] = {
            "tickers": parse_test_tickers(args.test_tickers),
            "test_class": args.test_class,
            "class_help": AC.CLASS_PARAM_HELP,
            "assets": test_estimates,
            "skipped": [{"ticker": t, "reason": r} for t, r in test_skipped],
            # 测试标的与各大类的相关性: 基准对比要用它把测试标的并入协方差,
            # 否则 Beta/Alpha 会因为权重质量不足 1 而失真 (2026-10-13 修)。
            "corr": [[i, j, float(v)] for (i, j), v in test_corr_pairs.items()],
        }

    if internal is not None:
        x_internal, ret2, vol2, sharpe2, w2, internal_tickers = internal
        internal_total = float(x_internal.sum())
        output["stage2"] = {
            "internal_total_weight": internal_total,
            "internal_weights_total": {t: float(x_internal[i]) for i, t in enumerate(internal_tickers)},
            "internal_weights_normalized": {t: float(x_internal[i]) / internal_total for i, t in enumerate(internal_tickers)},
            "amounts": {t: float(x_internal[i]) * total_assets for i, t in enumerate(internal_tickers)},
            "ret": ret2,
            "vol": vol2,
            "sharpe": sharpe2,
            "floor": ret2 - 1.96 * vol2,
        }
        if stage2_error:
            output["warnings"].append(stage2_error)

    log_step("finalize", f"优化完成 共{len(assets)}大类 夏普={sharpe1:.3f}", log_path=args.log_path)
    # 加法式告警明细必须在**所有** append 之后重算（CAP_HITS 等是后面才追加的），
    # 否则 warnings 与 warning_items 条数会不一致。
    output["warning_items"] = _warning_items(warnings)

    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))

    if args.result_path:
        summary = build_result_summary(output, hsbc_pool=hsbc_pool)
        if args.out_path:
            summary["source_detail"] = args.out_path
        with open(args.result_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
