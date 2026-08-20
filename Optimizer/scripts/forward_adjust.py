#!/usr/bin/env python3
"""Forward-looking adjustment using aistockresearcher.

Reads:
  /Users/sectator/MEGA/Finance/tmp/calibrated_params.json

Uses aistockresearcher (if importable) to fetch:
  - market regime (牛/熊/震荡) for sh000001
  - index valuation percentile (PE/PB) for sh000300
  - macro scenarios

Then applies a small forward-strength adjustment to asset expected returns so
that the portfolio reflects the current market position instead of mechanically
following long-run history. Default strength = 0.3.

Output:
  /Users/sectator/MEGA/Finance/tmp/forward_adjusted.json
"""

import argparse
import json
import sys
from pathlib import Path

BASE = "/Users/sectator/MEGA/Finance/tmp"
CALIB = Path(BASE) / "calibrated_params.json"
OUT = Path(BASE) / "forward_adjusted.json"
AISTOCK = "/Users/sectator/.dsh/skills/aistockresearcher/scripts"
FORWARD_STRENGTH = 0.3


def load_calib():
    with open(CALIB, encoding="utf-8") as f:
        return json.load(f)


def try_import():
    if AISTOCK not in sys.path:
        sys.path.insert(0, AISTOCK)
    try:
        from stock_researcher import (
            classify_market_regime,
            classify_index_valuation,
            analyze_scenarios,
        )
        return classify_market_regime, classify_index_valuation, analyze_scenarios
    except Exception:
        return None, None, None


def adjust_mu(mu, regime, valuation_pct, strength=FORWARD_STRENGTH):
    """Small forward adjustment around the long-run mu."""
    delta = 0.0
    # Regime tilt (max ±0.01 before strength scaling)
    if regime == "牛市":
        delta += 0.01
    elif regime == "熊市":
        delta -= 0.015
    # Valuation percentile tilt (max ±0.01)
    if valuation_pct is not None:
        if valuation_pct > 0.8:
            delta -= 0.01
        elif valuation_pct < 0.2:
            delta += 0.01
    return round(mu + strength * delta, 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strength", type=float, default=FORWARD_STRENGTH)
    args = parser.parse_args()

    calib = load_calib()
    assets = dict(calib.get("assets", {}))
    info = {"loaded": True, "strength": args.strength}

    classify_market_regime, classify_index_valuation, analyze_scenarios = try_import()
    if classify_market_regime is None:
        info["note"] = "aistockresearcher 不可用，未做前瞻调整"
        info["assets"] = assets
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        print("no aistockresearcher, wrote original")
        return

    regime = None
    valuation_pct = None
    scenario = None
    try:
        rr = classify_market_regime("sh000001")
        regime = rr.regime if hasattr(rr, "regime") else None
    except Exception as e:
        info["regime_error"] = str(e)
    try:
        vr = classify_index_valuation("sh000300")
        # try common attribute names
        for attr in ("pe_percentile", "pb_percentile", "percentile", "pe_pct", "pb_pct"):
            if hasattr(vr, attr):
                valuation_pct = getattr(vr, attr)
                break
    except Exception as e:
        info["valuation_error"] = str(e)
    try:
        scenario = analyze_scenarios(base_return=0.10, base_vol=0.10)
    except Exception as e:
        info["scenario_error"] = str(e)

    # Adjust equity-ish assets (apply to all is fine; strength is small).
    for key, a in assets.items():
        if isinstance(a, dict) and "mu" in a:
            a["mu"] = adjust_mu(a["mu"], regime, valuation_pct, args.strength)

    info.update({
        "regime": regime,
        "valuation_percentile": valuation_pct,
        "scenario": str(scenario)[:400] if scenario else None,
        "assets": assets,
    })
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    print(json.dumps({"regime": regime, "valuation_percentile": valuation_pct, "adjusted": len(assets)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
