#!/usr/bin/env python3
"""Fetch all funds from HSBC China's 基金搜索易 and save the open-subscription list.

Source: https://www.hsbc.com.cn/investments/products/3rd-party/local-unit-trust/
The underlying Morningstar-powered search tool is:
  http://fundsresearch.investments.hsbc.com.cn/rbwm/QuickRank.aspx?fund=
Using pageSize=2147483647 returns the full table in one page.

每次优化运行都会调用本脚本实时抓取 (见 fund_pipeline.py) — 不再依赖预存快照。
解析用纯 stdlib html.parser (打包 app 的 venv 不保证安装 BeautifulSoup)。
"""

import argparse
import json
import re
import ssl
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

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


class _TableCollector(HTMLParser):
    """Collect every <table> as a list of rows, each row a list of cell texts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_funds(html):
    collector = _TableCollector()
    collector.feed(html)
    if not collector.tables:
        return []
    data_table = max(collector.tables, key=len)
    funds = []
    for cells in data_table:
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
