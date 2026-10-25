#!/usr/bin/env python3
"""Sensitivity analysis: step target return from 5% to max feasible, 1% increments.

Outputs JSON array of points, each with:
  target_return, feasible (bool), achieved_return, volatility, sharpe, worst_year_95

基金口径与 optimize_portfolio.py 完全一致: 复用优化运行留在 work dir 的
临时文件 (hsbc_funds.json + availability.json) 构建开放基金池并剔除
暂停申购的大类 — 敏感性分析不再重复联网; 临时文件缺失/过期时才全量重跑管线。
"""

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

# Add scripts dir to path so params can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))
# P4：包根 = scripts 的父目录（bundle 内目录同构）。
_PKG_ROOT = str(Path(__file__).resolve().parent.parent)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import math

import numpy as np

from portfolio_system.core.constants import (  # noqa: E402
    DELIVERY_EPS,
    SCAN_LOOP_EPS as _SCAN_LOOP_EPS,
    SCAN_TARGET_MARGIN_EPS as _SCAN_MARGIN,
    TARGET_REACH_EPS)
try:
    from scipy import integrate, stats
except ImportError:
    print("scipy required for sensitivity analysis.", file=sys.stderr)
    sys.exit(2)

from params import (
    BROAD_CORR, RF,
    GC_INTERNAL, GC_INTERNAL_CORR,
    JP_INTERNAL, JP_INTERNAL_CORR,
    US_INTERNAL, US_INTERNAL_CORR,
    build_broad_cov,
    build_open_pool_from_status,
    us_core_params,
)
from optimize_common import (
    max_feasible_return,
    stage1_bounds,
)
# 完整求解（阶段一 + 袖内固定点 + 阶段二）：与结果页同源，否则敏感性只扫了阶段一。
# P3-b3（步骤④）：改经 callers.SensitivityRunner 直连 core/engine —— 本脚本不再依赖
# optimize_portfolio（模块级与调用期都不 import；P3 的正式门）。
from portfolio_system.callers.sensitivity_runner import (  # noqa: E402
    SensitivityRunner, anchor_mismatch_warning)
from portfolio_system.core.fixpoint import restart_seed_params  # noqa: E402
from portfolio_system.core.sleeve_params import (  # noqa: E402
    SLEEVE_KEYS, SleeveUniverse, build_pool_specs)
from portfolio_system.core.stage2 import (  # SP500 内部相关性：pure data，唯一来源在 core
    SP500_INTERNAL_CORR, SP500_INTERNAL_CORR_DEFAULT)
# P3-b3：基金状态流水线改走 services（唯一子进程入口）；脚本层不再直接 import fund_pipeline。
from portfolio_system.services.pipeline import (
    build_investable_assets,
    ensure_pipeline,
    ensure_work_dir,
    live_anchors_from_extract,
    load_pipeline,
)
from portfolio_system.services.portfolio_source import load_extract

# ── 敏感性锚定（契约 §2.2，2026-10-18 Q2）──────────────────────────────────────
#: 锚点目标在网格里的判定容差（网格目标四舍五入到 4 位 → 5e-5 足以覆盖半格）。
_ANCHOR_TARGET_EQ_EPS = 5e-5
#: 锚点一致性的判定值（Lead 2026-10-18 收口）：锚点目标点必须与主结果**逐位一致**。
#: 点上统计先 round 到 6 位，主结果的 ret/vol 是原始浮点 → 取 1e-6 覆盖「六位小数相同」
#: 的舍入误差（比 1e-9 稳健，又仍远小于任何有意义的收益/波动差）。
_ANCHOR_MATCH_TOL = 1e-6
#: 低目标档的确定性候选 λ（契约 §2.2 固定值 0.20）。
_ANCHOR_SEED_LAMBDA = 0.20
#: 边界带宽度（Lead 2026-10-18 v5）：切点估计以上 2pp 内、且 ≤ anchor_target 的点
#: 改用 **prev 起跑**（anchor 起跑在该带每点打满 10 轮未收敛 ~30s；D4 实测 3 点 105s；
#: prev 起跑 ~4s）。失败才做一次独立 anchor（无袖参数则 seed）重试。
_ANCHOR_BOUNDARY_BAND = 0.02
#: 候选包络的判等容差：Sharpe 已 round 到 6 位，1e-6 覆盖舍入误差。
ENVELOPE_EPS = 1e-6


def _sleeve_universe():
    """把 params 的内部池表装配成 core 的显式入参（core 不 import params）。

    与 optimize_portfolio._universe() 同构；pool_specs 走 core 的唯一构造器
    build_pool_specs（袖种子只此一份）。
    """
    return SleeveUniverse(
        us_internal=US_INTERNAL, us_internal_corr=US_INTERNAL_CORR,
        jp_internal=JP_INTERNAL, jp_internal_corr=JP_INTERNAL_CORR,
        gc_internal=GC_INTERNAL, gc_internal_corr=GC_INTERNAL_CORR,
        sp500_internal_corr=SP500_INTERNAL_CORR,
        sp500_internal_corr_default=SP500_INTERNAL_CORR_DEFAULT,
        pool_specs=build_pool_specs(US_INTERNAL, US_INTERNAL_CORR, JP_INTERNAL,
                                    JP_INTERNAL_CORR, GC_INTERNAL, GC_INTERNAL_CORR),
        internal_pool_keys=tuple(US_INTERNAL) + tuple(JP_INTERNAL) + tuple(GC_INTERNAL))


def _stderr_log(step, message, level="info"):
    print(f"[{step}] {message}", file=sys.stderr)


def load_extract_assets(extract, work_dir, hsbc_request_timeout=60.0,
                        request_timeout=12.0, check_total_timeout=300.0, log=None):
    """复用/重建联网管线, 按优化同一口径构建 assets 与开放基金池。

    返回 (assets, hsbc_pool, warnings, pipeline_info, held_meta)。

    第 5 元 held_meta 是**持仓纳入**（契约 §1.3/§1.4，2026-10-18 Q1）的加法产物：
    `None`（没有 held_assets / 全部已建模 / helper 不可用）或
    `{"corr": {(held_key, broad_key): r}, "block": {included/skipped/counts}}`。
    求解的相关性表 = BROAD_CORR + held corr（见 run_sensitivity）；estimates/skipped
    透传进敏感性输出的加法块 `"held"`。
    """
    log = log or _stderr_log
    warnings = list(extract.get("warnings", []))

    # live_anchors: 用户实际持仓的境内基金锚定 (与 optimize_portfolio 同一逻辑)
    live_anchors = live_anchors_from_extract(extract)

    work = ensure_work_dir(work_dir)
    cached = load_pipeline(work)
    if cached is not None:
        hsbc_data, availability = cached
        log("pipeline_reuse", f"复用临时文件: {work}（抓取于 {hsbc_data.get('fetched_at', '?')[:19]}）")
    else:
        log("pipeline_refresh", "临时文件缺失或过期，全量重跑联网管线")
        hsbc_data, availability = ensure_pipeline(
            work,
            live_anchors=live_anchors,
            hsbc_request_timeout=hsbc_request_timeout,
            request_timeout=request_timeout,
            check_total_timeout=check_total_timeout,
            log=log,
        )
    avail_funds = availability["funds"]
    hsbc_status = {str(f.get("code", "")).strip(): f for f in hsbc_data.get("funds", [])}

    hsbc_pool, pool_meta = build_open_pool_from_status(
        hsbc_data.get("funds", []), availability, live_anchors=live_anchors)

    internal_weights = {h["ticker"]: h["weight"] for h in extract["us_equity"]["holdings"]}
    core_mu, core_vol = us_core_params(internal_weights)

    # 与 optimize_portfolio.main() 共用同一份资产构建循环；drop_note 保留本入口
    # 原有的提示措辞（「已剔除」），文案属于对外可见行为，重构不统一它。
    assets = build_investable_assets(
        hsbc_pool, pool_meta, avail_funds, hsbc_status, warnings,
        core_mu=core_mu, core_vol=core_vol, drop_note="已剔除")

    # ---- 持仓纳入（Q1，契约 §1.4 末）：在可投大类装配之后、求解之前 ----
    # 实现是 scripts/held_assets.py（依赖 params/market_data 的候选池表）的 helper；
    # 本脚本只调用、不复制逻辑。helper 未就绪 → 跳过（旧行为，输出不加 held 块）。
    held_meta = None
    held_input = list(extract.get("held_assets") or [])
    if held_input and extract.get("include_unmodeled_holdings"):
        try:
            from held_assets import classify_held_assets, inject_held_assets
        except ImportError:
            log("held_assets", "held_assets.py 不可用，跳过持仓纳入")
            classify_held_assets = inject_held_assets = None
        if inject_held_assets is not None:
            from test_tickers import resolve_test_assets
            modeled, _unmodeled = classify_held_assets(extract, assets, hsbc_pool)
            assets, held_corr, held_estimates, held_skipped = inject_held_assets(
                extract, assets,
                work_dir=work,
                hsbc_pool=hsbc_pool,
                existing_keys={a["key"] for a in assets},
                corr_keys=[a["key"] for a in assets],
                resolve=resolve_test_assets,
                log=(lambda message, level="info":
                     log("held_assets", message, level=level)),
            )
            if held_estimates or held_skipped:
                held_meta = {
                    "corr": dict(held_corr or {}),
                    "block": {
                        "included": held_estimates,
                        "skipped": [{"ticker": t, "reason": r} for t, r in held_skipped],
                        # 恒等式：held = modeled + injected + skipped（未建模的每只都有归宿）
                        "counts": {"held": len(held_input), "modeled": len(modeled),
                                   "injected": len(held_estimates),
                                   "skipped": len(held_skipped)},
                    },
                }
                log("held_assets",
                    f"持仓纳入：候选池内 {len(modeled)} 只 / 注入 {len(held_estimates)} 只 / "
                    f"跳过 {len(held_skipped)} 只")

    pipeline_info = {
        "work_dir": str(work),
        "reused_cache": cached is not None,
        "hsbc_fetched_at": hsbc_data.get("fetched_at"),
        "availability_checked_at": availability.get("checked_at"),
        "checked_codes": len(avail_funds),
        "open_pool_categories": len(hsbc_pool),
    }
    return assets, hsbc_pool, warnings, pipeline_info, held_meta


def lognormal_cagr(mu, sigma):
    """对数正态假设下的精确长期 CAGR（取代粗近似 mu - sigma^2/2）。

    CAGR = exp(E[ln(1+R)]) - 1；R 服从矩匹配 (mu, sigma) 的对数正态分布时
    E[ln(1+R)] = ln(1+mu) - s^2/2，其中 s^2 = ln(1 + sigma^2/(1+mu)^2)。
    """
    if sigma <= 0:
        return mu
    m = 1.0 + mu
    s2 = math.log(1.0 + sigma * sigma / (m * m))
    return math.exp(math.log(m) - s2 / 2.0) - 1.0


def fattail_cagr(mu, sigma, nu=3.0, floor=-0.999):
    """肥尾混合假设下的长期 CAGR：亏损侧幂律尾 + 盈利侧对数正态。

    拼接分布（splice，拼接点 R=0，亏损/盈利分界）：
    - 亏损侧 R<=0：R = mu + c*T，T ~ Student-t(nu)，尾部指数 alpha=nu
      （t 生存函数渐近 Pareto：P(T>x)~L(x)*x^-nu）；方差匹配
      c = sigma*sqrt((nu-2)/nu)；左截断于 floor（默认 -0.999，无下限时
      幂律左尾使 E[ln(1+R)]=-inf），截断概率以点质量置于 floor。
      t 左半整体重标定到对数正态的亏损概率 F_ln(0)：亏损概率与盈利侧
      都同对数正态基准，红线相对绿线的差 = 纯左尾形状（深度）代价。
      （若按 t 自身左概率 F_t(0) 拼接，方差匹配的 t3 肩窄、P(亏损) 反而
      低于对数正态，混合 CAGR 会高于绿线，违背肥尾=保守的意图。）
    - 盈利侧 R>0：与 cagr_lognormal 完全相同的矩匹配对数正态，原样保留。

    CAGR = exp(E[ln(1+R)]) - 1（年化对数增速的大数定律极限），
    E[ln(1+R)] 用一维数值积分精确计算（确定性，无蒙特卡洛噪声）。
    注：拼接后整体方差不严格等于 sigma^2（两侧各自保持原标定）。
    """
    if sigma <= 0:
        return mu
    nu = max(float(nu), 2.05)  # 方差匹配需要 nu > 2
    c = sigma * math.sqrt((nu - 2.0) / nu)
    tdist = stats.t(nu)
    # 矩匹配对数正态：Y = 1+R ~ LN(nu_ln, s)
    m = 1.0 + mu
    s2 = math.log(1.0 + sigma * sigma / (m * m))
    s = math.sqrt(s2)
    nu_ln = math.log(m) - s2 / 2.0
    lndist = stats.lognorm(s, scale=math.exp(nu_ln))

    t_floor = (floor - mu) / c
    t_q = (0.0 - mu) / c              # 拼接点 R=0 对应的 t 分位数
    p_floor = float(tdist.cdf(t_floor))
    f_t_left = float(tdist.cdf(t_q))      # F_t(R<=0)
    f_ln_left = float(lndist.cdf(1.0))    # F_ln(R<=0) = P(Y<=1)

    # 亏损侧原始期望：floor 点质量 + (floor, 0] 上 t 连续密度
    val_left, _ = integrate.quad(
        lambda t: math.log(1.0 + mu + c * t) * float(tdist.pdf(t)),
        t_floor, t_q, limit=200,
    )
    left_raw = p_floor * math.log(1.0 + floor) + val_left
    # 重标定 t 左半到对数正态的亏损概率（保持与绿线可比）
    w_left = f_ln_left / max(f_t_left, 1e-12)
    # 盈利侧：对数正态原样（R 的密度 = Y 的密度平移：f_R(x)=f_Y(1+x)）
    val_right, _ = integrate.quad(
        lambda x: math.log(1.0 + x) * float(lndist.pdf(1.0 + x)),
        0.0, math.inf, limit=200,
    )
    g = w_left * left_raw + val_right
    return math.exp(g) - 1.0


def _anchor_ignore(message):
    """锚点参数不合法时的**唯一**提示出口（契约 §2.2：忽略锚点 + stderr 一条提示，不失败）。"""
    print(f"[anchor] {message}，已忽略锚点参数（按无锚点模式继续）", file=sys.stderr)


def _parse_anchor(anchor_target, anchor_sleeve, anchor_ret, anchor_vol, assets):
    """校验/规范化锚点参数；任何一项不合法 → stderr 一条提示并**整体**忽略锚点。

    契约 §2.2：anchor 解析失败 / 袖 key 与资产不匹配 → 忽略锚点（不失败）。
    `anchor_target` 是锚定模式的开关：None = 无锚点（与旧版逐字节一致）。
    返回 None 或 {"target", "sleeve", "ret", "vol"}；sleeve 规范化为 {key: (mu, sigma)}。
    """
    if anchor_target is None:
        return None
    try:
        target = float(anchor_target)
    except (TypeError, ValueError):
        _anchor_ignore(f"锚点目标 {anchor_target!r} 不是数值")
        return None
    if not math.isfinite(target):
        _anchor_ignore(f"锚点目标 {anchor_target!r} 不是有限数值")
        return None

    sleeve = None
    if anchor_sleeve is not None:
        raw = anchor_sleeve
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                _anchor_ignore(f"锚点袖参数不是合法 JSON（{exc}）")
                return None
        if not isinstance(raw, Mapping):
            _anchor_ignore("锚点袖参数不是 {袖 key: [μ, σ]} 结构")
            return None
        sleeve = {}
        for key, value in raw.items():
            try:
                mu, vol = float(value[0]), float(value[1])
            except (TypeError, ValueError, IndexError):
                _anchor_ignore(f"锚点袖 {key!r} 的参数不是 [μ, σ] 数值对")
                return None
            if not (math.isfinite(mu) and math.isfinite(vol) and vol > 0):
                _anchor_ignore(f"锚点袖 {key!r} 的参数非法（μ/σ 必须有限且 σ>0）")
                return None
            sleeve[str(key)] = (mu, vol)
        asset_sleeve_keys = {str(a.get("key")) for a in assets
                             if str(a.get("key")) in SLEEVE_KEYS}
        unknown = sorted(set(sleeve) - asset_sleeve_keys)
        if unknown:
            _anchor_ignore("锚点袖 key 与本轮资产不匹配：" + "、".join(unknown))
            return None

    extras = {}
    for name, value in (("anchor_ret", anchor_ret), ("anchor_vol", anchor_vol)):
        if value is None:
            extras[name] = None
            continue
        try:
            fv = float(value)
        except (TypeError, ValueError):
            _anchor_ignore(f"{name} 不是数值")
            return None
        if not math.isfinite(fv):
            _anchor_ignore(f"{name} 不是有限数值")
            return None
        extras[name] = fv
    return {"target": target, "sleeve": sleeve,
            "ret": extras["anchor_ret"], "vol": extras["anchor_vol"]}


def _anchor_consistency(target, point, main_ret, main_vol, rf, warnings):
    """锚点一致性检查（契约 §2.2 + Lead 2026-10-18 收口）：返回加法式 "anchor" 块。

    判定 = **逐位一致**（不是「未被支配」）：锚点目标点的 ret/vol 与主结果的 ret/vol
    相差都 ≤ `_ANCHOR_MATCH_TOL`（1e-6，见该常量的注释）才算 matched=True。
    原因：锚点目标点改为**直接用主结果袖参数作初值**求解（见 `_add_point`），所以它
    本来就应当与主运行同一固定点；任何可见差异都说明锚定口径不一致 → 发
    `sensitivity.anchor_mismatch` 告警。

    缺少主结果收益/波动（CLI 没传 --anchor-ret/--anchor-vol）时无法比较 → matched=True
    （不误报）；锚点目标点不可行但主结果有统计 → matched=False（曲线缺了锚点）。
    """
    main_sharpe = None
    if main_ret is not None and main_vol is not None and float(main_vol) > 0:
        main_sharpe = (float(main_ret) - float(rf)) / float(main_vol)
    main = {"ret": main_ret, "vol": main_vol, "sharpe": main_sharpe}
    if point is None:
        point = {"ret": None, "vol": None, "sharpe": None}
    matched = True
    if main_ret is not None and main_vol is not None:
        if point.get("ret") is None or point.get("vol") is None:
            matched = False
        else:
            matched = (abs(float(point["ret"]) - float(main_ret)) <= _ANCHOR_MATCH_TOL
                       and abs(float(point["vol"]) - float(main_vol)) <= _ANCHOR_MATCH_TOL)
    if not matched:
        warnings.append(anchor_mismatch_warning(target, point, main))
    return {"target": round(float(target), 4), "matched": bool(matched),
            "point": point, "main": main}


def _build_targets(*, step_min, step_max, max_ret, anchor_target, anchor_mode):
    """构造扫描目标网格：等步长 → 锚点目标 → max feasible 末点；返回 (targets, cap, anchor_t)。"""
    upper_bound = min(step_max, max_ret) if step_max is not None else max_ret
    targets = []
    t = step_min
    while t <= upper_bound + _SCAN_LOOP_EPS:
        targets.append(round(t, 4))
        t += 0.005
    cap = min(step_max, max_ret) if step_max is not None else max_ret
    anchor_t = round(float(anchor_target), 4) if anchor_mode else None
    if anchor_t is not None:
        targets.append(anchor_t)
    if max_ret > 0 and (not targets or abs(targets[-1] - max_ret) > 0.005):
        if max_ret <= cap + _SCAN_LOOP_EPS:
            targets.append(round(max_ret, 4))
    if anchor_t is not None:
        targets = sorted(set(targets))
    return targets, cap, anchor_t


def run_sensitivity(extract, work_dir=None, hsbc_request_timeout=60.0,
                    request_timeout=12.0, check_total_timeout=300.0,
                    step_min=0.05, step_max=None, tail_dof=3.0, log=None,
                    anchor_target=None, anchor_sleeve=None,
                    anchor_ret=None, anchor_vol=None, monotone_envelope=True):
    """Run sensitivity analysis from step_min to max feasible return, 1% increments.

    锚定模式（契约 §2.2，2026-10-18 Q2）：传 `anchor_target`（= 主结果的目标收益率）
    即启用。锚点目标并入扫描网格，并对「约束绷紧」的点与锚点目标点额外传一个
    canonical 候选（主结果的袖参数 / λ=0.20 种子）给引擎择优 —— 于是曲线上锚点目标
    那一点就是「同锚定口径下实际会交付的组合」，可与主结果（绿点）重合。
    无锚点调用（anchor_target=None）：求解次数、数值行为与旧版一致，只多加法字段
    `fallback` / `chosen_start`（锚定模式才有的 `anchor` 块不出现）。

    候选包络（2026-10-25，契约 §2.3）：`monotone_envelope=True`（默认）时，扫描结束后
    把每个目标点的显示值改为「所有可交付候选中 ret ≥ T 的夏普最大者」——可行集随 T
    收缩，包络必然非增（目标↑ ⇒ 夏普不增）。被支配的点先用主结果袖参数重解一次
    （`repaired=true`）；修不掉就借用包络候选并标 `source_target`。
    `monotone_envelope=False` 只用于锁定旧 start-policy 的回归测试。
    """
    global RF

    # RF override from extract
    rf = extract.get("rf")
    if rf is not None:
        RF = float(rf)
    rf = float(RF)

    dom_w = float(extract["domestic_weight"])
    ov_w = float(extract["overseas_weight"])

    # 与 optimize_portfolio.py 同一资产口径 (联网管线复用/重建 + 暂停申购大类剔除)
    loaded = load_extract_assets(
        extract, work_dir,
        hsbc_request_timeout=hsbc_request_timeout,
        request_timeout=request_timeout,
        check_total_timeout=check_total_timeout,
        log=log,
    )
    if len(loaded) == 5:                       # 契约 §1.4：装配层返回 held_meta
        assets, _hsbc_pool, warnings, pipeline_info, held_meta = loaded
    else:                                      # 兼容旧 4 元组装配桩（既有离线测试）
        assets, _hsbc_pool, warnings, pipeline_info = loaded
        held_meta = None
    held_corr = dict((held_meta or {}).get("corr") or {})

    # 求解用的相关性表 = 宽基基础表 + 持仓纳入（与 optimize_portfolio 同口径；
    # 没有 held 时内容与 BROAD_CORR 完全一致 → 旧行为逐字节不变）。
    solve_corr = dict(BROAD_CORR)
    solve_corr.update(held_corr)

    # ---- 锚点参数校验（在拿到 assets 之后做袖 key 匹配检查）----
    anchor = _parse_anchor(anchor_target, anchor_sleeve, anchor_ret, anchor_vol, assets)
    anchor_mode = anchor is not None
    anchor_target = anchor_ret = anchor_vol = None
    anchor_sleeve = None
    if anchor_mode:
        anchor_target = anchor["target"]
        anchor_sleeve = anchor["sleeve"]
        anchor_ret = anchor["ret"]
        anchor_vol = anchor["vol"]

    keys = [a["key"] for a in assets]
    mu = np.array([a["mu"] for a in assets])
    vols = np.array([a["vol"] for a in assets])
    cov = build_broad_cov([{"key": a["key"], "vol": a["vol"]} for a in assets], solve_corr)
    dom_idx = [i for i, a in enumerate(assets) if a["pool"] == "domestic"]
    ov_idx = [i for i, a in enumerate(assets) if a["pool"] == "overseas"]

    lower, upper = stage1_bounds(assets)

    # Find max feasible return
    max_ret = max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w)
    if max_ret <= 0:
        return {"error": "无法计算最大可行收益率", "points": [],
                "warnings": warnings, "pipeline": pipeline_info}

    # Determine step range / target grid；--step-max 是上限，锚点目标例外（见 _build_targets）。
    targets, cap, anchor_t = _build_targets(
        step_min=step_min, step_max=step_max, max_ret=max_ret,
        anchor_target=anchor_target, anchor_mode=anchor_mode)

    # 逐点跑**完整**流水线（阶段一 + 袖内固定点 + 阶段二）：图上每一点就是
    # 「把目标设成 T 时实际会交付的那个组合」，与结果页同源。
    #
    # 初值选择 = **按点**（Lead 2026-10-18 v4；D3 实测 374.77s 的根因修复）：
    #   · 锚定模式首点（切点估计未知）：直接 canonical 起跑 —— 有主结果袖参数 → anchor
    #     （D2 证明该初值在低目标给到好盆地），否则 λ=0.20 种子，都无才冷启动；
    #   · 锚定模式 slack 点（target ≤ 切点估计）：**不调 solve_point**，直接复制上一个
    #     slack 点的 ret/vol/sharpe/worst/cagr（同一组切点组合、不同目标下限），
    #     chosen_start="reuse"（D2 里 T≤9% 的 8 个 28s 级求解由此省掉）；
    #   · 锚点目标点（非首点）：直接用主结果袖参数（与主运行同一固定点 → 绿点=蓝点）；
    #   · 边界带（切点估计 + ε < target ≤ 切点估计 + 2pp 且 ≤ anchor_target、非锚点目标点）
    #     → **prev 起跑单次**；仅失败时做一次独立 anchor（无袖参数则 seed）起跑重试
    #     （D4 实测 anchor 起跑在该带每点打满 10 轮 ~30s，prev ~4s）；
    #   · 其余切点估计 < target ≤ anchor_target → 主结果袖参数 canonical（"anchor"）；
    #     无锚点袖参数 → 确定性 λ=0.20 种子（"seed"）；失败且有 prev → 一次独立 prev 重试；
    #   · target > anchor_target → **anchor canonical 单次**（D2 该段 ~3.5s/点）；
    #     结果 fallback/未收敛 → **直接判停，不做 prev 重试**（v3 的 0.215 双跑 82s 纯浪费）。
    # 任何重试都是**独立的一次求解**（sleeve_start=另一初值、alt_starts=None），绝不把
    # 引擎的 base 再跑一遍。每点默认 1 次求解（slack 0 次），最多 2 次；首个 fallback 点即停。
    # ⚠️ 以上重排只作用于**锚定模式**；无锚点（anchor_target=None）路径保持逐字节不变。
    total_assets = float(extract["total_value"])
    universe = _sleeve_universe()
    pool_specs = dict(universe.pool_specs or {})
    runner = SensitivityRunner(universe=universe)
    points = []
    point_meta = []              # 与 points 对齐：deliverable/converged/origin（包络用）
    sleeve_start = None          # 上一点的交付袖参数（prev 热启动/重试的唯一来源）
    last_mu, last_cov = mu, cov
    last_ok_mu = None            # 最后一个**非回退**点的模型（max_final 的诚实口径）
    tangency_est = None          # 首个可行点的交付收益 = 切点（约束开始绷紧）估计
    anchor_point = None          # 锚点目标点的 {ret, vol, sharpe}
    _slack_ref = None            # 上一个 slack 点的交付字段（锚定模式复用来源）
    _slack_meta = None           # 与 _slack_ref 对齐的候选元数据
    _seed_cache = {"resolved": False, "vec": False}   # λ=0.20 种子惰性缓存（False=无种子）

    def _seed_params():
        """确定性 λ=0.20 种子（每扫描只算一次；无种子 → None）。"""
        if not _seed_cache["resolved"]:
            seeds = restart_seed_params(rf, pool_specs, (_ANCHOR_SEED_LAMBDA,))
            _seed_cache["vec"] = dict(seeds[0][1]) if seeds else False
            _seed_cache["resolved"] = True
        if not _seed_cache["vec"]:
            return None
        return dict(_seed_cache["vec"])

    def _is_fallback(solved, target_f):
        """契约 §2.2 的 fallback 判定：引擎降级了目标，或交付收益没到目标。"""
        return bool(
            float(solved.get("target", target_f)) < target_f - TARGET_REACH_EPS
            or float(solved["final"]["ret"]) < target_f - DELIVERY_EPS)

    def _needs_retry(solved, target_f):
        """该初值的结果「不可用」：不可行 / fallback / 未收敛 → 才值得另起一次求解。"""
        return (solved is None or _is_fallback(solved, target_f)
                or not bool(solved.get("sleeve_converged", True)))

    def _quality(solved, target_f):
        """重试「取可用者」的比较键：可用优先 → 交付缺口小 → 夏普高（确定性）。"""
        if solved is None:
            return (2, 0.0, 0.0)
        fin = solved.get("final") or {}
        ret = float(fin.get("ret") or 0.0)
        sharpe = float(fin.get("sharpe") or 0.0)
        bad = 0 if not _needs_retry(solved, target_f) else 1
        return (bad, round(max(0.0, target_f - ret), 6), round(-sharpe, 6))

    def _solve_at(target, start, point_warnings):
        """独立单次求解（alt_starts 恒为 None —— 绝不让引擎双跑）。"""
        return runner.solve_point(
            assets, solve_corr, dom_w=dom_w, ov_w=ov_w, target=target, rf=rf,
            extract=extract, total_assets=total_assets, warnings=point_warnings,
            sleeve_start=start, alt_starts=None,
        )

    def _plan_start(target_f, is_anchor, prev_start):
        """按点选择初值：返回 (start, label, retry)；retry = (start2, label2) 或 None。"""
        # 首点（切点估计未知）：锚定模式直接 canonical 起跑（v4；有袖参数 → anchor，
        # 否则 λ=0.20 种子，都无才冷启动）；无锚点模式保持旧行为 sleeve_start=None。
        if tangency_est is None:
            if anchor_mode:
                if anchor_sleeve is not None:
                    return dict(anchor_sleeve), "anchor", None
                seed = _seed_params()
                if seed is not None:
                    return seed, "seed", None
            return None, "prev", None
        # 锚点目标点（非首点）：直接用主结果袖参数（逐位一致；不重试）。
        if is_anchor and anchor_sleeve is not None:
            return dict(anchor_sleeve), "anchor", None
        # 约束松弛（target ≤ 切点估计）：上一点热启动（廉价）。
        if target_f <= float(tangency_est) + _SCAN_LOOP_EPS:
            return prev_start, "prev", None
        # 锚点右半段（target > anchor_target）：anchor canonical **单次**；失败直接判停。
        # （v3 的「先 prev、失败才 anchor」会让 0.215 这种点双跑 ~82s，纯浪费。）
        if (anchor_mode and anchor_target is not None
                and target_f > float(anchor_target) + _ANCHOR_TARGET_EQ_EPS):
            if anchor_sleeve is not None:
                return dict(anchor_sleeve), "anchor", None
            seed = _seed_params()
            if seed is not None:
                return seed, "seed", None
            return prev_start, "prev", None
        # 边界带（v5，Lead 2026-10-18）：切点估计 + ε < target ≤ 切点估计 + 2pp、且
        # target ≤ anchor_target、非锚点目标点 → **prev 起跑单次**；失败才做一次独立
        # anchor（无袖参数则 seed）起跑重试。反过来的 anchor 起跑会每点白付 ~30s。
        if (anchor_mode and not is_anchor and anchor_target is not None
                and tangency_est is not None
                and float(tangency_est) + _SCAN_LOOP_EPS < target_f
                and target_f <= float(tangency_est) + _ANCHOR_BOUNDARY_BAND + _SCAN_LOOP_EPS
                and target_f <= float(anchor_target) + _ANCHOR_TARGET_EQ_EPS):
            retry = None
            if anchor_sleeve is not None:
                retry = (dict(anchor_sleeve), "anchor")
            else:
                seed = _seed_params()
                if seed is not None:
                    retry = (seed, "seed")
            return prev_start, "prev", retry
        # 切点 < target ≤ anchor_target：canonical 单次求解；失败 +1 次独立 prev 重试。
        if anchor_sleeve is not None:
            retry = (prev_start, "prev") if prev_start is not None else None
            return dict(anchor_sleeve), "anchor", retry
        seed = _seed_params()
        if seed is not None:
            retry = (prev_start, "prev") if prev_start is not None else None
            return seed, "seed", retry
        return prev_start, "prev", None

    def _add_point(target):
        """求解一个目标点并追加到 points；返回 (feasible, fallback)。

        fallback=True（或 feasible=False）表示已到前沿回退段 → 调用方停止补点。
        锚定模式的 slack 点**不求解**，直接复用上一个 slack 点的交付字段（v4）。
        """
        nonlocal sleeve_start, last_mu, last_cov, last_ok_mu, tangency_est
        nonlocal anchor_point, _slack_ref, _slack_meta
        target_f = float(target)
        is_anchor = bool(anchor_mode) and \
            abs(target_f - float(anchor_target)) <= _ANCHOR_TARGET_EQ_EPS
        # ── 锚定模式 slack 点：不调 solve_point，直接复制上一个 slack 点的字段 ──
        # 「同一组切点组合、不同目标下限」：target_return 用新值，交付统计不变。
        if (anchor_mode and not is_anchor and _slack_ref is not None
                and tangency_est is not None
                and target_f <= float(tangency_est) + _SCAN_LOOP_EPS):
            ref = _slack_ref
            reused = {
                "target_return": round(target_f, 4),
                "feasible": True,
                "achieved_return": ref["achieved_return"],
                "volatility": ref["volatility"],
                "sharpe": ref["sharpe"],
                "worst_year_95": ref["worst_year_95"],
                "cagr_lognormal": ref["cagr_lognormal"],
                "cagr_fat_tail": ref["cagr_fat_tail"],
                "fallback": False,
                "chosen_start": "reuse",
            }
            points.append(reused)
            point_meta.append(dict(_slack_meta or {}))
            _slack_ref = reused       # 后续 slack 点复用同一组字段
            _slack_meta = point_meta[-1]
            return True, False
        prev_start = sleeve_start
        start, label, retry = _plan_start(target_f, is_anchor, prev_start)
        point_warnings = []
        solved = _solve_at(target, start, point_warnings)
        chosen_start = label
        # 最多一次重试：**独立**单次求解另一种初值，再取可用者（不含 base+alt 双跑）。
        if retry is not None and retry[0] is not None and _needs_retry(solved, target_f):
            alt_solved = _solve_at(target, retry[0], point_warnings)
            if _quality(alt_solved, target_f) < _quality(solved, target_f):
                solved, chosen_start = alt_solved, retry[1]
        if solved is None:
            points.append({
                "target_return": round(target_f, 4),
                "feasible": False,
                "achieved_return": None,
                "volatility": None,
                "sharpe": None,
                "worst_year_95": None,
                # 不可行点保留（UI 按 feasible 过滤），但扫描到此为止（前沿已到头）。
                "fallback": False,
                "chosen_start": "prev",
            })
            point_meta.append({"deliverable": False, "converged": False,
                               "origin": "point"})
            if is_anchor:
                anchor_point = {"ret": None, "vol": None, "sharpe": None}
            return False, True
        for w in point_warnings:
            if w not in warnings:
                warnings.append(w)
        fin = solved["final"]
        ret, vol, sharpe = float(fin["ret"]), float(fin["vol"]), float(fin["sharpe"])
        fallback = _is_fallback(solved, target_f)
        sleeve_start = {k: tuple(v) for k, v in solved["sleeve_params"].items()}
        last_mu, last_cov = solved["mu"], solved["cov"]
        if not fallback:
            last_ok_mu = solved["mu"]
        if tangency_est is None:
            # 首个可行点（通常是 step_min，约束最松）的交付收益 = 切点收益估计。
            tangency_est = ret
        point = {
            "target_return": round(target_f, 4),
            "feasible": True,
            "achieved_return": round(ret, 6),
            "volatility": round(vol, 6),
            "sharpe": round(sharpe, 6),
            "worst_year_95": round(ret - 1.96 * vol, 6),
            "cagr_lognormal": round(lognormal_cagr(ret, vol), 6),
            # 键名必须是 cagr_fat_tail（蛇形→驼峰得 cagrFatTail）：
            # cagr_fattail 会转成 cagrFattail，与 Swift 属性 cagrFatTail
            # 不匹配，可选字段静默解码为 nil → UI 回退旧近似。
            "cagr_fat_tail": round(fattail_cagr(ret, vol, nu=tail_dof), 6),
            # 加法式（契约 §2.2）：锚定/回退诊断字段。
            "fallback": fallback,
            "chosen_start": chosen_start,
        }
        points.append(point)
        meta = {"deliverable": bool(solved.get("deliverable", True)),
                "final_source": str(solved.get("final_source", "delivered")),
                "converged": bool(solved.get("sleeve_converged", True)),
                "origin": "point"}
        try:
            meta["model_gap"] = round(
                abs(float(fin["ret"]) - float(solved.get("stage1_ret", fin["ret"])))
                + abs(float(fin["vol"]) - float(solved.get("stage1_vol", fin["vol"]))), 6)
        except (TypeError, ValueError, KeyError):
            pass
        point_meta.append(meta)
        if anchor_mode and not fallback and target_f <= float(tangency_est) + _SCAN_LOOP_EPS:
            # 该实解点也是「切点组合」（例如锚点目标落在切点以下）→ 作为后续 slack 复用来源
            _slack_ref = point
            _slack_meta = meta
        if is_anchor:
            anchor_point = {"ret": point["achieved_return"],
                            "vol": point["volatility"],
                            "sharpe": point["sharpe"]}
        return True, fallback

    # ── 顺序扫描：**到首个回退点即停**（Lead 2026-10-18）───────────────────────────
    # 回退点及其之后的目标都只是回退角点（真实数据 T≥21.5% 有 7 个，每个都可能触发
    # 30s 级 fallback 轮），不属于「把目标设成 T 时实际会交付的组合」→ 不再扫描。
    # 首个回退点本身保留在 points 里（UI 按 fallback 过滤）。
    stopped = False
    for target in targets:
        ok, fallback = _add_point(target)
        if not ok or fallback:
            stopped = True
            break

    # 上面那张目标清单是用"当前持仓篮子"的袖参数估的上限；收敛后再算一次真实的
    # 最大可行收益率（诚实值：用最后一个**非回退**点的模型），并把中间缺的目标点补齐。
    dom_idx2 = [i for i, a in enumerate(assets) if a["pool"] == "domestic"]
    ov_idx2 = [i for i, a in enumerate(assets) if a["pool"] == "overseas"]
    model_mu = last_ok_mu if last_ok_mu is not None else last_mu
    max_ret_final = float(max_feasible_return(model_mu, lower, upper, dom_idx2, ov_idx2,
                                              dom_w, ov_w))
    # 第二阶段补点同样遵守 --step-max，且遇到 fallback 立即 break。
    cap_final = min(step_max, max_ret_final) if step_max is not None else max_ret_final
    if not stopped:
        t = (points[-1]["target_return"] if points else step_min) + 0.005
        while t <= cap_final - 0.002 and len(points) < 200:
            ok, fallback = _add_point(t)
            if not ok or fallback:
                stopped = True
                break
            t += 0.005
    if (not stopped and max_ret_final > 0 and max_ret_final <= cap_final + _SCAN_LOOP_EPS
            and (not points or points[-1]["target_return"] < max_ret_final - 0.002)):
        _add_point(max_ret_final - _SCAN_MARGIN)

    # ── 候选包络 + 支配修复（2026-10-25，契约 §2.3）─────────────────────────────
    # 目标 T 的可行集 {ret ≥ T} 随 T 收缩，所以「同一资产级模型下所有候选的夏普上包络」
    # 必然非增。候选 = 各点的自身解 + 主结果锚点（App 提供 --anchor-ret/--anchor-vol）。
    # 被更高目标的可行候选支配的点，先用主结果袖参数重解一次（App 有 anchor_sleeve）；
    # 修不掉就借用包络候选并标 source_target。这样曲线满足「目标↑ ⇒ 夏普不增」，
    # 而不是让热启动的次优固定点把曲线顶出一个假回升。
    if monotone_envelope and points:
        def _env_candidate(index, target_f, ret, vol, origin, chosen,
                           worst=None, cagr_ln=None, cagr_ft=None):
            if ret is None or vol is None or float(vol) <= 0:
                return None
            ret_f, vol_f = float(ret), float(vol)
            return {
                "index": index,
                "target": round(float(target_f), 4),
                "ret": round(ret_f, 6),
                "vol": round(vol_f, 6),
                "sharpe": round((ret_f - rf) / vol_f, 6),
                # 点自身已有这些派生值 → 直接复用，避免重复数值积分（扫描时间）。
                "worst": worst if worst is not None else round(ret_f - 1.96 * vol_f, 6),
                "cagr_ln": cagr_ln if cagr_ln is not None
                else round(lognormal_cagr(ret_f, vol_f), 6),
                "cagr_ft": cagr_ft if cagr_ft is not None
                else round(fattail_cagr(ret_f, vol_f, nu=tail_dof), 6),
                "origin": origin,
                "chosen": chosen,
            }

        env_candidates = []
        for i, p in enumerate(points):
            if not p.get("feasible") or p.get("fallback"):
                continue
            meta = point_meta[i] if i < len(point_meta) else {}
            if not bool(meta.get("deliverable", True)):
                continue
            cand = _env_candidate(i, p["target_return"], p["achieved_return"],
                                  p["volatility"], "point", p.get("chosen_start"),
                                  worst=p.get("worst_year_95"),
                                  cagr_ln=p.get("cagr_lognormal"),
                                  cagr_ft=p.get("cagr_fat_tail"))
            if cand is not None:
                env_candidates.append(cand)
        if (anchor_mode and anchor_ret is not None and anchor_vol is not None
                and float(anchor_vol) > 0):
            cand = _env_candidate(None, anchor_target, anchor_ret, anchor_vol, "main", None)
            if cand is not None:
                env_candidates.append(cand)

        def _envelope_pick(target_f):
            """在「ret ≥ T」的候选里取夏普最大者；同分优先本点、其次最近目标。"""
            eligible = [c for c in env_candidates
                        if c["ret"] is not None
                        and float(c["ret"]) >= float(target_f) - ENVELOPE_EPS]
            if not eligible:
                return None
            return min(eligible, key=lambda c: (
                -float(c["sharpe"]),
                0 if abs(float(c["target"]) - float(target_f)) <= ENVELOPE_EPS else 1,
                abs(float(c["target"]) - float(target_f)),
                float(c["target"]),
            ))

        # 支配修复：只对「自身解被更高目标的可行候选支配」的点；每点最多一次。
        if anchor_mode and anchor_sleeve:
            for i, p in enumerate(points):
                if not p.get("feasible") or p.get("fallback"):
                    continue
                meta = point_meta[i] if i < len(point_meta) else {}
                if not bool(meta.get("deliverable", True)):
                    continue
                best = _envelope_pick(p["target_return"])
                if best is None or best["index"] == i:
                    continue
                if float(best["sharpe"]) <= float(p["sharpe"]) + ENVELOPE_EPS:
                    continue
                repair_warns = []
                solved = _solve_at(p["target_return"], dict(anchor_sleeve), repair_warns)
                if solved is None or _needs_retry(solved, p["target_return"]):
                    continue
                if not bool(solved.get("deliverable", True)):
                    continue
                fin = solved["final"]
                cand = _env_candidate(i, p["target_return"], fin["ret"], fin["vol"],
                                      "repair", "anchor-repair")
                if cand is None or float(cand["sharpe"]) <= float(best["sharpe"]) + ENVELOPE_EPS:
                    continue
                env_candidates.append(cand)
                point_meta[i] = {"deliverable": True,
                                 "final_source": str(solved.get("final_source", "delivered")),
                                 "converged": bool(solved.get("sleeve_converged", True)),
                                 "origin": "repair"}
                for w in repair_warns:
                    if w not in warnings:
                        warnings.append(w)

        # 最终包络：每个点显示「ret ≥ T 的候选里夏普最大者」。
        for i, p in enumerate(points):
            if not p.get("feasible") or p.get("fallback"):
                continue
            best = _envelope_pick(p["target_return"])
            if best is None:
                continue
            p["achieved_return"] = best["ret"]
            p["volatility"] = best["vol"]
            p["sharpe"] = best["sharpe"]
            p["worst_year_95"] = best["worst"]
            p["cagr_lognormal"] = best["cagr_ln"]
            p["cagr_fat_tail"] = best["cagr_ft"]
            if best["index"] == i:
                p.pop("source_target", None)
                if best["origin"] == "repair":
                    p["repaired"] = True
                else:
                    p.pop("repaired", None)
            else:
                if abs(float(best["target"]) - float(p["target_return"])) > ENVELOPE_EPS:
                    p["source_target"] = best["target"]
                else:
                    p.pop("source_target", None)
                p.pop("repaired", None)
        # 锚点块取包络后的显示值（锚点目标点若被更高目标候选支配，这里如实反映）。
        if anchor_mode:
            for p in points:
                if (p.get("feasible") and not p.get("fallback")
                        and abs(float(p["target_return"]) - float(anchor_target))
                        <= _ANCHOR_TARGET_EQ_EPS):
                    anchor_point = {"ret": p["achieved_return"],
                                    "vol": p["volatility"],
                                    "sharpe": p["sharpe"]}
                    break

    result = {
        "max_feasible_return": round(max_ret_final, 6),
        "tail_dof": float(tail_dof),
        "rf": round(RF, 6),
        "domestic_weight": round(dom_w, 6),
        "overseas_weight": round(ov_w, 6),
        "points": points,
        "scope": ("候选包络（每点 = 目标 T 处已知最优的可交付组合；T 只是下限）"
                  if monotone_envelope else
                  "完整流水线（阶段一 + 袖内固定点 + 阶段二），与结果页同源"),
        "warnings": warnings,
        "pipeline": pipeline_info,
    }
    # 加法式块（契约 §1.4 / §2.2）：只在真的发生时出现 → 无锚点/无持仓纳入的旧调用不变。
    if held_meta is not None and held_meta.get("block") is not None:
        result["held"] = held_meta["block"]
    if anchor_mode:
        result["anchor"] = _anchor_consistency(
            anchor_target, anchor_point, anchor_ret, anchor_vol, rf, warnings)
    return result


def main():
    parser = argparse.ArgumentParser(description="敏感性分析：目标收益率步进扫描")
    parser.add_argument("extract_json", help="extract_live.json / extract_app.json 路径")
    parser.add_argument("--work-dir", default=None,
                        help="优化运行临时文件目录 (复用联网管线产物); 缺省用系统临时目录共享目录")
    parser.add_argument("--hsbc-timeout", type=float, default=60.0)
    parser.add_argument("--request-timeout", type=float, default=12.0)
    parser.add_argument("--check-timeout", type=float, default=300.0,
                        help="临时文件缺失全量重跑时, 天天基金校验子进程总超时秒数")
    parser.add_argument("--step-min", type=float, default=0.05, help="起始目标收益率（默认 0.05）")
    parser.add_argument("--step-max", type=float, default=None, help="上限目标收益率（默认=最大可行）")
    parser.add_argument("--tail-dof", type=float, default=3.0,
                        help="肥尾模型 Student-t 自由度=尾部指数 α（默认 3）")
    # ---- 敏感性锚定（契约 §2.2，2026-10-18 Q2）：传 --anchor-target 即启用锚定模式 ----
    parser.add_argument("--anchor-target", type=float, default=None,
                        help="主结果的目标收益率（锚点；提供即启用锚定模式）")
    parser.add_argument("--anchor-sleeve", default=None,
                        help='主结果的袖参数 JSON，如 {"O_US_CORE":[0.11,0.15]}')
    parser.add_argument("--anchor-ret", type=float, default=None,
                        help="主结果的组合收益（锚点一致性检查用；可选）")
    parser.add_argument("--anchor-vol", type=float, default=None,
                        help="主结果的组合波动（锚点一致性检查用；可选）")
    parser.add_argument("--out", dest="out_path", default=None, help="输出 JSON 路径")
    args = parser.parse_args()

    extract = load_extract(args.extract_json)

    result = run_sensitivity(
        extract,
        work_dir=args.work_dir,
        hsbc_request_timeout=args.hsbc_timeout,
        request_timeout=args.request_timeout,
        check_total_timeout=args.check_timeout,
        step_min=args.step_min,
        step_max=args.step_max,
        tail_dof=args.tail_dof,
        anchor_target=args.anchor_target,
        anchor_sleeve=args.anchor_sleeve,
        anchor_ret=args.anchor_ret,
        anchor_vol=args.anchor_vol,
    )

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
