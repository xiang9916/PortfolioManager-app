#!/usr/bin/env python3
"""Fetch fund fees / scale / inception for HSBC open-fund candidates.

Reads:  /Users/sectator/MEGA/Finance/tmp/hsbc_open_funds.json
Writes: /Users/sectator/MEGA/Finance/tmp/hsbc_fund_fees.json

Data sources (Eastmoney):
  - jjfl_{code}.html  -> purchase / management / custody / sales-service fees
  - jbgk_{code}.html  -> net asset scale and inception date
"""

import argparse
import json
import re
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

BASE = "https://fundf10.eastmoney.com"


def http_get(url, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            import time
            time.sleep(1.0)
    raise last


def parse_pct(s):
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)%", s or "")
    return float(m.group(1)) / 100.0 if m else None


def fetch_one(code):
    out = {"code": code, "name": None, "purchase_fee": None,
           "management_fee": None, "custody_fee": None, "sales_fee": None,
           "annual_cost": None, "aum_yi": None, "inception": None, "error": None}
    # Fee page (jjfl) — independent try so a jbgk failure doesn't lose fees.
    try:
        fee_html = http_get(f"{BASE}/jjfl_{code}.html")
        m = re.search(r'<b class="sourcerate">\s*([0-9.]+%)\s*</b>', fee_html)
        out["purchase_fee"] = parse_pct(m.group(1)) if m else None
        m = re.search(r"管理费率</td><td[^>]*>([0-9.]+%（每年）)", fee_html)
        out["management_fee"] = parse_pct(m.group(1)) if m else None
        m = re.search(r"托管费率</td><td[^>]*>([0-9.]+%（每年）)", fee_html)
        out["custody_fee"] = parse_pct(m.group(1)) if m else None
        m = re.search(r"销售服务费率</td><td[^>]*>([0-9.]+%（每年）)", fee_html)
        out["sales_fee"] = parse_pct(m.group(1)) if m else None
        annual = 0.0
        for v in (out["management_fee"], out["custody_fee"], out["sales_fee"]):
            if v is not None:
                annual += v
        out["annual_cost"] = round(annual, 4) if (out["management_fee"] is not None) else None
    except Exception as e:
        out["error"] = f"jjfl {type(e).__name__}: {e}"

    # Profile page (jbgk) — scale and inception.
    try:
        gk_html = http_get(f"{BASE}/jbgk_{code}.html")
        m = re.search(r"净资产规模：\s*<span>\s*([0-9.]+)亿元", gk_html)
        out["aum_yi"] = float(m.group(1)) if m else None
        m = re.search(r"成立日期：\s*<span>([0-9]{4}-[0-9]{2}-[0-9]{2})", gk_html)
        out["inception"] = m.group(1) if m else None
    except Exception as e:
        err = out.get("error") or ""
        out["error"] = (err + f" | jbgk {type(e).__name__}: {e}").strip(" |")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/Users/sectator/MEGA/Finance/tmp/hsbc_open_funds.json")
    parser.add_argument("--output", default="/Users/sectator/MEGA/Finance/tmp/hsbc_fund_fees.json")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    funds = data.get("funds", data if isinstance(data, list) else [])
    if args.limit:
        funds = funds[: args.limit]

    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, str(f["code"])): f for f in funds}
        done = 0
        for fut in as_completed(futs):
            f = futs[fut]
            r = fut.result()
            r["name"] = f.get("name")
            results[r["code"]] = r
            done += 1
            if done % 50 == 0:
                print(f"progress {done}/{len(funds)}", flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "fees": results,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    n_ok = sum(1 for r in results.values() if not r["error"])
    print(f"done {n_ok}/{len(results)} ok -> {args.output}")


if __name__ == "__main__":
    main()
