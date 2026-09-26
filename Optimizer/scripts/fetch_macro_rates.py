#!/usr/bin/env python3
"""Fetch CN/US 10-year treasury yields for dynamic RF — 纯标准库实现.

数据源: 东方财富数据中心「中美国债收益率」(type=RPTA_WEB_TREASURYYIELD),
与 akshare.bond_zh_us_rate 同源, 但只用 urllib/json, 不再依赖 akshare/pandas/requests.

Outputs JSON: {"cn_10y": 0.0167, "us_10y": 0.0517, "date": "2026-09-25", "source": "eastmoney"}
Yields are fractions (e.g. 0.0469 = 4.69%) for RF = ov_w*us_10y + dom_w*cn_10y.
接口返回「百分点」(4.69 = 4.69%), 故除以 100.
"""
import json
import sys
import urllib.parse
import urllib.request

API = "https://datacenter.eastmoney.com/api/data/get"
PARAMS = {
    "type": "RPTA_WEB_TREASURYYIELD",
    "sty": "ALL",
    "st": "SOLAR_DATE",
    "sr": "-1",
    "token": "894050c76af8597a853f5b408b759f5d",  # 东财公开 token (akshare 同款)
    "p": "1",
    "ps": "60",  # 取最近 60 个交易日, 足够覆盖两个市场都已有数据的最近一天
}
CN_FIELD = "EMM00166466"  # 中国国债收益率10年
US_FIELD = "EMG00001310"  # 美国国债收益率10年


def _pct(row, field):
    """把某字段转成小数 (4.69 -> 0.0469); 缺失/非数值返回 None."""
    try:
        return float(row[field]) / 100.0
    except (KeyError, TypeError, ValueError):
        return None


def main():
    url = API + "?" + urllib.parse.urlencode(PARAMS)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            rows = (json.load(resp).get("result") or {}).get("data") or []
    except Exception as exc:
        print(json.dumps({"error": f"eastmoney fetch failed: {exc}"}), file=sys.stderr)
        return 1
    if not rows:
        print(json.dumps({"error": "no data"}), file=sys.stderr)
        return 1

    # rows 已按日期降序; 中美休市日不同步, 两个市场各取自己最近一个有值的交易日.
    def latest(field):
        return next((v for v in (_pct(r, field) for r in rows) if v is not None), None)

    result = {
        "cn_10y": latest(CN_FIELD),
        "us_10y": latest(US_FIELD),
        "date": str(rows[0].get("SOLAR_DATE", ""))[:10],
        "source": "eastmoney",
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
