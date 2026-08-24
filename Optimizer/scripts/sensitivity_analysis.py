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
from pathlib import Path

# Add scripts dir to path so params can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

import math

import numpy as np
try:
    from scipy.optimize import minimize
    from scipy import integrate, stats
except ImportError:
    print("scipy required for sensitivity analysis.", file=sys.stderr)
    sys.exit(2)

from params import (
    BROAD_ASSETS, BROAD_CORR, RF, TARGET_RETURN,
    build_broad_cov, classify_domestic_fund,
    build_open_pool_from_status,
    us_core_params,
)
from optimize_common import (
    portfolio_stats,
    feasible_start,
    solve_min_var,
    max_feasible_return,
    stage1_bounds,
)
from fund_pipeline import (
    ensure_pipeline,
    ensure_work_dir,
    filter_available_assets,
    load_pipeline,
)


def _stderr_log(step, message, level="info"):
    print(f"[{step}] {message}", file=sys.stderr)


def load_extract_assets(extract, work_dir, hsbc_request_timeout=60.0,
                        request_timeout=12.0, check_total_timeout=300.0, log=None):
    """复用/重建联网管线, 按优化同一口径构建 assets 与开放基金池。

    返回 (assets, hsbc_pool, warnings, pipeline_info)。
    """
    log = log or _stderr_log
    warnings = list(extract.get("warnings", []))

    # live_anchors: 用户实际持仓的境内基金锚定 (与 optimize_portfolio 同一逻辑)
    live_anchors = {}
    for h in extract.get("domestic_holdings", []):
        cat = classify_domestic_fund(h.get("name", ""))
        live_anchors[cat] = h.get("code")

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

    assets = []
    for a in BROAD_ASSETS:
        if a["pool"] == "domestic" and hsbc_pool and a["key"] not in hsbc_pool:
            cat = pool_meta["categories"].get(a["key"])
            if cat:
                warnings.append(
                    f"境内大类 {a['name']}（{a['key']}）的 {len(cat.get('tried', []))} 只候选在天天基金"
                    f"全部为暂停申购/校验失败，已剔除。")
            else:
                warnings.append(f"境内大类 {a['name']}（{a['key']}）在汇丰在售池中无候选基金，已剔除。")
            continue
        item = dict(a)
        if item["key"] == "O_US_CORE":
            item["mu"] = core_mu
            item["vol"] = core_vol
        assets.append(item)
    assets = filter_available_assets(assets, avail_funds, warnings, hsbc_status)

    pipeline_info = {
        "work_dir": str(work),
        "reused_cache": cached is not None,
        "hsbc_fetched_at": hsbc_data.get("fetched_at"),
        "availability_checked_at": availability.get("checked_at"),
        "checked_codes": len(avail_funds),
        "open_pool_categories": len(hsbc_pool),
    }
    return assets, hsbc_pool, warnings, pipeline_info


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


def run_sensitivity(extract, work_dir=None, hsbc_request_timeout=60.0,
                    request_timeout=12.0, check_total_timeout=300.0,
                    step_min=0.05, step_max=None, tail_dof=3.0, log=None):
    """Run sensitivity analysis from step_min to max feasible return, 1% increments."""
    global RF

    # RF override from extract
    rf = extract.get("rf")
    if rf is not None:
        RF = float(rf)

    dom_w = float(extract["domestic_weight"])
    ov_w = float(extract["overseas_weight"])

    # 与 optimize_portfolio.py 同一资产口径 (联网管线复用/重建 + 暂停申购大类剔除)
    assets, _hsbc_pool, warnings, pipeline_info = load_extract_assets(
        extract, work_dir,
        hsbc_request_timeout=hsbc_request_timeout,
        request_timeout=request_timeout,
        check_total_timeout=check_total_timeout,
        log=log,
    )

    keys = [a["key"] for a in assets]
    mu = np.array([a["mu"] for a in assets])
    vols = np.array([a["vol"] for a in assets])
    cov = build_broad_cov([{"key": a["key"], "vol": a["vol"]} for a in assets], BROAD_CORR)
    dom_idx = [i for i, a in enumerate(assets) if a["pool"] == "domestic"]
    ov_idx = [i for i, a in enumerate(assets) if a["pool"] == "overseas"]

    lower, upper = stage1_bounds(assets)

    # Find max feasible return
    max_ret = max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w)
    if max_ret <= 0:
        return {"error": "无法计算最大可行收益率", "points": [],
                "warnings": warnings, "pipeline": pipeline_info}

    # Determine step range
    if step_max is not None:
        upper_bound = min(step_max, max_ret)
    else:
        upper_bound = max_ret

    # Generate targets: 5%, 5.5%, 6%, ..., up to floor(max_ret)
    targets = []
    t = step_min
    while t <= upper_bound + 1e-9:
        targets.append(round(t, 4))
        t += 0.005

    # Always include the max feasible as last point
    if max_ret > 0 and (not targets or abs(targets[-1] - max_ret) > 0.005):
        targets.append(round(max_ret, 4))

    points = []
    for target in targets:
        solved = solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, seed=23, rf=RF)
        if solved is not None:
            w, ret, vol, sharpe = solved
            worst = ret - 1.96 * vol
            points.append({
                "target_return": round(target, 4),
                "feasible": True,
                "achieved_return": round(ret, 6),
                "volatility": round(vol, 6),
                "sharpe": round(sharpe, 6),
                "worst_year_95": round(worst, 6),
                "cagr_lognormal": round(lognormal_cagr(ret, vol), 6),
                # 键名必须是 cagr_fat_tail（蛇形→驼峰得 cagrFatTail）：
                # cagr_fattail 会转成 cagrFattail，与 Swift 属性 cagrFatTail
                # 不匹配，可选字段静默解码为 nil → UI 回退旧近似。
                "cagr_fat_tail": round(fattail_cagr(ret, vol, nu=tail_dof), 6),
            })
        else:
            points.append({
                "target_return": round(target, 4),
                "feasible": False,
                "achieved_return": None,
                "volatility": None,
                "sharpe": None,
                "worst_year_95": None,
            })

    return {
        "max_feasible_return": round(max_ret, 6),
        "tail_dof": float(tail_dof),
        "rf": round(RF, 6),
        "domestic_weight": round(dom_w, 6),
        "overseas_weight": round(ov_w, 6),
        "points": points,
        "warnings": warnings,
        "pipeline": pipeline_info,
    }


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
    parser.add_argument("--out", dest="out_path", default=None, help="输出 JSON 路径")
    args = parser.parse_args()

    with open(args.extract_json, encoding="utf-8") as f:
        extract = json.load(f)

    result = run_sensitivity(
        extract,
        work_dir=args.work_dir,
        hsbc_request_timeout=args.hsbc_timeout,
        request_timeout=args.request_timeout,
        check_total_timeout=args.check_timeout,
        step_min=args.step_min,
        step_max=args.step_max,
        tail_dof=args.tail_dof,
    )

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
