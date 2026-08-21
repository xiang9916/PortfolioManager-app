#!/usr/bin/env python3
"""Monte Carlo simulation: 60-year compound growth for A-E portfolio scenarios.

Each scenario has arithmetic mean return (mu) and volatility (sigma).
We simulate annual returns from a lognormal distribution matching those moments,
compound over 60 years, and report median terminal wealth, median CAGR,
and the geometric return approximation mu - sigma^2/2.
"""

import argparse
import json
import numpy as np


def lognormal_params(mu, sigma):
    """Given arithmetic mean (mu) and std (sigma) of simple returns,
    return lognormal parameters (nu, s) such that E[R]=mu, Std[R]=sigma."""
    m = 1 + mu
    var_ratio = sigma**2 / m**2
    s2 = np.log(1 + var_ratio)
    nu = np.log(m) - s2 / 2
    return nu, np.sqrt(s2)


def run_monte_carlo(mu, sigma, years=60, n_sims=100_000, seed=42):
    """Simulate n_sims paths of 'years' annual returns from lognormal(mu, sigma).
    Returns median terminal wealth, median CAGR, mean terminal wealth."""
    rng = np.random.default_rng(seed)
    nu, s = lognormal_params(mu, sigma)
    # Generate (n_sims, years) array of log-returns
    log_returns = rng.normal(nu, s, size=(n_sims, years))
    # Compound: terminal wealth = prod(1+R) = exp(sum(log_returns))
    log_wealth = log_returns.sum(axis=1)  # sum of log-returns
    terminal = np.exp(log_wealth)
    # Median terminal wealth
    median_wealth = float(np.median(terminal))
    # Median CAGR = exp(median log-wealth / years) - 1
    median_log_cagr = float(np.median(log_wealth)) / years
    median_cagr = np.exp(median_log_cagr) - 1
    # Mean terminal wealth
    mean_wealth = float(np.mean(terminal))
    # Approximate geometric return (classic formula)
    geom_approx = mu - sigma**2 / 2
    # Exact lognormal median log-growth
    exact_nu = nu
    exact_cagr = np.exp(exact_nu) - 1
    return {
        "median_terminal_wealth": round(median_wealth, 2),
        "median_cagr": round(float(median_cagr), 6),
        "mean_terminal_wealth": round(mean_wealth, 2),
        "geometric_approx": round(float(geom_approx), 6),
        "exact_lognormal_cagr": round(float(exact_cagr), 6),
        "terminal_multiple": round(median_wealth, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="60年蒙特卡洛模拟：A-E 方案复利对比")
    parser.add_argument("--years", type=int, default=60, help="投资年限（默认60）")
    parser.add_argument("--sims", type=int, default=100000, help="模拟次数（默认100000）")
    parser.add_argument("--out", dest="out_path", default=None, help="输出 JSON 路径")
    args = parser.parse_args()

    # A-E scenarios from user's data
    scenarios = [
        {"label": "A", "mu": 0.132, "sigma": 0.4169, "sharpe": 0.224, "worst_year_95": -0.6851},
        {"label": "B", "mu": 0.1308, "sigma": 0.3061, "sharpe": 0.301, "worst_year_95": -0.4691},
        {"label": "C", "mu": 0.12, "sigma": 0.1638, "sharpe": 0.496, "worst_year_95": -0.2010},
        {"label": "D", "mu": 0.11, "sigma": 0.1311, "sharpe": 0.543, "worst_year_95": -0.1470},
        {"label": "E", "mu": 0.10, "sigma": 0.1058, "sharpe": 0.578, "worst_year_95": -0.1074},
    ]

    results = []
    for sc in scenarios:
        mc = run_monte_carlo(sc["mu"], sc["sigma"], years=args.years, n_sims=args.sims)
        results.append({
            "label": sc["label"],
            "mu": sc["mu"],
            "sigma": sc["sigma"],
            "sharpe": sc["sharpe"],
            "worst_year_95": sc["worst_year_95"],
            **mc,
        })

    # Sort by median terminal wealth descending
    results.sort(key=lambda x: -x["median_terminal_wealth"])

    # Determine winner
    winner = results[0]["label"]

    output = {
        "years": args.years,
        "n_sims": args.sims,
        "winner": winner,
        "ranking": [r["label"] for r in results],
        "scenarios": results,
    }

    out = json.dumps(output, ensure_ascii=False, indent=2)
    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
