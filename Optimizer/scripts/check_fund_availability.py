#!/usr/bin/env python3
"""Query 天天基金 for domestic fund subscription status.

Fast single-page check: https://fundf10.eastmoney.com/jbgk_<code>.html
"""

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone

STATUS_RE = re.compile(r"交易状态[：:]\s*<span[^>]*>([^<]+)</span>")


def fetch_fund(code, timeout=15.0):
    url = f"https://fundf10.eastmoney.com/jbgk_{code}.html"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Referer": "https://fund.eastmoney.com/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    text = raw.decode("utf-8", errors="replace")

    m = STATUS_RE.search(text)
    if m is None:
        return {
            "code": code,
            "name": None,
            "status": "查询失败",
            "open": False,
            "error": "页面中未找到交易状态",
        }

    status = html.unescape(m.group(1)).strip()
    if "暂停申购" in status:
        return {"code": code, "name": None, "status": "暂停申购", "open": False}
    if "限大额" in status or "限制大额" in status:
        return {"code": code, "name": None, "status": "限大额", "open": True}
    if "开放申购" in status or "开放" in status:
        return {"code": code, "name": None, "status": "开放申购", "open": True}
    return {
        "code": code,
        "name": None,
        "status": status or "未知",
        "open": "暂停" not in status,
    }


def default_codes():
    try:
        from params import BROAD_ASSETS
    except ImportError:
        return []
    return [
        a["fund_code"]
        for a in BROAD_ASSETS
        if a.get("fund_code") and a["pool"] == "domestic"
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", help="逗号分隔的基金代码，缺省使用 params.py 境内基金")
    parser.add_argument("--json", dest="out_path", help="写入 JSON 文件")
    parser.add_argument("--timeout", type=float, default=15.0, help="单个基金请求超时秒数")
    args = parser.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = default_codes()
    if not codes:
        print("No fund codes provided.", file=sys.stderr)
        sys.exit(2)

    funds = {}
    for code in codes:
        try:
            info = fetch_fund(code, timeout=args.timeout)
        except Exception as exc:
            info = {
                "code": code,
                "name": None,
                "status": "查询失败",
                "open": False,
                "error": str(exc),
            }
        funds[code] = info

    result = {
        "source": "天天基金",
        "base_url": "https://fundf10.eastmoney.com/jbgk_{code}.html",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "funds": funds,
    }

    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
