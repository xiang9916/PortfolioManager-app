#!/usr/bin/env python3
"""Two-stage portfolio optimization."""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    from scipy.optimize import minimize
except ImportError:
    print("scipy is required.", file=sys.stderr)
    sys.exit(2)

from params import (
    BROAD_ASSETS,
    BROAD_CORR,
    RF,
    TARGET_RETURN,
    US_INTERNAL,
    US_INTERNAL_CORR,
    JP_INTERNAL,
    JP_INTERNAL_CORR,
    HK_INTERNAL,
    HK_INTERNAL_CORR,
    GC_INTERNAL,
    GC_INTERNAL_CORR,
    build_broad_cov,
    load_hsbc_fund_pool,
    us_core_params,
)



def log_step(step, message, level="info", log_path=None):
    """Append a JSONL step record for the Swift sidecar / AI to read."""
    if not log_path:
        return
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "step": step,
             "message": message, "level": level}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def portfolio_stats(weights, mu, cov):
    weights = np.asarray(weights, dtype=float)
    ret = float(weights @ mu)
    vol = float(np.sqrt(weights @ cov @ weights))
    sharpe = (ret - RF) / vol if vol > 0 else 0.0
    return ret, vol, sharpe


def load_availability(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    funds = data.get("funds")
    if not isinstance(funds, dict) or not funds:
        raise ValueError("availability JSON 中没有 funds 字段")
    return data, funds


def run_availability_check(timeout=120.0):
    script = Path(__file__).resolve().parent / "check_fund_availability.py"
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    tmp.close()
    cmd = [sys.executable, str(script), "--json", tmp.name]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(json.dumps({"error": "基金状态在线校验超时"}, ensure_ascii=False))
        sys.exit(2)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "")[-2000:]
        print(json.dumps({"error": "基金状态在线校验失败", "detail": detail}, ensure_ascii=False))
        sys.exit(2)
    return tmp.name


def filter_available_assets(assets, avail_funds, warnings):
    kept = []
    for a in assets:
        code = a.get("fund_code")
        if code is None:
            kept.append(a)
            continue
        info = avail_funds.get(code)
        if info is None:
            warnings.append(f"基金 {a['name']}（{code}）未在天天基金校验结果中，已剔除。")
            continue
        if not info.get("open", False):
            warnings.append(f"基金 {a['name']}（{code}）当前状态为 {info.get('status', '未知')}，已剔除。")
            continue
        kept.append(a)
    return kept


def feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng):
    n = len(lower)
    x = np.zeros(n)
    for idx, target in ((dom_idx, dom_w), (ov_idx, ov_w)):
        lo = lower[idx]
        hi = upper[idx]
        cap = hi - lo
        rem = target - lo.sum()
        if rem < -1e-9:
            return None
        if cap.sum() <= 0:
            if abs(rem) > 1e-9:
                return None
            x[idx] = lo
            continue
        p = rng.random(len(idx)) * cap
        if p.sum() <= 0:
            return None
        x[idx] = lo + rem * p / p.sum()
    return x


def solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, ret_target, n_starts=120, seed=7):
    n = len(mu)
    bounds = list(zip(lower, upper))
    cons = [
        {"type": "eq", "fun": lambda w: w.sum() - 1.0},
        {"type": "eq", "fun": lambda w: w[dom_idx].sum() - dom_w},
        {"type": "eq", "fun": lambda w: mu @ w - ret_target},
    ]
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(n_starts):
        start = feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng)
        if start is None:
            continue
        res = minimize(
            lambda w: w @ cov @ w,
            start,
            method="SLSQP",
            bounds=bounds,
            constraints=cons,
            options={"maxiter": 2500, "ftol": 1e-14},
        )
        w = res.x
        if abs(w.sum() - 1) > 1e-4 or abs(w[dom_idx].sum() - dom_w) > 1e-4:
            continue
        if abs(mu @ w - ret_target) > 1e-4:
            continue
        if np.any(w < -1e-6) or np.any(w > np.array(upper) + 1e-6):
            continue
        ret, vol, sharpe = portfolio_stats(w, mu, cov)
        if best is None or sharpe > best[3]:
            best = (w, ret, vol, sharpe)
    return best


def max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w, n_starts=80, seed=11):
    n = len(mu)
    bounds = list(zip(lower, upper))
    cons = [
        {"type": "eq", "fun": lambda w: w.sum() - 1.0},
        {"type": "eq", "fun": lambda w: w[dom_idx].sum() - dom_w},
    ]
    rng = np.random.default_rng(seed)
    best = -1.0
    for _ in range(n_starts):
        start = feasible_start(lower, upper, dom_idx, ov_idx, dom_w, ov_w, rng)
        if start is None:
            continue
        res = minimize(
            lambda w: -mu @ w,
            start,
            method="SLSQP",
            bounds=bounds,
            constraints=cons,
            options={"maxiter": 2000, "ftol": 1e-14},
        )
        if res.success:
            best = max(best, float(mu @ res.x))
    return best


def stage1_bounds(assets):
    n = len(assets)
    return np.zeros(n), np.ones(n)


def stage2_covariance(fixed_assets, internal_tickers, broad_cov, broad_keys, broad_vols):
    """Build covariance for fixed assets + internal US holdings."""
    keys = [a["key"] for a in fixed_assets] + internal_tickers
    n = len(keys)
    vols = np.zeros(n)
    mus = np.zeros(n)
    corr = np.eye(n)

    for i, a in enumerate(fixed_assets):
        vols[i] = a["vol"]
        mus[i] = a["mu"]

    for j, ticker in enumerate(internal_tickers):
        idx = len(fixed_assets) + j
        vols[idx] = US_INTERNAL[ticker]["vol"]
        mus[idx] = US_INTERNAL[ticker]["mu"]

    broad_corr = broad_cov / np.outer(broad_vols, broad_vols)
    for i, a in enumerate(fixed_assets):
        if a["key"] == "O_US_CORE":
            continue
        ki = a["key"]
        ii = broad_keys.index(ki)
        for j, b in enumerate(fixed_assets):
            if b["key"] == "O_US_CORE" or j <= i:
                continue
            kj = b["key"]
            jj = broad_keys.index(kj)
            corr[i, j] = corr[j, i] = broad_corr[ii, jj]

    # Fixed vs internal.
    core_col = broad_keys.index("O_US_CORE")
    for i, a in enumerate(fixed_assets):
        if a["key"] == "O_US_CORE":
            continue
        base = broad_corr[broad_keys.index(a["key"]), core_col]
        for j, ticker in enumerate(internal_tickers):
            idx = len(fixed_assets) + j
            if a["key"] == "D_HSBC_SP500":
                base_ij = {"SPLG": 0.95, "VTV": 0.85, "SPMO": 0.85, "UNH": 0.75, "GOOG": 0.75, "BUFFER": 0.60}.get(ticker, 0.75)
            else:
                base_ij = base
            corr[i, idx] = corr[idx, i] = base_ij

    # Internal-internal.
    for j1, t1 in enumerate(internal_tickers):
        for j2, t2 in enumerate(internal_tickers):
            if j1 == j2:
                continue
            i1 = len(fixed_assets) + j1
            i2 = len(fixed_assets) + j2
            corr[i1, i2] = corr[i2, i1] = US_INTERNAL_CORR.get((t1, t2), US_INTERNAL_CORR.get((t2, t1), 0.5))

    # PSD projection.
    a = (corr + corr.T) / 2.0
    vals, vecs = np.linalg.eigh(a)
    vals = np.clip(vals, 0.0, None)
    corr = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(corr))
    corr = corr / np.outer(d, d)
    corr = (corr + corr.T) / 2.0
    cov = np.outer(vols, vols) * corr
    cov += np.eye(n) * 1e-10
    return mus, cov, keys


def stage2_optimize(stage1_weights, extract, total_assets, assets, broad_cov, broad_keys, broad_vols):
    """Optimize internal US equity weights with total US core weight fixed and no per-ticker caps."""
    # Always consider the full US internal candidate pool, even for tickers the
    # Numbers file does not currently hold. Keeping file tickers first preserves
    # current-holding priority in tie cases, but the optimizer may add back
    # UNH/GOOG/VTV etc. if they improve the risk-adjusted result.
    file_tickers = [h["ticker"] for h in extract["us_equity"]["holdings"] if h["ticker"] in US_INTERNAL]
    internal_tickers = []
    for ticker in file_tickers + list(US_INTERNAL.keys()):
        if ticker not in internal_tickers:
            internal_tickers.append(ticker)
    if not internal_tickers:
        return None, "No supported US internal holdings found."

    core_idx = broad_keys.index("O_US_CORE")
    core_total = float(stage1_weights[core_idx])
    if core_total <= 1e-6:
        return None, "Stage 1 gave zero weight to US core equity."

    fixed_assets = [a for a in assets if a["key"] != "O_US_CORE"]
    fixed_weights = [
        float(stage1_weights[i])
        for i, a in enumerate(assets)
        if a["key"] != "O_US_CORE"
    ]

    mus, cov, keys = stage2_covariance(fixed_assets, internal_tickers, broad_cov, broad_keys, broad_vols)
    n_internal = len(internal_tickers)
    fixed_n = len(fixed_assets)
    internal_slice = slice(fixed_n, fixed_n + n_internal)

    def total_weights(internal_w):
        w = np.zeros(fixed_n + n_internal)
        w[:fixed_n] = fixed_weights
        w[internal_slice] = internal_w
        return w

    def objective(internal_w):
        w = total_weights(internal_w)
        _, _, sharpe = portfolio_stats(w, mus, cov)
        return -sharpe

    bounds = [(0.0, None)] * n_internal

    cons = [
        {"type": "eq", "fun": lambda x: x.sum() - core_total},
        {"type": "ineq", "fun": lambda x: mus @ total_weights(x) - TARGET_RETURN},
    ]
    rng = np.random.default_rng(19)
    best = None
    for _ in range(80):
        caps = np.array([core_total] * n_internal)
        start = np.zeros(n_internal)
        remaining = core_total
        guard = 0
        while remaining > 1e-8 and guard < 200:
            available = np.maximum(caps - start, 0.0)
            p = rng.random(n_internal) * available
            if p.sum() <= 0:
                break
            add = min(remaining, float(p.sum()))
            start += add * p / p.sum()
            start = np.minimum(start, caps)
            remaining = core_total - start.sum()
            guard += 1
        if abs(start.sum() - core_total) > 1e-6:
            continue
        res = minimize(objective, start, method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 2000, "ftol": 1e-12})
        x = res.x
        w = total_weights(x)
        if abs(x.sum() - core_total) > 1e-4:
            continue
        if np.any(x < -1e-6) or np.any(x > core_total + 1e-6):
            continue
        if mus @ w < TARGET_RETURN - 1e-4:
            continue
        ret, vol, sharpe = portfolio_stats(w, mus, cov)
        if best is None or sharpe > best[3]:
            best = (x, ret, vol, sharpe, w, internal_tickers)
    return best, None


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


def _internal_pool_optimize(stage1_weights, assets, internal_pool, internal_corr, broad_key, total_assets, seed=31):
    """Generic second-level optimization inside a broad equity bucket."""
    tickers = list(internal_pool.keys())
    if not tickers:
        return None
    if broad_key not in [a["key"] for a in assets]:
        return None
    idx = [i for i, a in enumerate(assets) if a["key"] == broad_key][0]
    total_w = float(stage1_weights[idx])
    if total_w <= 1e-6:
        return None

    n = len(tickers)
    mu = np.array([internal_pool[t]["mu"] for t in tickers])
    vols = np.array([internal_pool[t]["vol"] for t in tickers])
    corr = np.eye(n)
    for i, a in enumerate(tickers):
        for j, b in enumerate(tickers):
            if i != j:
                corr[i, j] = internal_corr.get((a, b), internal_corr.get((b, a), 0.5))
    vals, vecs = np.linalg.eigh(corr)
    vals = np.clip(vals, 0.0, None)
    corr = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.diag(corr))
    corr = corr / np.outer(d, d)
    cov = np.outer(vols, vols) * corr

    def obj(w):
        ret = float(w @ mu)
        vol = float(np.sqrt(w @ cov @ w))
        return -((ret - RF) / vol if vol > 0 else 0.0)

    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    bounds = [(0.0, 1.0)] * n
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(60):
        start = rng.random(n)
        start /= start.sum()
        res = minimize(obj, start, method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 1500, "ftol": 1e-12})
        x = res.x
        if abs(x.sum() - 1.0) > 1e-4:
            continue
        if np.any(x < -1e-6):
            continue
        ret = float(x @ mu)
        vol = float(np.sqrt(x @ cov @ x))
        sharpe = (ret - RF) / vol if vol > 0 else 0.0
        if best is None or sharpe > best[0]:
            best = (sharpe, x, ret, vol)
    if best is None:
        return None
    sharpe, x, ret, vol = best
    return {
        "broad_total_weight": total_w,
        "internal_weights_total": {t: float(x[i]) * total_w for i, t in enumerate(tickers)},
        "internal_weights_normalized": {t: float(x[i]) for i, t in enumerate(tickers)},
        "amounts": {t: float(x[i]) * total_w * total_assets for i, t in enumerate(tickers)},
        "ret": ret,
        "vol": vol,
        "sharpe": sharpe,
    }


def stage2_japan_optimize(stage1_weights, assets, total_assets):
    """Optimize Japan equity internal factor weights."""
    return _internal_pool_optimize(stage1_weights, assets, JP_INTERNAL, JP_INTERNAL_CORR, "O_JP_EQ", total_assets, seed=31)


def stage2_hk_optimize(stage1_weights, assets, total_assets):
    """Optimize Hong Kong equity internal factor weights."""
    return _internal_pool_optimize(stage1_weights, assets, HK_INTERNAL, HK_INTERNAL_CORR, "O_HK_HSTECH", total_assets, seed=37)


def stage2_gc_optimize(stage1_weights, assets, total_assets):
    """Optimize Greater China equity internal (沪深300/红利低波/主动/混合/指数/港股宽基/高股息/科技)."""
    return _internal_pool_optimize(stage1_weights, assets, GC_INTERNAL, GC_INTERNAL_CORR, "D_GREATER_CN", total_assets, seed=43)


def build_result_summary(output):
    """Build a clean Finance result file: per-asset weights/return/vol/sharpe + totals."""
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
            "name": f"{a['name']} → {sel['selected']['code']} {sel['selected']['name']}",
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
            "name": info["name"],
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
                "name": f"日本·{info['name']}",
                "weight": w,
                "expected_return": mu,
                "volatility": vol,
                "sharpe": (mu - RF) / vol if vol else 0.0,
            })

    # Hong Kong equity internal factor split
    hk = output.get("hk_selection")
    if hk is not None:
        for ticker, w in hk.get("internal_weights_total", {}).items():
            if w <= 1e-8:
                continue
            info = HK_INTERNAL.get(ticker)
            if info is None:
                continue
            mu = info["mu"]
            vol = info["vol"]
            assets.append({
                "key": f"HK:{ticker}",
                "name": f"香港·{info['name']}",
                "weight": w,
                "expected_return": mu,
                "volatility": vol,
                "sharpe": (mu - RF) / vol if vol else 0.0,
            })

    # Greater China equity internal factor split
    gc = output.get("gc_selection")
    if gc is not None:
        for ticker, w in gc.get("internal_weights_total", {}).items():
            if w <= 1e-8:
                continue
            info = GC_INTERNAL.get(ticker)
            if info is None:
                continue
            mu = info["mu"]
            vol = info["vol"]
            assets.append({
                "key": f"GC:{ticker}",
                "name": f"大中华·{info['name']}",
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
        assets.append({
            "key": key,
            "name": a["name"],
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - RF) / vol if vol else 0.0,
        })
    assets.sort(key=lambda x: -x["weight"])
    for a in assets:
        a["weight"] = round(float(a["weight"]), 6)
        a["expected_return"] = round(float(a["expected_return"]), 6)
        a["volatility"] = round(float(a["volatility"]), 6)
        a["sharpe"] = round(float(a["sharpe"]), 6)
    final = output.get("final") or {}
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
        keys = [a["key"] for a in broad]
        mu = np.array([a["mu"] if a["mu"] is not None else 0.0 for a in broad])
        vols = np.array([a["vol"] if a["vol"] is not None else 0.0 for a in broad])
        cov = build_broad_cov([{"key": k, "vol": v} for k, v in zip(keys, vols)], BROAD_CORR)

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("extract_json")
    parser.add_argument("--total-assets", type=float, default=None)
    parser.add_argument(
        "--availability",
        help="天天基金校验 JSON；缺省时自动运行 check_fund_availability.py",
    )
    parser.add_argument("--check-timeout", type=float, default=120.0)
    parser.add_argument(
        "--hsbc-funds",
        default="/Users/sectator/MEGA/Finance/tmp/hsbc_open_funds.json",
        help="基金搜索易开放申购基金 JSON 路径",
    )
    parser.add_argument("--json", dest="out_path", help="Write detailed JSON output to file")
    parser.add_argument("--result-json", dest="result_path", help="Write clean summary JSON to file")
    parser.add_argument("--log", dest="log_path", help="JSONL step log path")
    args = parser.parse_args()

    with open(args.extract_json, encoding="utf-8") as f:
        extract = json.load(f)
    log_step("load_extract", f"已加载提取结果 total={extract.get('total_value')}", log_path=args.log_path)

    warnings = list(extract.get("warnings", []))

    hsbc_pool = {}
    if args.hsbc_funds and Path(args.hsbc_funds).exists():
        try:
            hsbc_pool = load_hsbc_fund_pool(args.hsbc_funds)
            pool_count = sum(len(v) for v in hsbc_pool.values())
            log_step("load_hsbc_pool", f"已加载开放基金池 {pool_count} 只", log_path=args.log_path)
            warnings.append(f"已加载基金搜索易开放基金池：{pool_count} 只。")
        except Exception as exc:
            warnings.append(f"基金搜索易开放基金池加载失败：{exc}")
    else:
        warnings.append("未找到基金搜索易开放基金池 JSON，境内基金内部选择将只使用原代表性基金。")

    if args.availability:
        try:
            availability, avail_funds = load_availability(args.availability)
        except (OSError, ValueError) as exc:
            print(
                    json.dumps(
                    {"error": "无法读取天天基金校验结果", "detail": str(exc)},
                    ensure_ascii=False,
                )
            )
            sys.exit(2)
        warnings.append(f"已读取天天基金校验结果：{args.availability}")
    else:
        availability_path = run_availability_check(timeout=args.check_timeout)
        try:
            availability, avail_funds = load_availability(availability_path)
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {"error": "天天基金校验结果不可用", "detail": str(exc)},
                    ensure_ascii=False,
                )
            )
            sys.exit(2)
        warnings.append("已自动通过天天基金校验境内基金申购状态。")
    log_step("availability", "天天基金申购状态校验完成", log_path=args.log_path)

    dom_w = float(extract["domestic_weight"])
    ov_w = float(extract["overseas_weight"])

    internal_weights = {h["ticker"]: h["weight"] for h in extract["us_equity"]["holdings"]}
    core_mu, core_vol = us_core_params(internal_weights)

    assets = []
    for a in BROAD_ASSETS:
        item = dict(a)
        if a["key"] == "O_US_CORE":
            item["mu"] = core_mu
            item["vol"] = core_vol
        assets.append(item)
    assets = filter_available_assets(assets, avail_funds, warnings)
    log_step("build_assets", f"构建大类资产 {len(assets)} 项", log_path=args.log_path)
    if not any(a["pool"] == "domestic" for a in assets):
        print(
            json.dumps(
                {"error": "没有通过天天基金校验的境内基金", "warnings": warnings},
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    keys = [a["key"] for a in assets]
    mu = np.array([a["mu"] for a in assets])
    vols = np.array([a["vol"] for a in assets])
    cov = build_broad_cov([{"key": a["key"], "vol": a["vol"]} for a in assets], BROAD_CORR)
    dom_idx = [i for i, a in enumerate(assets) if a["pool"] == "domestic"]
    ov_idx = [i for i, a in enumerate(assets) if a["pool"] == "overseas"]

    log_step("stage1_broad", "第一阶段：大类资产均值-方差优化", log_path=args.log_path)
    lower, upper = stage1_bounds(assets)
    target = TARGET_RETURN
    solved = solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target)
    if solved is None:
        max_ret = max_feasible_return(mu, lower, upper, dom_idx, ov_idx, dom_w, ov_w)
        warnings.append(f"10% 目标在当前约束下不可行，已回退到最大可行收益 {max_ret:.2%}。")
        target = max_ret - 1e-6
        solved = solve_min_var(mu, cov, lower, upper, dom_idx, ov_idx, dom_w, ov_w, target, seed=23)

    if solved is None:
        print(json.dumps({"error": "Optimization infeasible", "warnings": warnings}, ensure_ascii=False, indent=2))
        sys.exit(1)

    w1, ret1, vol1, sharpe1 = solved
    log_step("stage1_result", f"第一阶段完成 收益={ret1:.2%} 波动={vol1:.2%} 夏普={sharpe1:.3f}", log_path=args.log_path)
    total_assets = args.total_assets or float(extract["total_value"])

    domestic_selection = select_domestic_funds(w1, assets, hsbc_pool, total_assets)
    log_step("select_domestic", "境内基金选择完成", log_path=args.log_path)

    japan_selection = stage2_japan_optimize(w1, assets, total_assets)
    hk_selection = stage2_hk_optimize(w1, assets, total_assets)
    gc_selection = stage2_gc_optimize(w1, assets, total_assets)

    log_step("stage2_internal", "第二阶段：美国权益内部标的优化", log_path=args.log_path)
    internal, stage2_error = stage2_optimize(
        w1,
        extract,
        total_assets,
        assets,
        cov,
        keys,
        vols,
    )
    final = None
    if internal is not None:
        x_internal, ret2, vol2, sharpe2, w2, internal_tickers = internal
        final = {
            "ret": ret2,
            "vol": vol2,
            "sharpe": sharpe2,
            "floor": ret2 - 1.96 * vol2,
        }

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
        "availability": {
            "source": availability.get("source"),
            "base_url": availability.get("base_url"),
            "checked_at": availability.get("checked_at"),
            "funds": avail_funds,
        },
        "stage1": stage1_detail,
        "stage2": None,
        "domestic_selection": domestic_selection,
        "japan_selection": japan_selection,
        "hk_selection": hk_selection,
        "gc_selection": gc_selection,
        "final": final,
        "warnings": warnings,
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
    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))

    if args.result_path:
        summary = build_result_summary(output)
        if args.out_path:
            summary["source_detail"] = args.out_path
        with open(args.result_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
