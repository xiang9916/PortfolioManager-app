"""两层流水线引擎：阶段一求解/一轮求解/固定点驱动（P3-b2 从 optimize_portfolio 等价搬入）。

依赖铁律：只依赖 core 内部实现 + **显式入参**（`SleeveUniverse` = 内部池表/相关性/pool_specs；
`SleeveHooks` = 由调用方提供的策略函数）。不 import params/optimize_portfolio/market_data/
fund_pipeline/test_tickers，无延迟转发；不写模块级全局（触顶记录经 `cap_hits_out` 回传）。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import numpy as np

from .constants import (CAP_HIT_EPS, DELIVERY_EPS, FALLBACK_MARGIN_EPS, TARGET_REACH_EPS,
                        WEIGHT_ZERO_EPS)
from .constraints import apply_active_caps, active_cap_map, stage1_bounds
from .fixpoint import (compute_sleeve_floors, floor_next_params, floor_seed,
                       required_sleeve_mu, reseed_sleeves, resolved_sleeves,
                       seed_levels_for_target, sleeve_levels_for_feasibility,
                       sleeve_tangency, sleeve_tangency_all, stage1_weight,
                       undeliverable_warning, unresolved_weighted_sleeves,
                       validate_sleeve_params)
from .linalg import build_broad_cov
from .sleeve_params import (FLOOR_SLEEVE_KEYS, MAX_SLEEVE_ITER, SLEEVE_DAMPING, SLEEVE_KEYS,
                            SLEEVE_TOL, SleeveHooks, SleeveUniverse, damp_sleeve_params,
                            inject_sleeve_params, sleeve_params_us)
from .solver import max_feasible_return, solve_max_sharpe, solve_min_var
from .stage2 import stage2_gc_optimize, stage2_japan_optimize, stage2_optimize
from .warnings import CodedWarning as _Warn
from .warnings import warning_items


#: 阶段一"目标不可行 → 回退到最大可行收益"提示的公共标记（生产端与过滤端共用一段文案）。
STAGE1_FALLBACK_MARK = "目标在当前约束下不可行，已回退到最大可行收益"


def is_stage1_fallback_prompt(message):
    """是否为阶段一逐轮回退提示（数字随轮次变，必须合并而不是逐条累积）。"""
    return STAGE1_FALLBACK_MARK in message


def _resolve_hooks(hooks, universe, cap_hits_out):
    """把（可能为 None 的）hooks 补全成 5 个具体可调用对象。

    既有测试对 optimize_portfolio 的模块级**函数**打桩要尽量继续生效：薄壳在调用时把
    模块级名字传进来；core 直接调用（DTO 路径）时用自带实现 + universe.pool_specs。
    """
    pool_specs = dict(universe.pool_specs or {})

    def _solve_once_impl(assets, corr_pairs, **kw):
        return solve_once(assets, corr_pairs, universe=universe, cap_hits_out=cap_hits_out, **kw)

    def _sleeve_params_us_impl(internal, internal_tickers):
        return sleeve_params_us(internal, internal_tickers, us_internal=universe.us_internal,
                                us_internal_corr=universe.us_internal_corr)

    defaults = SleeveHooks(
        solve_once=_solve_once_impl,
        sleeve_params_us=_sleeve_params_us_impl,
        compute_sleeve_floors=lambda solved, assumed, target, rf, prev_floors, damping, dom_w, ov_w, cache:
            compute_sleeve_floors(solved, assumed, target, rf, prev_floors, damping, dom_w, ov_w,
                                  cache, pool_specs),
        seed_levels_for_target=lambda solved, tan_mu, target, dom_w, ov_w:
            seed_levels_for_target(solved, tan_mu, target, dom_w, ov_w, pool_specs),
        floor_seed=lambda key, t, rf: floor_seed(key, t, rf, pool_specs),
    )
    if hooks is None:
        return defaults
    return SleeveHooks(*[getattr(hooks, f) or getattr(defaults, f)
                         for f in ("solve_once", "sleeve_params_us", "compute_sleeve_floors",
                                   "seed_levels_for_target", "floor_seed")])


def solve_stage1(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf,
                  stage1_starts, warnings):
    """阶段一求解与回退链：max 夏普 → min 方差（约束绷紧区间两者等价）→ 最大可行收益。

    返回 (解, 生效目标)。三级都失败时返回 (None, target)，由调用方决定整体失败。
    seed=23 的末级回退是历史行为，不要顺手改（会改变回退结果的可复现性）。
    """
    solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target,
                              n_starts=stage1_starts, rf=rf)
    if solved is None:
        solved = solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, rf=rf)
    if solved is not None:
        return solved, target
    max_ret = max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w)
    warnings.append(_Warn(f"{target:.0%} {STAGE1_FALLBACK_MARK} {max_ret:.2%}。",
                          "target.stage1_fallback"))
    eff_target = max_ret - FALLBACK_MARGIN_EPS
    solved = solve_max_sharpe(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, eff_target,
                              seed=23, n_starts=stage1_starts, rf=rf)
    return solved, (eff_target if solved is not None else target)


def solve_once(assets, corr_pairs, *, dom_w, ov_w, target, rf, extract, total_assets,
               universe, cap_hits_out=None, log=None, stage1_starts=160, stage2_starts=80,
               warn=None, sleeve_floors=None, sleeve_exclude=None):
    """一轮完整求解：阶段一（大类，max 夏普 s.t. 收益 ≥ 目标）→ 阶段二（袖内再优化）。

    sleeve_floors（P1 方案 A）：本轮要传给 JP/GC 袖内求解器的收益下限。默认 None
    —— 低目标档（下限恒松弛）与 re-seed 路径都走 None，行为与修复前逐字节一致。
    """
    warnings = warn if warn is not None else []
    keys = [a["key"] for a in assets]
    mu = np.array([a["mu"] for a in assets])
    vols = np.array([a["vol"] for a in assets])
    cov = build_broad_cov([{"key": a["key"], "vol": a["vol"]} for a in assets], corr_pairs)
    dom_idx = [i for i, a in enumerate(assets) if a["pool"] == "domestic"]
    ov_idx = [i for i, a in enumerate(assets) if a["pool"] == "overseas"]

    lower, upper = stage1_bounds(assets)
    cap_map = active_cap_map(assets, universe.internal_pool_keys)
    apply_active_caps(upper, assets, cap_map, dom_idx, dom_w, warnings)
    # 加固①：某只袖的候选池给不出篮子时，按**0 权重**把它从组合里剔除后重解 ——
    # 这样交付组合里不再有"按模型参数计价"的权重（不把阶段一模型统计当交付）。
    excluded = [k for k in (sleeve_exclude or ()) if k in keys]
    for k in excluded:
        upper[keys.index(k)] = 0.0

    solved, eff_target = solve_stage1(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w,
                                       float(target), rf, stage1_starts, warnings)
    if solved is None:
        return None
    w1, ret1, vol1, sharpe1 = solved
    for i, a in enumerate(assets):
        cap = cap_map.get(a.get("key"))
        if cap is not None and w1[i] >= cap - CAP_HIT_EPS and cap_hits_out is not None:
            cap_hits_out.append((a["key"], float(w1[i]), float(cap)))
    if log:
        log("stage1_result", f"第一阶段完成 收益={ret1:.2%} 波动={vol1:.2%} 夏普={sharpe1:.3f}")

    floors_in = dict(sleeve_floors or {})
    for k in list(floors_in):                 # 0 权重的袖本轮不跑阶段二 → 传下去也不会生效
        if k not in keys or float(w1[keys.index(k)]) <= WEIGHT_ZERO_EPS:
            del floors_in[k]
    japan_selection = stage2_japan_optimize(
        w1, assets, total_assets, universe.jp_internal, universe.jp_internal_corr, rf,
        cap_map=cap_map, floor=floors_in.get("O_JP_EQ"), warnings=warnings,
        cap_hits_out=cap_hits_out)
    gc_selection = stage2_gc_optimize(
        w1, assets, total_assets, universe.gc_internal, universe.gc_internal_corr, rf,
        cap_map=cap_map, floor=floors_in.get("D_GREATER_CN"), warnings=warnings,
        cap_hits_out=cap_hits_out)
    internal, stage2_error = stage2_optimize(
        w1, extract, total_assets, assets, cov, keys, vols, eff_target, rf, cap_map=cap_map,
        n_starts=stage2_starts, us_internal=universe.us_internal,
        us_internal_corr=universe.us_internal_corr,
        sp500_internal_corr=universe.sp500_internal_corr,
        sp500_internal_corr_default=universe.sp500_internal_corr_default,
        cap_hits_out=cap_hits_out)

    if internal is not None:
        x_internal, ret2, vol2, sharpe2, w2, internal_tickers = internal
        final = {"ret": ret2, "vol": vol2, "sharpe": sharpe2, "floor": ret2 - 1.96 * vol2}
    else:
        # stage2 不可行时汇总回退第一阶段统计，否则结果文件会全成 0（Bug D2）
        final = {"ret": ret1, "vol": vol1, "sharpe": sharpe1, "floor": ret1 - 1.96 * vol1}
        if stage2_error:
            warnings.append(_Warn(stage2_error, "sleeve.stage2_error"))
        warnings.append(_Warn("美国权益内部优化不可行，组合统计已回退使用第一阶段结果。",
                              "sleeve.us_unavailable"))

    # 加固①：final 的来源 —— 只有当"阶段一有权重、阶段二却没给出篮子"的袖为空时，
    # final 才是真正的交付统计；否则它只是阶段一的**模型**统计（不可交付）。
    w1_idx = {k: float(w1[keys.index(k)]) for k in SLEEVE_KEYS if k in keys}
    weighted_no_basket = []
    for k, wt in w1_idx.items():
        if wt <= WEIGHT_ZERO_EPS:
            continue
        if k == "O_US_CORE" and internal is None:
            weighted_no_basket.append(k)
        elif k == "O_JP_EQ" and japan_selection is None:
            weighted_no_basket.append(k)
        elif k == "D_GREATER_CN" and gc_selection is None:
            weighted_no_basket.append(k)
    out = {
        "assets": assets, "keys": keys, "mu": mu, "vols": vols, "cov": cov,
        "dom_idx": dom_idx, "ov_idx": ov_idx, "lower": lower, "upper": upper, "cap_map": cap_map,
        "stage1_weights": w1, "stage1_ret": ret1, "stage1_vol": vol1, "stage1_sharpe": sharpe1,
        "japan_selection": japan_selection, "gc_selection": gc_selection,
        "internal": internal, "internal_tickers": (internal[5] if internal is not None else None),
        "stage2_error": stage2_error, "final": final, "target": eff_target, "warnings": warnings,
        "excluded_sleeves": sorted(excluded),
        "weighted_no_basket": sorted(weighted_no_basket),
        "final_source": "stage1_model" if weighted_no_basket else "delivered",
    }
    if floors_in:
        # 加法式内部键：本轮**实际生效**的袖内下限（未生效时不出现 → 低目标档逐字节一致）。
        out["sleeve_floors"] = {k: float(v) for k, v in floors_in.items()}
    return out


def solve_portfolio(assets, corr_pairs, *, dom_w, ov_w, target, rf, extract, total_assets,
                    universe, warnings=None, log=None, max_sleeve_iter=MAX_SLEEVE_ITER,
                    sleeve_tol=SLEEVE_TOL, stage1_starts=160, stage2_starts=80,
                    sleeve_start=None, damping=SLEEVE_DAMPING, cap_hits_out=None, hooks=None):
    """完整求解（袖内参数固定点迭代）。

    返回 None（不可行）或结果字典；袖参数、迭代轮数与是否收敛一并带出，便于审计与提示。

    收敛判据 = 每只袖的 μ/σ 变化 ≤ sleeve_tol，**但回退轮除外**：某一轮若发生"目标不可行 →
    回退到最大可行收益"，该轮解的是另一个问题（目标被降低、袖多半为空），不参与收敛判定，
    并按各袖候选池重新播种后继续迭代（2026-10-15 修，见 SLEEVE_DAMPING 下方的说明）。
    重播种后那一轮会额外获得预算（每只袖最多补一轮），否则预算刚好用尽时会把已经算出的
    重播种丢掉、还会把可达的目标误报成"不可行"。
    同理，"阶段一给了某只袖权重、阶段二却没跑出结果"的轮次也不算收敛：它的 final 只是
    阶段一的模型统计、不是交付组合（那种情况下 model_gap 恒为 0，会伪装成完美一致）。
    若最终仍不可行，则交付最大可行收益解并在 warnings 里显式说明"收益低于目标"。
    """
    warnings = [] if warnings is None else warnings
    pool_specs = dict(universe.pool_specs or {})
    hooks = _resolve_hooks(hooks, universe, cap_hits_out)
    sleeve = {}
    for a in assets:
        if a["key"] in SLEEVE_KEYS and a.get("mu") is not None:
            start = (sleeve_start or {}).get(a["key"])
            sleeve[a["key"]] = tuple(start) if start else (float(a["mu"]), float(a["vol"]))
    validate_sleeve_params(sleeve, sleeve_start)

    iters = max(1, int(max_sleeve_iter))
    history, converged, delta = [], False, 0.0
    best = None          # (分数, 该轮结果, 该轮假设的袖参数)：未收敛时取误差最小的一轮
    fallback_last = None  # 全程都判"目标不可行"时的兜底（收益低于目标，必须显式说明）
    last_assumed = None   # 最后一轮实际使用的袖参数（审计字段不能输出从未被求解过的值）
    pending_reseed = False
    # task-50 ①：播种轮（触发 sleeve_seeded_levels 的那一轮）不得**直接**成为收敛终点 ——
    # 播种值本身可能就自洽，会让"阻尼推进"从未被检验。播种后至少再多推一轮。
    seeded_pending = False
    budget_exhausted = False   # 是否因迭代预算用尽而退出（此时"目标不可行"的结论受预算限制）
    floors = None              # P1 袖内收益下限：只在出现回退轮之后才会被建立（低目标档恒为 None）
    tan_cache = {}             # 袖"切点参考参数"缓存（_sleeve_tangency）
    floor_unreachable_notes = {}   # 目标超过袖内可达上限的袖 → (切点 μ, 可达上限 μ)，收尾合并成一条
    # 重播种本身不产生关于固定点的信息，所以"重播种后还欠一轮检验"时要多给预算：
    # 否则 max_sleeve_iter 刚好用完时会把已经算出的重播种丢掉，还会把**可达**的目标
    # 误报成"不可行、请下调目标"。上界固定为 iters + max(1, 袖数)，保证总轮数有界
    # （正常情况下每只袖至多补一轮；阻尼把参数推离播种值后也可能再次补，故只保证上界）。
    max_rounds = iters + max(1, len(sleeve))
    for it in range(max_rounds):
        if it >= iters and not pending_reseed:
            budget_exhausted = True
            break
        pending_reseed = False
        assumed = dict(sleeve)
        last_assumed = assumed
        warn_local = []
        solved = hooks.solve_once(inject_sleeve_params(assets, sleeve), corr_pairs,
                             dom_w=dom_w, ov_w=ov_w, target=target, rf=rf, extract=extract,
                             total_assets=total_assets, log=log,
                             stage1_starts=stage1_starts, stage2_starts=stage2_starts,
                             warn=warn_local, sleeve_floors=floors)
        if solved is None:
            return None
        # 2026-10-17（task-42）：**中间轮的告警不进交付 warnings** —— 否则中间回退轮的结论
        # （"美国权益内部优化不可行，组合统计已回退使用第一阶段结果"之类）会留在最终交付里，
        # 与 deliverable / final_source 自相矛盾。逐轮告警保留在 history 审计字段；
        # 交付侧只带**选中轮**的告警（见收尾处的合并）。逐轮回退提示仍然只合并成一条。
        round_warns = [m for m in warn_local if not is_stage1_fallback_prompt(m)]

        nxt = dict(sleeve)
        us = hooks.sleeve_params_us(solved["internal"], solved["internal_tickers"])
        if us is not None:
            nxt["O_US_CORE"] = us
        for key, sel in (("O_JP_EQ", solved["japan_selection"]),
                         ("D_GREATER_CN", solved["gc_selection"])):
            if key in nxt and sel and sel.get("ret") is not None:
                nxt[key] = (float(sel["ret"]), float(sel["vol"]))
        delta = max((max(abs(nxt[k][0] - sleeve[k][0]), abs(nxt[k][1] - sleeve[k][1]))
                     for k in nxt if k in sleeve), default=0.0)

        # 本轮是否发生了"目标不可行 → 回退到最大可行收益"（见 _solve_stage1）。
        # 回退轮解的是**另一个问题**（目标被悄悄降低），它的解与"袖参数有没有变化"都不能
        # 当作本次目标下的结论：回退角点通常不给袖任何权重 → 阶段二整段跳过 → delta=0
        # 会被误判成收敛（见 SLEEVE_DAMPING 下方的说明）。所以回退轮既不参与收敛判定，
        # 也不参与"最优轮"的挑选。
        fell_back = float(solved["target"]) < float(target) - TARGET_REACH_EPS
        entry = {"iter": it + 1, "delta": round(float(delta), 6),
                 "sleeve": {k: [round(float(v[0]), 6), round(float(v[1]), 6)]
                            for k, v in sleeve.items()},
                 "fallback": fell_back}
        if round_warns:
            # 加法式审计键：中间轮（含被丢弃的回退轮）的告警原样保留，便于事后定位；
            # 仅在本轮确有告警时出现（低目标档不受影响）。
            entry["warnings"] = [str(m) for m in round_warns]
            entry["warning_items"] = warning_items(round_warns)
        if solved.get("sleeve_floors"):
            # 加法式审计键：仅在本轮真的带了袖内下限时出现（未生效的档位与修复前逐字节一致）。
            entry["sleeve_floors"] = {k: round(float(v), 6)
                                      for k, v in sorted(solved["sleeve_floors"].items())}
        if fell_back:
            fallback_last = solved
            entry["reseeded"] = reseed_sleeves(sleeve, solved, rf, pool_specs)
            new_floors, floor_audit = hooks.compute_sleeve_floors(
                solved, assumed, target, rf, floors, damping, dom_w, ov_w, tan_cache)
            if new_floors:
                floors = new_floors
                entry["floor_recomputed"] = floor_audit
                seeded = []
                for key in sorted(floors):
                    seed = floor_next_params(solved, key, floors[key], rf, pool_specs,
                                             floor_seed_fn=hooks.floor_seed)
                    if seed is not None:
                        # 同时写进 sleeve 与 nxt（见 _floor_next_params 的说明）。
                        sleeve[key] = seed
                        nxt[key] = seed
                        seeded.append(key)
                if seeded:
                    entry["floor_seeded"] = seeded
            elif floor_audit.get("skipped") == "target unreachable at sleeve max":
                # 目标超过袖内上限：不设下限（不硬凑），收尾显式报告并走既有回退/不可行路径。
                for k, m in (floor_audit.get("uncapped_max") or {}).items():
                    floor_unreachable_notes[k] = (floor_audit.get("tangency_mu", {}).get(k), m)
                # 回退角点清零袖后的播种（2026-10-16 落库，承接 AGENTS §12"袖全空"那条的残留）：
                # 角点常把三只袖都压到 0 权重，而"只按该袖切点篮子重新播种"不够 —— 切点 μ 可能
                # 明显低于阶段二实际能交付的篮子 → 阶段二因 0 权重早退、永不运行 → 误判不可行。
                # 契约（仅在"目标高于切点前沿"且走到本分支时生效）：
                #   ① 只对**前沿尚有余量**（可达上限明显高于切点 μ）的袖播种，无余量的袖完全不碰；
                #   ② 水平取"**刚好够到目标**的最小值"，逐袖求解（不取该袖上限、也不被可达上限
                #      最低的袖卡死）—— 播种量必须由"组合需要多少"决定；按袖上限播种会与阶段二
                #      交付篮子来回摆，连正常的主场景也会塌成多轮回退角点（attempt#1 的教训）；
                #   ③ 播种后强制多给一轮预算（pending_reseed），否则播种等于白播；
                #   ④ 审计键 sleeve_seeded_levels 为加法式、仅触发时出现（setup(_seed_levels_for_target)）。
                tan_all = {}
                for k in SLEEVE_KEYS:
                    got_tan = sleeve_tangency(k, rf, pool_specs, cache=tan_cache)
                    if got_tan is not None:
                        tan_all[k] = float(got_tan[0])
                levels = hooks.seed_levels_for_target(solved, tan_all, target, dom_w, ov_w)
                resolved_now = resolved_sleeves(solved)
                boot = []
                for key in sorted(levels):
                    if key in resolved_now:
                        continue
                    seed = hooks.floor_seed(key, levels[key], rf)
                    if seed is not None:
                        sleeve[key] = seed
                        nxt[key] = seed
                        boot.append(key)
                if boot:
                    entry["sleeve_seeded_levels"] = {k: round(float(levels[k]), 6) for k in boot}
                    pending_reseed = True      # 播种必须换来一轮检验，否则等于白播
                    seeded_pending = True      # task-50 ①：播种轮不得直接判收敛
            history.append(entry)
            if log:
                log("sleeve_fixpoint",
                    f"袖内参数迭代 {it + 1}/{max_rounds}：目标在本轮模型下不可行（已回退），"
                    f"按候选池重新播种 {len(entry['reseeded'])} 只袖")
            if entry["reseeded"]:
                pending_reseed = True
                continue                      # 空袖已换成乐观初值 → 下一轮重算（欠一轮检验）
            if not (resolved_sleeves(solved) & set(sleeve)):
                break                         # 袖全空且已在播种值上：目标确实不可行
            # 袖都被解出了（角点残留了权重）→ 这一轮的袖信息仍然有效，照常阻尼推进；
            # 只是不参与收敛判定，避免把"被降低后的目标"当成答案。
        else:
            history.append(entry)
            # 本轮"交付"是否真是交付：阶段一给了某只袖权重、阶段二却没跑出结果
            # （袖内池为空 / 全部起点失败），此时 final 只是阶段一的**模型**统计
            # （见 _solve_once 的 final 回退分支），model_gap 恒为 0，会被当成
            # "模型与交付完全一致"而拿满分 —— 必须降权，且不能算收敛。
            unresolved_weighted = unresolved_weighted_sleeves(solved, sleeve)
            if log:
                tail = "（已收敛）" if delta <= sleeve_tol and not unresolved_weighted else ""
                log("sleeve_fixpoint", f"袖内参数迭代 {it + 1}/{max_rounds}：最大变化 {delta:.4f}{tail}")
            # 排序键：先排除"交付其实没发生"的轮次，再看"模型与交付组合差多少"
            # （这才是我们要的一致性），最后看是否收敛。
            model_gap = abs(solved["final"]["ret"] - solved["stage1_ret"]) + \
                abs(solved["final"]["vol"] - solved["stage1_vol"])
            # 条件⑤（P1）：袖内下限一旦生效，"阶段一模型达标"不再等于"交付达标" ——
            # 只有**交付组合**收益 ≥ 本轮生效目标（非回退轮即用户目标）才算收敛。
            # 回退轮走的是另一个分支（本来就排除在收敛判定外）→ 不可行路径不会因此永不收敛（U3）。
            floor_active = bool(solved.get("sleeve_floors"))
            delivered_ok = (not floor_active
                            or float(solved["final"]["ret"]) >= float(solved["target"]) - DELIVERY_EPS)
            # 加固③（2026-10-16）：择优以**交付目标**为第一序 —— 先看这一轮交付有没有达标，
            # 再看缺口大小，再看"模型 vs 交付"的差，最后用轮次号做确定性 tie-break。
            # 旧序把"模型-交付差"放在达标之前，会把**没达标**的一轮顶上来当选（极限环区尤其明显）。
            shortfall = max(0.0, float(target) - float(solved["final"]["ret"]))
            # task-50 ②：达标轮之间按"同目标下交付组合波动更小"择优 —— 判据落地为**交付夏普**
            # （两次交付都恰好达标、收益相同时，夏普高 ⇔ 波动小；这正是任务书给的等价判据）。
            # 为什么不用裸的 vol 键：当两轮交付**都高于目标、收益不同**时，裸 vol 会选到收益更低、
            # 夏普更差的一轮（低目标档 poolA_low 实测 Sharpe 1.807 → 1.709），与"低目标档不得变化"冲突。
            # 完整序（确定性）：交付是否达标 → 交付缺口 → 交付夏普(大者优) → 交付波动(小者优)
            #                        → 模型-交付差 → 轮次号。
            sharpe = float(solved["final"].get("sharpe") or 0.0)
            # 完整序（确定性）：交付是否真的发生 → 交付缺口 → 模型-交付差 → 交付夏普(大者优)
            #                 → 交付波动(小者优) → 轮次号。
            # ⚠️ 波动/夏普刻意放在"模型-交付差"**之后**：字面把它们放在前面会
            #   ① 改变低目标档既有选择（实测 poolA_low 的 Sharpe 1.807 → 1.709）；
            #   ② 破坏已落地的 test_sleeve_fixpoint 契约（未收敛轮"模型误差 < 0.5pp"）。
            #   放在后面 = "同一达标 + 同一模型自洽度内，取波动更小（夏普更高）的一轮"。
            score = (1 if unresolved_weighted else 0, round(shortfall, 6),
                     round(model_gap, 6), round(-sharpe, 6),
                     round(float(solved["final"]["vol"]), 6), it)
            if best is None or score < best[0]:
                best = (score, solved, assumed)
            if (delta <= sleeve_tol and not unresolved_weighted and delivered_ok
                    and not seeded_pending):
                converged = True
                break
            if seeded_pending:
                # task-50 ①：播种轮之后的第一轮只用来"检验阻尼推进" —— 不判收敛，并再欠一轮预算；
                # 预算真的不够时循环会在顶上以 budget_exhausted 退出（走"预算用尽"告警，绝不报收敛）。
                seeded_pending = False
                pending_reseed = True

        # 阻尼推进：把"观测到的新参数"按 λ 混进当前值，避免离散选标引起的来回跳
        sleeve = damp_sleeve_params(nxt, sleeve, damping)

    # ── P0（2026-10-16）：目标可达性只在**组合层**判定 ────────────────────────────
    # 走到这里说明袖内固定点没有收敛。**先看交付是否真的没到目标**，只有没到才做组合层复核：
    # 阶段一用的是"当前假设的袖参数"，某只袖的假设 μ 可能低于它自己可交付的篮子（固定点尚未收敛），
    # 或该袖自身没有余量 —— 这些都是**袖级**事实，不等于整条组合不可行。旧逻辑会把这种轮次整轮标成
    # 回退轮（目标被降低、阶段二失去目标约束、交付塌成角点；用户实测：注入新标的后它缺席）。
    # 复核：把袖抬到"组合刚好够到目标"的最小水平（逐袖求解；没余量的袖按自身上限参与，其余腿照常
    # 承担）后按**请求目标**重解一次，成功就用它交付（阶段二照常带目标约束）。
    # 本来已经达标的档位一个字节都不动 —— 低目标性质不变量由此守住。
    retry_sleeve = None
    solved_retry = None
    levels = None
    provisional = best[1] if best is not None else fallback_last
    provisional_sleeve = (best[2] if best is not None
                          else (last_assumed if last_assumed is not None else sleeve))
    if provisional is not None and float(provisional["final"]["ret"]) < float(target) - DELIVERY_EPS:
        levels = sleeve_levels_for_feasibility(
            provisional, sleeve_tangency_all(rf, tan_cache, pool_specs), target, dom_w, ov_w,
            pool_specs)
        if levels:
            retry_sleeve = dict(provisional_sleeve)
            for key in sorted(levels):
                seed = hooks.floor_seed(key, levels[key], rf)
                if seed is not None:
                    retry_sleeve[key] = seed
        if retry_sleeve is not None and \
                any(retry_sleeve[k] != provisional_sleeve.get(k) for k in retry_sleeve):
            warn_retry = []
            solved_retry = hooks.solve_once(inject_sleeve_params(assets, retry_sleeve), corr_pairs,
                                       dom_w=dom_w, ov_w=ov_w, target=target, rf=rf,
                                       extract=extract, total_assets=total_assets, log=log,
                                       stage1_starts=stage1_starts, stage2_starts=stage2_starts,
                                       warn=warn_retry, sleeve_floors=floors)
            if solved_retry is None or \
                    float(solved_retry["target"]) < float(target) - TARGET_REACH_EPS:
                solved_retry, retry_sleeve = None, None
        else:
            retry_sleeve = None

    if solved_retry is not None:
        # 交付改由"组合层复核后按请求目标重解"的那一轮承担（袖按各自可达水平参与）。
        solved = solved_retry
        sleeve = retry_sleeve
        # 组合层复核既然证明目标可达，就不再报"袖内下限无法建立 → 走回退路径"（那句话已不成立）。
        floor_unreachable_notes.clear()
        history.append({
            "iter": len(history) + 1, "delta": 0.0,
            "sleeve": {k: [round(float(v[0]), 6), round(float(v[1]), 6)] for k, v in sleeve.items()},
            "fallback": False,
            "sleeve_levels": {k: round(float(v), 6) for k, v in (levels or {}).items()},
        })
        warnings.append(_Warn(
            "目标在阶段一的当前假设下不可行，但组合层复核显示可达：已把袖抬到"
            "「组合刚好够到目标」的水平后按请求目标重解，阶段二照常带目标约束"
            "（交付不再降级为角点）。",
            "sleeve.levels_lifted"))
        note = undeliverable_warning(solved, sleeve, pool_specs)
        if note:
            warnings.append(note)
    elif not converged:
        if best is not None:
            # 袖内选标是离散的，映射可能不收敛 → 取"阶段一模型 vs 交付组合"误差最小的那一轮，
            # 而不是硬性采用最后一轮（两者都可能不是固定点）。
            # score = (交付是否真的发生, 交付缺口, 交付夏普, 交付波动, 模型-交付差, 轮次号)。
            # 这里报告的是『模型与交付的差』—— 直接从选中轮算，不再依赖 score 的下标（防止改序后串位）。
            _, solved, sleeve = best
            sel_gap = (abs(float(solved["final"]["ret"]) - float(solved["stage1_ret"]))
                       + abs(float(solved["final"]["vol"]) - float(solved["stage1_vol"])))
            stale_note = ("；注意该轮有袖没被阶段二解出，那些权重仍按大类的**模型**参数计价，"
                          "收益/波动不完全来自交付组合"
                          if best[0][0] else "")
            warnings.append(_Warn(
                f"袖内参数迭代 {len(history)} 轮未完全收敛（最后一轮最大变化 {delta:.4f}）；"
                f"已采用误差最小的一轮（模型与交付组合的收益/波动差 {sel_gap:.4f}）{stale_note}。",
                "sleeve.not_converged"))
            note = undeliverable_warning(solved, sleeve, pool_specs)
            if note:
                warnings.append(note)
        else:
            # 每一轮都判"目标不可行"（重新播种后仍然不可行）→ 交付的就是最大可行收益解，
            # 它的收益**低于**目标。这里必须明说，不能让回退角点看起来像正常结果。
            # 审计字段要用这一轮**真正用过**的袖参数（重播种后的值可能从未被求解过）。
            solved = fallback_last
            sleeve = last_assumed if last_assumed is not None else sleeve
            delivered = float(solved["final"]["ret"])
            # 合并成**一条**提示（逐轮噪声已在上面的循环里丢掉），三个信息点必须都在：
            # ① 交付收益低于目标 ② 最大可行收益是多少 ③ 迭代预算是否用尽。
            if budget_exhausted:
                # 预算用尽 ≠ 目标真的不可行：末轮可能是"回退 + 袖被解出"，只是阻尼推进还没来得及
                # 被检验。此时不能断言"收敛后的模型下不可行"，更不能建议用户下调目标。
                budget_note = (f"迭代预算已用尽（{len(history)} 轮，上限 {max_rounds} 轮）—— "
                               f"目标未必真的不可行，可提高迭代预算后重试，或下调目标。")
            else:
                budget_note = (f"迭代预算未用尽（{len(history)} 轮，上限 {max_rounds} 轮）："
                               f"目标在收敛后的模型下仍不可行，请下调目标至该值以下，"
                               f"或检查相关标的的参数口径。")
            warnings.append(_Warn(
                f"目标 {float(target):.2%} 不可行：已按最大可行收益 {delivered:.2%} 交付"
                f"（低于目标 {float(target):.2%}）。{budget_note}",
                "target.budget_exhausted" if budget_exhausted else "target.infeasible"))
            note = undeliverable_warning(solved, sleeve, pool_specs)
            if note:
                warnings.append(note)

    # 这条提示解释的是"交付被降级"：只有**最终交付确实没到目标**时才报 ——
    # 否则一句话会让人以为整轮降级了（实测用户就是照这条提示误判的）。
    # 组合层复核成功时上面已 clear()。
    if floor_unreachable_notes and float(solved["final"]["ret"]) < float(target) - DELIVERY_EPS:
        # 逐轮累积合并成一条（P3 的教训：逐轮提示会刷屏）。
        detail = "；".join(f"{k} 切点 {float(a):.2%} / 可达上限 {float(b):.2%}"
                           for k, (a, b) in sorted(floor_unreachable_notes.items()))
        warnings.append(_Warn(
            f"袖内收益下限无法建立：目标 {float(target):.2%} 高于 JP/GC 在阶段一上限口径下"
            f"能提供的水平（{detail}）—— 组合层复核（把袖抬到各自可达上限参与）后该目标仍不可达，"
            f"本轮走既有的回退路径。",
            "target.sleeve_unreachable"))

    # ── 加固①（2026-10-16）：袖给不出篮子时，不得把阶段一**模型统计**当交付 ────────────
    # 旧行为：那种轮次的 final 就是阶段一的模型统计（权重按大类模型参数计价）→ 只做了"显式标注"。
    # 现在先走**真有解的路径**：把给不出篮子的袖按 0 权重从组合里剔除后重解一次；
    # 剔除后仍不成功 → 结果显式标 deliverable=False + final_source=stage1_model + 可机读 code。
    deliverable = True
    stuck = unresolved_weighted_sleeves(solved, sleeve)
    if stuck:
        warn_ex = []
        retry_floors = {k: v for k, v in (floors or {}).items() if k not in stuck}
        solved_ex = hooks.solve_once(inject_sleeve_params(assets, sleeve), corr_pairs,
                                dom_w=dom_w, ov_w=ov_w, target=target, rf=rf, extract=extract,
                                total_assets=total_assets, log=log,
                                stage1_starts=stage1_starts, stage2_starts=stage2_starts,
                                warn=warn_ex, sleeve_floors=retry_floors or None,
                                sleeve_exclude=stuck)
        if solved_ex is not None and not unresolved_weighted_sleeves(solved_ex, sleeve):
            solved = solved_ex
            warnings.append(_Warn(
                "袖 " + "、".join(sorted(stuck)) + " 在阶段二给不出篮子：已按 0 权重把它从组合里"
                "剔除后重解，交付组合不再包含它（不把阶段一的模型统计当交付）。",
                "sleeve.excluded_no_basket"))
        else:
            deliverable = False
            solved["weighted_no_basket"] = sorted(stuck)
            warnings.append(_Warn(
                "本次结果不可交付：有袖在阶段一有权重、阶段二却给不出篮子，按 0 权重剔除后重解"
                "仍未成功；final 只是阶段一**模型**统计，请勿据此执行。",
                "sleeve.undeliverable"))
    solved["deliverable"] = deliverable
    solved["final_source"] = "delivered" if deliverable else "stage1_model"

    # 加固③：极限环的可诊断振幅（袖 μ 在轮次间的摆幅）+ 边界说明（不承诺无条件收敛）
    _amp = {}
    for h in history:
        for k, v in (h.get("sleeve") or {}).items():
            _amp.setdefault(k, []).append(float(v[0]))
    _amp = {k: round(max(v) - min(v), 6) for k, v in _amp.items() if len(v) > 1}
    solved["sleeve_cycle"] = {
        "rounds": len(history),
        "amplitude_mu": _amp,
        "max_amplitude": round(max(_amp.values()), 6) if _amp else 0.0,
        "converged": converged,
        "budget_exhausted": budget_exhausted,
        "note": ("袖内选标是离散的：某些参数区存在极限环，实现按"
                 "「交付是否达标 → 缺口 → 模型-交付差 → 轮次」确定性择优，"
                 "不承诺无条件收敛（同输入同输出；振幅见 amplitude_mu）。"),
    }

    # 交付告警只反映**选中轮**（+ 调用方传入的 + 收尾汇总的）。
    # 自洽性：final_source='delivered' 时不会出现"已回退到第一阶段统计"这类中间轮结论；
    # 反过来真回退（选中轮本身就是回退轮）时该结论**必须**出现。
    for m in (solved.get("warnings") or []):
        if is_stage1_fallback_prompt(m):
            continue
        if m not in warnings:
            warnings.append(m)

    solved["warnings"] = warnings       # 合并后的完整提示（= 调用方 + 收尾汇总 + 选中轮）
    solved.update({
        "sleeve_params": {k: [round(float(v[0]), 6), round(float(v[1]), 6)] for k, v in sleeve.items()},
        "sleeve_iterations": len(history),
        "sleeve_converged": converged,
        "sleeve_history": history,
        # 加法式：每条告警的稳定 code（Swift 分类用）。warnings 本身仍是字符串数组、逐字节不变。
        "warning_items": warning_items(warnings),
    })
    return solved

