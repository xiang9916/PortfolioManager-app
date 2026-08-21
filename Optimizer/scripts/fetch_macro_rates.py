#!/usr/bin/env python3
"""Fetch CN/US 10-year treasury yields via akshare for dynamic RF.

Outputs JSON: {"cn_10y": 0.0123, "us_10y": 0.0469, "date": "2026-08-20", "source": "akshare"}
Yields are returned as fractions (e.g. 0.0469 = 4.69%) for direct use in RF = ov_w*us_10y + dom_w*cn_10y.

akshare.bond_zh_us_rate returns 百分点 numbers (e.g. 4.69 = 4.69%), so we divide by 100.
"""
import json
import sys

try:
    import akshare as ak
except ImportError:
    print(json.dumps({"error": "akshare not installed"}), file=sys.stderr)
    sys.exit(2)


def main():
    try:
        df = ak.bond_zh_us_rate(start_date="20260101")
    except Exception as exc:
        print(json.dumps({"error": f"akshare fetch failed: {exc}"}), file=sys.stderr)
        sys.exit(1)
    if df is None or df.empty:
        print(json.dumps({"error": "no data"}), file=sys.stderr)
        sys.exit(1)
    latest = df.iloc[-1]
    date = str(latest["日期"])
    # akshare returns 百分点 (e.g. 4.69); convert to fraction.
    # NaN check: pandas NaN != NaN, so a != a means missing.
    cn_raw = latest["中国国债收益率10年"]
    cn_10y = float(cn_raw) / 100.0 if cn_raw == cn_raw else None
    us_raw = latest["美国国债收益率10年"]
    us_10y = float(us_raw) / 100.0 if us_raw == us_raw else None
    # Fallback: if us_10y missing in latest row (US market not yet updated),
    # walk backwards to find the most recent row with non-null US 10y.
    if us_10y is None:
        for i in range(len(df) - 2, -1, -1):
            v = df.iloc[i]["美国国债收益率10年"]
            if v == v:
                us_10y = float(v) / 100.0
                break
    result = {"cn_10y": cn_10y, "us_10y": us_10y, "date": date, "source": "akshare"}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
