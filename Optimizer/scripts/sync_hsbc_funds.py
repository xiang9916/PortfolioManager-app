#!/usr/bin/env python3
"""Fetch all funds from HSBC China's 基金搜索易 and save the open-subscription list.

Source: https://www.hsbc.com.cn/investments/products/3rd-party/local-unit-trust/
The underlying Morningstar-powered search tool is:
  http://fundsresearch.investments.hsbc.com.cn/rbwm/QuickRank.aspx?fund=
Using pageSize=2147483647 returns the full table in one page.
"""

import argparse
import json
import re
import ssl
import urllib.request
from datetime import datetime, timezone

from bs4 import BeautifulSoup

BASE_URL = "http://fundsresearch.investments.hsbc.com.cn/rbwm/QuickRank.aspx?fund=&pageSize=2147483647"


def fetch_html(timeout=60.0):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(BASE_URL, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Referer": "https://www.hsbc.com.cn/investments/products/3rd-party/local-unit-trust/",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_funds(html):
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return []
    data_table = max(tables, key=lambda t: len(t.find_all("tr")))
    funds = []
    for tr in data_table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if not cells or not re.match(r"^\d{6}$", cells[0]):
            continue
        code = cells[0]
        name = cells[1]
        status = "暂停申购" if "暂停申购" in name else "开放申购"
        funds.append({
            "code": code,
            "name": name,
            "status": status,
            "open": status == "开放申购",
        })
    return funds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None, help="Output JSON path")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    html = fetch_html(timeout=args.timeout)
    funds = parse_funds(html)
    open_funds = [f for f in funds if f["open"]]
    result = {
        "source": "基金搜索易（HSBC China / Morningstar）",
        "url": BASE_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total_count": len(funds),
        "open_count": len(open_funds),
        "suspended_count": len(funds) - len(open_funds),
        "funds": open_funds,
    }

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps({
            "saved": args.json,
            "total_count": len(funds),
            "open_count": len(open_funds),
            "suspended_count": len(funds) - len(open_funds),
        }, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
