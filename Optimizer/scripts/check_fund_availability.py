#!/usr/bin/env python3
"""Query 天天基金 for domestic fund subscription status (实时联网, 限流友好).

数据源: https://fundf10.eastmoney.com/jbgk_<code>.html 的"交易状态"字段。

两种运行模式:
  --codes / --codes-file   全量模式: 校验给定代码列表 (审计用, 顺序+节流+重试)
  --plan-file              分组早停模式: 按 [{"category","codes":[...]}] 逐组校验,
                           每组查到第一只"开放申购"即停 — 优化运行的标准路径,
                           请求量从 354 只降到 ~40 只, 避开天天基金频控 (HTTP 514)。

状态语义:
  开放申购 / 限大额   → open=True
  暂停申购            → open=False
  无数据              → 天天基金页面无该基金交易状态 (如 968xxx 香港互认基金),
                        open=None, 由调用方决定回退口径 (汇丰搜索易实时状态)
  查询失败            → 网络/频控, open=False + error 字段, 带退避重试
"""

import argparse
import html
import json
import random
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

STATUS_RE = re.compile(r"交易状态[：:]\s*<span[^>]*>([^<]+)</span>")


def fetch_fund(code, timeout=12.0):
    """抓取单只基金的天天基金交易状态页。不重试 (重试逻辑见 fetch_with_retry)。"""
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
        # 页面正常返回但没有交易状态字段 → 天天基金不覆盖 (互认基金等)
        return {"code": code, "name": None, "status": "无数据", "open": None,
                "source": "eastmoney"}

    status = html.unescape(m.group(1)).strip()
    if "暂停申购" in status:
        return {"code": code, "name": None, "status": "暂停申购", "open": False,
                "source": "eastmoney"}
    if "限大额" in status or "限制大额" in status:
        return {"code": code, "name": None, "status": "限大额", "open": True,
                "source": "eastmoney"}
    if "开放申购" in status or "开放" in status:
        return {"code": code, "name": None, "status": "开放申购", "open": True,
                "source": "eastmoney"}
    return {"code": code, "name": None, "status": status or "未知",
            "open": "暂停" not in status, "source": "eastmoney"}


def fetch_with_retry(code, timeout=12.0, attempts=3, paced=True):
    """带节流与退避重试: 请求间随机抖动, 频控(514)/网络错误退避后重试。"""
    last = None
    for attempt in range(attempts):
        if paced:
            time.sleep(random.uniform(0.2, 0.5))
        try:
            info = fetch_fund(code, timeout=timeout)
            err = info.get("error")
            if not err:
                return info
            last = info
        except Exception as exc:
            last = {"code": code, "name": None, "status": "查询失败", "open": False,
                    "error": str(exc), "source": None}
        if attempt < attempts - 1:
            time.sleep(2.0 + 2.0 * attempt)  # 2s, 4s 退避
    return last


def run_plan(plan, hsbc_open_codes=(), timeout=12.0, attempts=3):
    """分组早停校验: 每组按顺序查到第一只开放即停。

    plan: [{"category": key, "codes": [有序代码], "stop_on_open": bool}]
    hsbc_open_codes: 汇丰实时列表中开放申购的代码集合 — 天天基金"无数据"的基金
                     (互认基金) 回退汇丰口径并标注 source=hsbc-fallback。
    返回 (funds: {code: info}, selection: {category: {...}}, stats)
    """
    hsbc_open = set(hsbc_open_codes)
    funds = {}
    selection = {}
    checked = open_found = fallback = 0

    for group in plan:
        category = group.get("category") or "_"
        codes = group.get("codes") or []
        stop_on_open = group.get("stop_on_open", True)
        tried = []
        chosen = None
        for code in codes:
            code = str(code).strip()
            if not code:
                continue
            if code in funds:  # 跨组复用 (如锚定代码重复出现)
                info = funds[code]
            else:
                info = fetch_with_retry(code, timeout=timeout, attempts=attempts)
                funds[code] = info
                checked += 1
            tried.append(code)
            if info.get("open") is None and code in hsbc_open:
                # 天天基金无数据 → 汇丰实时口径兜底 (开放), 显式标注来源
                info = dict(info, status="汇丰口径开放（天天基金无数据）",
                            open=True, source="hsbc-fallback")
                funds[code] = info
                fallback += 1
            if info.get("open"):
                chosen = code
                open_found += 1
                if stop_on_open:
                    break
        selection[category] = {
            "tried": tried,
            "chosen": chosen,
            "open_found": chosen is not None,
        }

    return funds, selection, {"checked": checked, "open_found": open_found,
                              "hsbc_fallback": fallback}


def codes_from_file(path):
    """从 JSON 文件读代码列表: 兼容 {"codes":[...]} / 汇丰抓取结果 / 纯数组。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        items = data
    else:
        items = data.get("codes") or data.get("funds") or []
    codes = []
    for it in items:
        code = it if isinstance(it, str) else str(it.get("code", "")).strip()
        if code and code not in codes:
            codes.append(code)
    return codes


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
    parser.add_argument("--codes", help="逗号分隔的基金代码 (全量模式)")
    parser.add_argument("--codes-file", help="JSON 代码清单 (全量模式)")
    parser.add_argument("--plan-file", help="分组早停计划 JSON (优化标准路径)")
    parser.add_argument("--hsbc-codes-file", help="汇丰实时开放基金 JSON (无数据回退口径)")
    parser.add_argument("--json", dest="out_path", help="写入 JSON 文件")
    parser.add_argument("--timeout", type=float, default=12.0, help="单请求超时秒数")
    parser.add_argument("--attempts", type=int, default=3, help="频控/网络错误重试次数")
    args = parser.parse_args()

    hsbc_open_codes = []
    if args.hsbc_codes_file:
        with open(args.hsbc_codes_file, encoding="utf-8") as f:
            data = json.load(f)
        hsbc_open_codes = [str(f.get("code", "")).strip()
                           for f in data.get("funds", []) if f.get("open", True)]

    started = time.time()
    plan = selection = None
    if args.plan_file:
        with open(args.plan_file, encoding="utf-8") as f:
            plan = json.load(f)
        funds, selection, stats = run_plan(plan, hsbc_open_codes=hsbc_open_codes,
                                           timeout=args.timeout, attempts=args.attempts)
    elif args.codes or args.codes_file:
        codes = ([c.strip() for c in args.codes.split(",") if c.strip()]
                 if args.codes else codes_from_file(args.codes_file))
        if not codes:
            print("No fund codes provided.", file=sys.stderr)
            sys.exit(2)
        plan = [{"category": "_all", "codes": codes, "stop_on_open": False}]
        funds, selection, stats = run_plan(plan, hsbc_open_codes=hsbc_open_codes,
                                           timeout=args.timeout, attempts=args.attempts)
    else:
        codes = default_codes()
        if not codes:
            print("No fund codes provided.", file=sys.stderr)
            sys.exit(2)
        plan = [{"category": "_default", "codes": codes, "stop_on_open": False}]
        funds, selection, stats = run_plan(plan, hsbc_open_codes=hsbc_open_codes,
                                           timeout=args.timeout, attempts=args.attempts)

    open_count = sum(1 for f in funds.values() if f.get("open"))
    result = {
        "source": "天天基金",
        "base_url": "https://fundf10.eastmoney.com/jbgk_{code}.html",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - started, 1),
        "total_count": len(funds),
        "open_count": open_count,
        "suspended_count": sum(1 for f in funds.values() if f.get("open") is False),
        "no_data_count": sum(1 for f in funds.values() if f.get("open") is None),
        "stats": stats,
        "plan": plan,
        "selection": selection,
        "funds": funds,
    }

    if args.out_path:
        with open(args.out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
