"""袖内固定点迭代的可行性/下限/择优纯逻辑（P3-b2 从 optimize_portfolio **等价搬入**）。

依赖铁律：只依赖 core 内部实现 + **显式入参**（内部池映射 `pool_specs`）。
不 import params/optimize_portfolio/market_data/fund_pipeline/test_tickers，无延迟转发；
不写任何模块级全局。
"""

from __future__ import annotations

import numpy as np

import asset_classes as AC

from .constants import (BISECT_EPS, BISECT_TOL, MU_REACH_EPS, RESEED_TOL,
                        SLEEVE_ROOM_EPS, TARGET_REACH_EPS, WEIGHT_ZERO_EPS)
from .constants import SOLVER_EPS as _SOLVER_EPS
from .constraints import max_return_given_mu
from .sleeve_params import (FLOOR_SLEEVE_KEYS, SLEEVE_KEYS, SLEEVE_RESOLVED_FIELD,
                            sleeve_reseed_params, sleeve_tangency)
from .stage2 import optimize_within_pool, pool_max_return
from .warnings import CodedWarning as _Warn


def resolved_sleeves(solved):
    """本轮真正被解出的袖：阶段二跑了并给出了篮子。

    袖得 0 权重时阶段二会在 `total_w <= 1e-6` 处早退（见 stage2_optimize /
    _internal_pool_optimize），于是"袖参数没有变化"只代表**没被检验**，不代表自洽 ——
    收敛判据必须区分这两者，否则回退角点会被当成固定点（见 SLEEVE_DAMPING 下方的说明）。
    """
    return {k for k, field in SLEEVE_RESOLVED_FIELD.items() if solved.get(field) is not None}


def stage1_weight(solved, key):
    """该轮阶段一给某只袖的权重（袖不在本轮资产里时返回 0）。"""
    keys = solved.get("keys") or []
    if key not in keys:
        return 0.0
    return abs(float(solved["stage1_weights"][keys.index(key)]))


def unresolved_weighted_sleeves(solved, sleeve):
    """本轮"阶段一给了权重、阶段二却没解出篮子"的袖（顺序与 sleeve 一致）。

    这类袖在交付统计里是按大类的**模型**参数计价的：`_solve_once` 在阶段二给不出篮子时会把
    final 退化为阶段一统计（避免结果文件全 0，历史 Bug D2）。那一刻 final 不是交付组合的
    统计，必须显式标注、不能让模型统计看起来像可交付结果（2026-10-15 P0）。
    """
    return [k for k in sleeve
            if k not in resolved_sleeves(solved)
            and stage1_weight(solved, k) > WEIGHT_ZERO_EPS]


def sleeve_failure_reason(key, pool_specs):
    """袖没被阶段二解出时的可诊断原因：池为空 / 有候选但解不出可行篮子。"""
    spec = pool_specs.get(key)
    if spec is None:
        return "候选池未注册"
    pool = spec[0]
    if not pool:
        return "袖内候选池为空（没有可执行标的）"
    return "袖内有候选标的但阶段二未解出可行篮子（全部起点失败）"


def validate_sleeve_params(sleeve, sleeve_start=None):
    """袖参数（含 sleeve_start 热启动值）必须有限且 σ>0，否则提前抛出可诊断的 ValueError。

    σ=0/NaN 会让 `build_broad_cov` 出现 0/0 → NaN，随后 stage2_covariance 调 project_psd
    时抛 `numpy.linalg.LinAlgError: Eigenvalues did not converge`，消息里没有任何上下文
    （2026-10-15 P2 修）。宁可在入口就把 key / 取值 / 来源说清楚，也不静默改成"看起来正常"的值。
    """
    for key, params in sleeve.items():
        mu, vol = float(params[0]), float(params[1])
        bad = []
        if not np.isfinite(mu):
            bad.append(f"μ={mu!r}")
        if not np.isfinite(vol) or vol <= 0:
            bad.append(f"σ={vol!r}")
        if not bad:
            continue
        source = "sleeve_start" if (sleeve_start or {}).get(key) else "资产参数"
        raise ValueError(
            f"袖参数非法（来源：{source}）：{key} 的 " + "、".join(bad) +
            "。袖参数必须是有限值且 σ>0 —— σ≤0/NaN 会让协方差矩阵出现 0/0（NaN），"
            "随后 project_psd 抛 numpy.linalg.LinAlgError、无法求解；请修正后重试。")


def undeliverable_warning(solved, sleeve, pool_specs):
    """本轮交付统计其实只是阶段一**模型**统计时，返回显式的"不可交付"提示；否则 None。"""
    stuck = unresolved_weighted_sleeves(solved, sleeve)
    if not stuck:
        return None
    detail = "；".join(f"{k}（{sleeve_failure_reason(k, pool_specs)}）" for k in stuck)
    return _Warn("本次交付统计不可交付：" + detail +
                 " 在阶段一有权重、阶段二却没给出篮子，这些权重是按大类**模型**参数计价的，"
                 "final 里的收益/波动是阶段一模型统计、不是可交付组合的统计（active 单标的 "
                 f"{AC.ACTIVE_CAP:.0%} 上限只在阶段二内部生效，模型口径可能高估收益）。"
                 "请勿据此执行，请检查该袖的候选池与参数后重跑。", "sleeve.undeliverable")


def required_sleeve_mu(solved, tan_mu, target, dom_w, ov_w, pool_specs):
    """二分求"让阶段一可达上限 ≥ 目标"所需的最小**统一**袖内 μ（P1 方案 A）。

    与**权重无关**：直接对阶段一的上界/池约束算可达上限，因此既不受 U1（多袖各自补缺 →
    超交付 + 风险全抬）影响，也不受 U2（cap_norm = 单标的上限 ÷ 袖权重，逐轮漂移）影响 ——
    这里只用阶段一上界数组，它由 dom_w 与 ACTIVE_CAP 决定、与袖权重无关。

    返回 (t, audit)：t = 所需统一 μ；"切点口径下已达标"或"即使每只袖都抬到自身可达上限
    也够不到目标" → (None, audit)，后者交给既有回退/不可行路径显式报告。
    """
    keys = solved.get("keys") or []
    for field in ("mu", "upper", "dom_idx", "ov_idx"):
        if field not in solved or solved.get(field) is None:
            return None, {"skipped": "missing " + field}
    idx = {k: i for i, k in enumerate(keys)}
    use = [k for k in FLOOR_SLEEVE_KEYS if k in idx and tan_mu.get(k) is not None]
    if not use:
        return None, {"skipped": "no floor sleeve"}
    caps = {}
    for k in use:
        spec = pool_specs.get(k)
        m = pool_max_return(spec[0], spec[1], None) if spec else None
        if m is None:
            return None, {"skipped": "sleeve pool infeasible"}
        caps[k] = float(m)

    # 参考向量：所有袖都取"它自己切点篮子"的 μ 与当前假设 μ 的较大者（非袖资产保持原样）。
    # 关键：门槛必须是"前沿口径"的，不能被某一轮偏弱的初始假设 μ 拉低 —— 否则 T 明明在
    # 切点前沿内可达，却会被判成需要抬升，从而破坏"下限恒松弛时与修复前逐字节一致"的不变量。
    vec0 = np.array(solved["mu"], dtype=float)
    for k in SLEEVE_KEYS:
        if k in idx and tan_mu.get(k) is not None:
            vec0[idx[k]] = max(vec0[idx[k]], float(tan_mu[k]))

    def ceiling(t):
        vec = vec0.copy()
        for k in use:
            vec[idx[k]] = max(vec[idx[k]], float(t))
        return max_return_given_mu(vec, solved["upper"], solved["dom_idx"], solved["ov_idx"],
                                    dom_w, ov_w)

    lo = max(float(tan_mu[k]) for k in use)
    hi = min(caps[k] for k in use)
    c_lo = ceiling(lo)
    if c_lo is not None and c_lo >= float(target) - TARGET_REACH_EPS:
        return None, {"skipped": "frontier already reaches target"}
    c_hi = ceiling(hi)
    if hi <= lo + BISECT_EPS or c_hi is None or c_hi < float(target) - TARGET_REACH_EPS:
        return None, {"skipped": "target unreachable at sleeve max",
                      "tangency_mu": {k: round(float(tan_mu[k]), 6) for k in use},
                      "uncapped_max": {k: round(caps[k], 6) for k in use}}
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        c = ceiling(mid)
        if c is not None and c >= float(target) - TARGET_REACH_EPS:
            hi = mid
        else:
            lo = mid
        if hi - lo <= BISECT_TOL:
            break
    audit = {k: {"required_mu": round(float(hi), 6), "tangency_mu": round(float(tan_mu[k]), 6),
                 "uncapped_max": round(caps[k], 6)} for k in use}
    return float(hi), audit


def floor_next_params(solved, key, t, rf, pool_specs, floor_seed_fn=None):
    """下限袖下一轮的假设参数：优先本轮**已交付**的篮子（它必须满足下限），否则用下限篮子。

    下限是"有意的模型口径"而不是观测值：直接写成下一轮的假设参数、不让它被随后的阻尼按 λ
    拉回旧观测 —— 否则一次抬升要 ~9 轮才爬到位（表现为预算用尽、目标被误报成不可行）。
    """
    field = SLEEVE_RESOLVED_FIELD.get(key)
    sel = solved.get(field) if field else None
    if sel is not None and sel.get("ret") is not None:
        return (float(sel["ret"]), float(sel["vol"]))
    if floor_seed_fn is not None:
        return floor_seed_fn(key, t, rf)      # 注入的钩子用调用方签名（optimize_portfolio 薄壳）
    return floor_seed(key, t, rf, pool_specs)


def floor_seed(key, t, rf, pool_specs):
    """下限对应的可执行篮子参数 (μ,σ)：袖内 max 夏普 s.t. μ ≥ t（不设组合上限）。

    用途 = **乐观播种**：本轮没被阶段二解出的下限袖，下一轮阶段一必须"看见"它抬升后的 μ，
    否则目标高于切点时它永远拿不到权重 —— P1 会在最需要的场景失效。
    """
    spec = pool_specs.get(key)
    if spec is None:
        return None
    got = optimize_within_pool(spec[0], spec[1], rf, spec[2], floor=float(t))
    if got is None:
        return None
    _, ret, vol, _ = got
    return (float(ret), float(vol))


def compute_sleeve_floors(solved, assumed, target, rf, prev_floors, damping, dom_w, ov_w, cache,
                          pool_specs, sleeve_tangency_fn=None):
    """回退轮之后重算袖内收益下限（P1 方案 A 的反馈项）。

    口径 = _required_sleeve_mu：所有 JP/GC 被要求抬到**同一个 μ**，该 μ 是"阶段一可达上限
    刚好够到目标"的最小值（与权重无关）。用绝对 μ 而不是"假设 μ + ℓ"：后者会让固定点方程
    退化成 μ = μ + ℓ、只有 ℓ=0 是解，迭代必然来回摆。

    返回 (floors, audit)：floors 只含"下限真高于该袖当前假设 μ"的袖 —— 未绑定的档位与修复前
    逐字节一致。

    ε 契约（2026-10-17 收口）：**只有一个语义 ε**（DELIVERY_EPS），且只用在交付断言
    「交付收益 ≥ 目标 − ε」上；袖内下限、袖内求解器断言都只留 _SOLVER_EPS（纯数值松弛）。
    因此实际保证是「交付收益 ≥ 目标 − ε」，不再出现"需求 − 3ε"的叠加。为什么不用方案 C（袖前沿 λ 参数化 + 联合求解）：λ 落在两个前沿节点之间时，
    插值出的 (μ,σ) 未必对应可执行篮子 → 老 gap 以"插值误差"回来，且阶段一要整体重构
    （U4；详见 tmp/agent-team/plan/P1-sleeve-objective.md §2/§6）。
    """
    keys = solved.get("keys") or []
    if not keys or solved.get("mu") is None or solved.get("upper") is None:
        # 受控桩（测试替身）可能不带这些字段：缺了就退化为"不设下限"（生产路径一定带）。
        return None, {}
    tan_mu = {}
    for k in SLEEVE_KEYS:                 # 全部袖的切点 μ：门槛口径要用"前沿"，见 _required_sleeve_mu
        if k in assumed:
            got = (sleeve_tangency_fn(k, rf, cache=cache) if sleeve_tangency_fn is not None
                   else sleeve_tangency(k, rf, pool_specs, cache=cache))
            if got is not None:
                tan_mu[k] = float(got[0])
    if not any(k in tan_mu for k in FLOOR_SLEEVE_KEYS):
        return None, {}
    t, audit = required_sleeve_mu(solved, tan_mu, target, dom_w, ov_w, pool_specs)
    if t is None:
        return None, audit
    if prev_floors:
        # 下限也是反馈通道：按与袖参数同样的 λ 阻尼（避免整块跳变；U2 的稳定化手段之一）。
        prev_t = min(float(v) for v in prev_floors.values())
        t = prev_t + damping * (t - prev_t)
    floors = {}
    for k in FLOOR_SLEEVE_KEYS:
        if k not in tan_mu:
            continue
        cur = max(float(assumed[k][0]), tan_mu[k])
        if t <= cur + _SOLVER_EPS:
            continue          # 恒松弛：不设下限 → 该袖本轮与修复前逐字节一致
        floors[k] = float(t)          # 单次 ε：下限即「需求」，ε 只在交付断言处扣
    if not floors:
        return None, audit
    return floors, audit


def _ceiling_solver(solved, ref, idx, dom_w, ov_w):
    """构造「给定各袖水平 → 组合可达最大收益」的求值函数。

    两条"目标水平播种"路径（seed_levels_for_target / sleeve_levels_for_feasibility）
    共用的纯数学，原先是两份逐字相同的局部闭包（2026-10-18 去重）。
    """
    def ceiling(levels):
        vec = ref.copy()
        for k, lv in levels.items():
            vec[idx[k]] = float(lv)
        return max_return_given_mu(vec, solved["upper"], solved["dom_idx"], solved["ov_idx"],
                                    dom_w, ov_w)

    return ceiling


def seed_levels_for_target(solved, tan_mu, target, dom_w, ov_w, pool_specs):
    """给"回退角点清零了权重、但前沿尚有余量"的袖求播种水平（2026-10-16 落库）。

    背景：回退轮常把袖压到 0 权重；若只按该袖**切点**篮子重新播种，切点 μ 可能明显低于阶段二
    实际能交付的篮子 → 阶段二因 0 权重早退、永不运行 → 目标被误判不可行（交付高波动角点）。

    契约（attempt#1 已回退，见下）：
      1) **逐袖**水平（不是全体统一 μ）→ 不被"可达上限最低"的袖卡死；
      2) 水平 = 让"阶段一可达上限刚好够到目标"的最小值，**不是该袖上限** → 不过冲。
         播种量必须由"组合需要多少"决定：按袖上限播种会让模型里的袖 μ 跳到上限、与阶段二交付
         篮子来回摆，连本来正常的主场景也会塌成多轮回退角点（attempt#1 的教训）；
      3) 只动"可达上限明显高于切点 μ"的袖；无余量的袖完全不碰（不扰动正常档位）。
    返回 {key: level}；若把有余量的袖全部抬满仍够不到目标 → 返回 {}（真不可行，交给显式报告）。
    """
    keys = solved.get("keys") or []
    if not keys or solved.get("mu") is None or solved.get("upper") is None:
        return {}
    idx = {k: i for i, k in enumerate(keys)}
    ref = np.array(solved["mu"], dtype=float)
    caps = {}
    for k in SLEEVE_KEYS:
        if k not in idx or tan_mu.get(k) is None:
            continue
        ref[idx[k]] = max(ref[idx[k]], float(tan_mu[k]))
        spec = pool_specs.get(k)
        m = pool_max_return(spec[0], spec[1], None) if spec else None
        if m is not None and float(m) > float(tan_mu[k]) + SLEEVE_ROOM_EPS:
            caps[k] = float(m)

    ceiling = _ceiling_solver(solved, ref, idx, dom_w, ov_w)

    if not caps:
        return {}
    base = ceiling({})
    if base is not None and base >= float(target) - TARGET_REACH_EPS:
        return {}
    levels = {}
    for k in sorted(caps, key=lambda x: -(caps[x] - float(tan_mu[x]))):
        c = ceiling(levels)
        if c is not None and c >= float(target) - TARGET_REACH_EPS:
            break
        lo = max(float(ref[idx[k]]), float(tan_mu[k]))
        hi = caps[k]
        if hi <= lo + BISECT_EPS:
            continue
        top = ceiling(dict(levels, **{k: hi}))
        if top is None or top < float(target) - TARGET_REACH_EPS:
            levels[k] = hi          # 抬满也不够 → 抬满后继续下一只
            continue
        for _ in range(60):        # 二分：该袖再抬多少刚好够到目标
            mid = 0.5 * (lo + hi)
            got = ceiling(dict(levels, **{k: mid}))
            if got is not None and got >= float(target) - TARGET_REACH_EPS:
                hi = mid
            else:
                lo = mid
            if hi - lo <= BISECT_TOL:
                break
        levels[k] = hi
        break
    c = ceiling(levels)
    if c is None or c < float(target) - TARGET_REACH_EPS:
        return {}
    return levels


def sleeve_tangency_all(rf, cache, pool_specs):
    """全部袖的"切点 μ"（带缓存）。门槛/复核口径统一用切点前沿，避免被某一轮偏弱的假设 μ 拉低。"""
    out = {}
    for k in SLEEVE_KEYS:
        got = sleeve_tangency(k, rf, pool_specs, cache=cache)
        if got is not None:
            out[k] = float(got[0])
    return out


def sleeve_levels_for_feasibility(solved, tan_mu, target, dom_w, ov_w, pool_specs):
    """组合层可达性复核（P0 2026-10-16）：求一组"最小抬升"的袖内水平，使**组合**可达目标。

    与 _seed_levels_for_target（回退轮里的乐观播种）的关键区别：这里**不筛"前沿是否还有余量"**。
    单只袖够不到目标（没有余量）只意味着"该袖不承担目标"，不等于整条组合不可行 —— 它按自身可达
    上限参与即可，目标由其它腿（其它袖 / 大类资产 / 新注入的标的）承担。只有当**每只袖都抬到自身
    可达上限**、组合仍够不到目标时，才返回 {}（真·组合级不可行，交给既有回退/显式报告路径）。

    返回 {key: level}（可能为空）—— 每只袖的水平都是"刚好够到目标"的最小值，逐袖求解，
    因此不会被可达上限最低的那只袖卡死，也不会像"按袖上限播种"那样引入过冲。
    """
    keys = solved.get("keys") or []
    if not keys or solved.get("mu") is None or solved.get("upper") is None:
        return {}
    idx = {k: i for i, k in enumerate(keys)}
    ref = np.array(solved["mu"], dtype=float)
    caps = {}
    for k in SLEEVE_KEYS:
        if k not in idx or tan_mu.get(k) is None:
            continue
        ref[idx[k]] = max(ref[idx[k]], float(tan_mu[k]))
        spec = pool_specs.get(k)
        m = pool_max_return(spec[0], spec[1], None) if spec else None
        if m is not None:
            caps[k] = float(m)          # 不筛余量：没余量的袖也按其自身上限参与
    if not caps:
        return {}

    ceiling = _ceiling_solver(solved, ref, idx, dom_w, ov_w)

    # 基线水平：每只袖**至少**按"它自己切点篮子"的 μ 参与。偏低的是本轮**假设 μ**
    # （初值篮子 / 上一轮交付 / 阻尼中间值），不是袖的真实能力 —— 这正是"假不可行"的来源。
    assumed_mu = {k: float(solved["mu"][idx[k]]) for k in caps}
    levels = {k: max(assumed_mu[k], float(tan_mu[k])) for k in caps
              if max(assumed_mu[k], float(tan_mu[k])) > assumed_mu[k] + MU_REACH_EPS}
    base = ceiling(levels)
    if base is not None and base >= float(target) - TARGET_REACH_EPS:
        # 袖按切点水平参与即可达标 → 只抬到切点，不做额外抬升（不会过冲）。
        return levels
    # 仍然不够 → 逐袖按"刚好够到目标"的最小水平继续抬（没余量的袖抬到自身上限）。
    for k in sorted(caps, key=lambda x: -(caps[x] - float(tan_mu[x]))):
        c = ceiling(levels)
        if c is not None and c >= float(target) - TARGET_REACH_EPS:
            break
        lo = max(float(levels.get(k, assumed_mu[k])), float(tan_mu[k]))
        hi = caps[k]
        if hi <= lo + BISECT_EPS:
            continue
        top = ceiling(dict(levels, **{k: hi}))
        if top is None or top < float(target) - TARGET_REACH_EPS:
            levels[k] = hi              # 抬到自身上限也不够 → 该袖按上限参与，再看下一只
            continue
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            got = ceiling(dict(levels, **{k: mid}))
            if got is not None and got >= float(target) - TARGET_REACH_EPS:
                hi = mid
            else:
                lo = mid
            if hi - lo <= BISECT_TOL:
                break
        levels[k] = hi
        break
    c = ceiling(levels)
    if c is None or c < float(target) - TARGET_REACH_EPS:
        return {}
    return levels


def reseed_sleeves(sleeve, solved, rf, pool_specs, tol=RESEED_TOL):
    """把本轮"没被解出"的袖按候选池重新播种，返回真正发生变化的 key 列表。

    已在播种值上时不再重复播种（返回空列表 = 没有可改善的余地）。
    """
    resolved = resolved_sleeves(solved)
    changed = []
    for key in list(sleeve):
        if key in resolved:
            continue
        seed = sleeve_reseed_params(key, rf, pool_specs)
        if seed is None:
            continue
        old = sleeve[key]
        if abs(seed[0] - old[0]) <= tol and abs(seed[1] - old[1]) <= tol:
            continue
        sleeve[key] = seed
        changed.append(key)
    return changed



def restart_seed_params(rf, pool_specs, lambdas):
    """多初值重启的**确定性**种子（2026-10-17，修「加新标的 → 夏普下降」）。

    背景：目标播种分叉后，固定点映射存在多个自洽解（模型与交付都自洽），当前路径可能
    锁在较差的一个。这里给出若干"确定性初值向量"供引擎重启固定点并择优：

        μ_seed = 切点 μ + λ ×（该袖**无上限最大收益** μ − 切点 μ)，σ_seed = 切点 σ

    - 取切点（= 该袖自己最大夏普篮子）作下界、无上限最大收益篮子作上界，
      λ ∈ (0,1) 在两者之间线性插值；**确定性、无随机、不读取任何全局**。
    - 只依赖显式入参 `pool_specs`（core 不 import params）；同输入同输出。
    返回 [(label, {key: (mu, sigma)})]；无法构造任何种子时返回 []。
    """
    cache = {}
    seeds = []
    for lam in lambdas:
        vec = {}
        for key in SLEEVE_KEYS:
            spec = pool_specs.get(key)
            if spec is None:
                continue
            got = sleeve_tangency(key, rf, pool_specs, cache=cache)
            if got is None:
                continue
            mu, vol = float(got[0]), float(got[1])
            mx = pool_max_return(spec[0], spec[1], None)
            if mx is not None and float(mx) > mu:
                mu = mu + float(lam) * (float(mx) - mu)
            vec[key] = (float(mu), float(vol))
        if vec:
            seeds.append(("lambda=%.2f" % float(lam), vec))
    return seeds
