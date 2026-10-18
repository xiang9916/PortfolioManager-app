#!/usr/bin/env python3
"""Two-stage portfolio optimization."""

import argparse
import json
import os as _os
import sys
# P4（2026-10-17）：包已上提到 Optimizer/portfolio_system（scripts 的**兄弟目录**）——
# 这里把包根（= scripts 的父目录）加进 sys.path。bundle 内目录同构，路径同样成立。
_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

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
from portfolio_system.callers.new_asset_runner import (  # P3-b3 步骤③
    NewAssetRunner, TestAssetsUnavailable)
# P3-b3 步骤②：结果装配（summary / benchmark）已搬进 callers.preset_runner。
# P4 收尾：选基规则 / 结果载荷装配 / 完整编排也已搬进 callers（main 只剩 argparse→Runner→写文件）。
from portfolio_system.callers.preset_runner import (  # noqa: F401
    InfeasibleOptimization, NoDomesticAssets, PipelineUnavailable, PresetDeps, PresetRunner,
    SummaryTables, build_summary, select_domestic_funds)
from portfolio_system.callers.base_runner import RunRequest
from portfolio_system.services.portfolio_source import load_extract
from test_tickers import parse_test_tickers, resolve_test_assets
# 持仓纳入（Q1）：classify/inject 实现（读 params / market_data 候选池表）在 scripts 层。
import held_assets as _held_assets
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


# 袖内收益下限的绝对容差（P1）：SLSQP 末位噪声 + 离散选标。
# ── 容差的唯一来源（2026-10-17 收口）────────────────────────────────────────────
# P2（2026-10-17）：容差常量的唯一来源已搬进 portfolio_system/core/constants.py，
# 这里只做再导出，保持既有 import 路径与名字不变（不许再出现字面量）。
from portfolio_system.core.constants import (  # noqa: E402  P3-b3：容差唯一来源在 core
    CHECK_SUM_EPS, DELIVERY_EPS, RESEED_TOL, SLEEVE_FLOOR_EPS, SLEEVE_ROOM_EPS,
    TINY_WEIGHT_EPS, WEIGHT_ZERO_EPS)
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


def _summary_tables():
    """结果 summary 要用的外部数据表（**调用时**读模块级变量 → 变量打桩仍兼容）。"""
    return SummaryTables(
        broad_assets=BROAD_ASSETS, broad_corr=BROAD_CORR, us_internal=US_INTERNAL,
        jp_internal=JP_INTERNAL, gc_internal=GC_INTERNAL, build_cov=build_broad_cov)


def build_result_summary(output, hsbc_pool=None):
    """结果 summary（P3-b3 步骤②薄壳）：实现已搬进 callers.preset_runner.build_summary。

    RF 是模块级全局（App 从 extract 注入），薄壳在**调用时**取 → 既有打桩语义不变。
    """
    return build_summary(output, tables=_summary_tables(), rf=RF, hsbc_pool=hsbc_pool)



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


def _reseed_sleeves(sleeve, solved, rf, tol=RESEED_TOL):
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
    "held.skipped": "持仓不在候选池且无法纳入优化（逐只，原因见明细）",
    "held.us_equity_outside_catalog": "美股权益持仓不在内部标的目录：按其顶层 what-if 腿纳入、不计入 O_US_CORE 袖",
    "sensitivity.anchor_mismatch": "敏感性锚点目标点的结果被主结果支配（Q2 锚定）",
    "other": "未分类告警（新增文案时请补 code）",
}


# _Warn 位于模块头部的 import：D2 起用 core.warnings.CodedWarning 作为**唯一载体**
# （str 子类 + .code，构造签名 (message, code="other") 与旧类逐字相同）。
# JSON 序列化、字符串拼接与相等比较都与普通 str 完全一致 → 既有输出字节不变；
# Swift 侧改用加法式的 warning_items[].code 分类（warnings 保持字符串数组不变）。




def _test_ticker_skip_warning(ticker, reason):
    """「新标的测试」里某只标的被跳过时的告警（消息与旧文案逐字一致，只补 code）。"""
    return _Warn(f"测试标的 {ticker} 已跳过: {reason}", "test.ticker_skipped")


def _held_skip_warning(ticker, reason):
    """持仓纳入（Q1）：某只不在候选池的持仓无法纳入优化（契约 §1.4 的消息格式）。"""
    return _Warn(f"持仓标的 {ticker} 未纳入优化: {reason}", "held.skipped")


def _us_equity_outside_catalog_warning(tickers):
    """us_equity 类持仓不在内部标的目录时的提示（契约 §1.4）。

    这类持仓不会进 O_US_CORE 袖（袖内候选只来自 US_INTERNAL 目录），而是按各自的顶层
    what-if 腿纳入组合模型 —— 必须显式说明，否则用户会以为它仍被袖内优化考虑。
    """
    names = "、".join(str(t) for t in tickers)
    return _Warn(
        f"美股权益持仓 {names} 不在内部标的目录中：已按各自的顶层 what-if 腿纳入优化"
        f"（test_class=active，受 {AC.ACTIVE_CAP:.0%} 单标的上限），不计入 O_US_CORE 袖。",
        "held.us_equity_outside_catalog")


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


#: P3-b3 步骤③：新标的注入的实现已搬进 callers.NewAssetRunner（本模块只留薄壳）。
_NEW_ASSET = NewAssetRunner(optimizer=None)


def _inject_test_assets(args, assets, work_dir, warnings):
    """新标的测试：临时标的作为新增资产注入第一阶段（P3-b3 薄壳）。

    实现见 callers/new_asset_runner.NewAssetRunner.inject_test_assets；本薄壳只保留
    **CLI 契约**：全部测试标的不可用 → 打印错误 JSON 并以退出码 3 结束。
    解析器 / 口径表 / 告警工厂由这里（装配层）注入 —— 联网与 asset_classes 属上层。
    """
    try:
        return _NEW_ASSET.inject_test_assets(
            assets,
            raw_tickers=args.test_tickers,
            test_class=args.test_class,
            work_dir=work_dir,
            resolve=resolve_test_assets,
            internal_keys=US_INTERNAL,
            corr_keys=[a["key"] for a in BROAD_ASSETS],
            class_rules=AC.CLASS_RULES,
            skip_warning=_test_ticker_skip_warning,
            warnings=warnings,
            log=lambda msg, level="info": log_step(
                "test_tickers", msg, level=level, log_path=args.log_path),
        )
    except TestAssetsUnavailable as exc:
        print(json.dumps(
            {"error": "所有测试标的均不可用", "detail": exc.detail, "warnings": warnings},
            ensure_ascii=False))
        sys.exit(3)


def _parse_args(argv=None):
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
    return parser.parse_args(argv)


def _preset_deps():
    """装配层职责：把 params / asset_classes / services 的依赖**显式注入** callers。

    这里保留的模块级名字（CAP_HITS / 各内部池 / 薄壳函数）在**调用时**解析，
    因此既有 monkeypatch 语义不变（测试直接替换 OP.* 即可）。
    """
    return PresetDeps(
        calibration_warnings=CALIBRATION_WARNINGS,
        us_core_params=us_core_params,
        build_investable_assets=build_investable_assets,
        build_open_pool=build_open_pool_from_status,
        ensure_pipeline=ensure_pipeline,
        ensure_work_dir=ensure_work_dir,
        live_anchors_from_extract=live_anchors_from_extract,
        inject_test_assets=_NEW_ASSET.inject_test_assets,
        resolve_test_assets=resolve_test_assets,
        internal_keys=tuple(US_INTERNAL),
        corr_keys=[a["key"] for a in BROAD_ASSETS],
        class_rules=AC.CLASS_RULES,
        test_skip_warning=_test_ticker_skip_warning,
        solve_portfolio=solve_portfolio,
        broad_corr=BROAD_CORR,
        parse_test_tickers=parse_test_tickers,
        class_param_help=AC.CLASS_PARAM_HELP,
        cap_hits=CAP_HITS,
        cap_hit_warning=_cap_hit_warning,
        warning_items_fn=_warning_items,
        # 持仓纳入（Q1）：实现是 scripts/held_assets.py 的两个函数；告警工厂在这里，
        # callers 只经 PresetDeps 调用（不得 import 上层脚本）。
        inject_held_assets=_held_assets.inject_held_assets,
        classify_held_assets=_held_assets.classify_held_assets,
        held_skip_warning=_held_skip_warning,
        us_equity_outside_catalog_warning=_us_equity_outside_catalog_warning,
    )


def _write_outputs(output, args, hsbc_pool=None):
    """写结果页 JSON / App 用的 summary JSON（唯一落盘处）。"""
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


def main():
    """CLI 入口（ADR 0003 §5）：argparse → RunRequest → PresetRunner → 写 JSON。

    编排（管线 / 装配 / 求解 / 汇总）全部在 callers.preset_runner；本函数只保留
    装配层职责：参数解析、全局口径注入（RF / TARGET_RETURN）、错误码映射、落盘。
    """
    args = _parse_args()

    # App 侧滑块传入的目标收益率覆盖 params.py 的默认值.
    # TARGET_RETURN 是模块级全局, 薄壳在**调用时**按全局名查找, 此处重绑后全部生效.
    global TARGET_RETURN
    if args.target_return is not None:
        TARGET_RETURN = float(args.target_return)
        log_step("target_return", f"目标收益率覆盖为 {TARGET_RETURN:.0%}", log_path=args.log_path)

    extract = load_extract(args.extract_json)
    log_step("load_extract", f"已加载提取结果 total={extract.get('total_value')}", log_path=args.log_path)

    # RF (无风险利率) 动态注入: App 侧按池比例加权中美10年国债收益率算出 rf,
    # 通过 extract_live.json 的 "rf" 字段传入（params.py 里不写死利率）。
    global RF
    if extract.get("rf") is not None:
        RF = float(extract["rf"])
        log_step("rf", f"无风险利率覆盖为 {RF:.2%} (动态中美10年国债加权)", log_path=args.log_path)
    else:
        log_step("rf", "提取文件未带 rf，本次夏普按 rf=0 计算", log_path=args.log_path)

    warnings = []
    CAP_HITS.clear()

    request = RunRequest(
        assets=(),
        target_return=TARGET_RETURN,
        rf=RF,
        domestic_weight=float(extract["domestic_weight"]),
        overseas_weight=float(extract["overseas_weight"]),
        total_assets=args.total_assets or float(extract["total_value"]),
        extract=extract,
        deps=_preset_deps(),
        test_tickers=args.test_tickers,
        test_class=args.test_class,
        work_dir=args.work_dir,
        hsbc_timeout=args.hsbc_timeout,
        request_timeout=args.request_timeout,
        check_timeout=args.check_timeout,
        log=lambda step, message, level="info": log_step(
            step, message, level=level, log_path=args.log_path),
        warnings_out=warnings,
    )
    try:
        outcome = PresetRunner(optimizer=None).run(request)
    except PipelineUnavailable as exc:
        print(json.dumps(
            {"error": "联网管线失败（基金在售/申赎状态数据源）", "detail": str(exc)},
            ensure_ascii=False))
        sys.exit(2)
    except NoDomesticAssets:
        print(json.dumps(
            {"error": "没有可申购的境内基金", "warnings": warnings,
             "warning_items": _warning_items(warnings)},
            ensure_ascii=False,
        ))
        sys.exit(1)
    except TestAssetsUnavailable as exc:
        print(json.dumps(
            {"error": "所有测试标的均不可用", "detail": exc.detail, "warnings": warnings},
            ensure_ascii=False))
        sys.exit(3)
    except InfeasibleOptimization:
        print(json.dumps({"error": "Optimization infeasible", "warnings": warnings},
                         ensure_ascii=False, indent=2))
        sys.exit(1)

    output = outcome.payload
    TARGET_RETURN = output["target_return"]   # 回退后的靶值要留给后续（历史 Bug D1）
    _write_outputs(output, args, hsbc_pool=outcome.context.get("hsbc_pool"))


if __name__ == "__main__":
    main()
