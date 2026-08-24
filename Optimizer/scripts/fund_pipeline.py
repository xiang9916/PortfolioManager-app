#!/usr/bin/env python3
"""优化运行的标准联网管线: 汇丰搜索易实时列表 → 天天基金实时申赎状态 → 临时文件。

每次优化 (optimize_portfolio.py) 都完整执行:
  1. sync_hsbc_funds.py  实时抓取基金搜索易全部在售基金 → <work>/hsbc_funds.json
  2. build_check_plan()  每类候选排序: 锚定优先 → 费率升序 → A/C 备选垫后
  3. check_fund_availability.py --plan-file  分组早停实时校验
                         (每类查到第一只"开放申购"即停, 避开天天基金频控)
                         → <work>/availability.json (含 selection: 每类选中的开放基金)
  4. params.build_open_pool_from_status()  用 selection 构建开放基金池

敏感性分析 (sensitivity_analysis.py) 复用同一 work dir 下的临时文件, 不重新联网。

work dir 生命周期: Swift 侧 OptimizationService 在关闭优化器时整体删除;
Python 独立运行时缺省用系统临时目录下的共享目录 PortfolioManager-optimizer-work。
"""

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from params import (
    BROAD_ASSETS,
    DOMESTIC_FUND_ORDER,
    _dedup_with_fallback,
    _inefficient,
    _load_fees,
    classify_domestic_fund,
)

WORK_DIRNAME = "PortfolioManager-optimizer-work"
HSBC_FILENAME = "hsbc_funds.json"
AVAIL_FILENAME = "availability.json"
PLAN_FILENAME = "check_plan.json"


def default_work_dir():
    return Path(tempfile.gettempdir()) / WORK_DIRNAME


def ensure_work_dir(path=None):
    work = Path(path) if path else default_work_dir()
    work.mkdir(parents=True, exist_ok=True)
    return work


def hsbc_json_path(work_dir):
    return Path(work_dir) / HSBC_FILENAME


def availability_json_path(work_dir):
    return Path(work_dir) / AVAIL_FILENAME


def _noop_log(step, message, level="info"):
    pass


def _category_order(key):
    return DOMESTIC_FUND_ORDER.index(key) if key in DOMESTIC_FUND_ORDER else len(DOMESTIC_FUND_ORDER)


def build_check_plan(hsbc_funds, live_anchors=None, extra_codes=()):
    """构建分组早停校验计划: 每类候选排序后逐只查到第一只开放即停。

    组内排序:
      1. 锚定基金 (用户持仓 live 优先, 其次 params 静态 fund_code)
      2. 其余按 (非低效优先, 年费率升序, 规模降序) 排序的基金组
      3. 每组内 [主份额, A/C 备选份额] — 主份额被暂停时备选垫后兜底
    另附一组 stop_on_open=False 的锚定代码 (params 静态锚定未进任何类候选时
    也要校验, 供 filter_available_assets 剔除暂停的大类锚定)。
    """
    fees = _load_fees()
    anchors = {a["key"]: a.get("fund_code") for a in BROAD_ASSETS if a["pool"] == "domestic"}
    if live_anchors:
        anchors = {**anchors, **live_anchors}

    by_cat = {}
    code_meta = {}
    for item in hsbc_funds:
        if not item.get("open", True):
            continue
        code = str(item.get("code", "")).strip()
        name = str(item.get("name", "")).strip()
        if not code or not name:
            continue
        by_cat.setdefault(classify_domestic_fund(name), []).append({"code": code, "name": name})
        code_meta[code] = name

    plan = []
    for key in sorted(by_cat, key=_category_order):
        cands = by_cat[key]
        anchor = anchors.get(key)
        groups = {}  # base -> [primary, alternate?]
        for c in _dedup_with_fallback(cands, anchor):
            base = c["code"]
            groups.setdefault(base, {"primary": None, "alt": None, "name": c["name"]})
            entry = groups[base]
            if entry["primary"] is None:
                entry["primary"] = c
            else:
                entry["alt"] = c

        def _fee_rank(entry):
            info = fees.get(entry["primary"]["code"]) or {}
            return (
                1 if _inefficient(entry["primary"], fees) else 0,
                info.get("annual_cost") if info.get("annual_cost") is not None else float("inf"),
                -(info.get("aum_yi") or 0.0),
            )

        entries = list(groups.values())
        ordered = []
        if anchor:
            anchor_entry = next((e for e in entries if e["primary"]["code"] == anchor), None)
            if anchor_entry is not None:
                ordered.append(anchor_entry)
        for entry in sorted((e for e in entries if e not in ordered), key=_fee_rank):
            ordered.append(entry)

        codes = []
        for entry in ordered:
            codes.append(entry["primary"]["code"])
            if entry["alt"] is not None and entry["alt"]["code"] != entry["primary"]["code"]:
                codes.append(entry["alt"]["code"])
        if codes:
            plan.append({"category": key, "codes": codes, "stop_on_open": True,
                         "names": {c: code_meta.get(c, "") for c in codes}})

    # 锚定代码兜底组: 未出现在任何类候选里的 params 静态锚定也全量校验
    leftover = [c for c in extra_codes if c and c not in code_meta]
    if leftover:
        plan.append({"category": "_anchors", "codes": leftover, "stop_on_open": False})
    return plan


def anchor_codes(live_anchors=None):
    """params.py 静态锚定 + 用户实际持仓锚定的基金代码 (全部纳入实时校验)。"""
    codes = [
        a["fund_code"]
        for a in BROAD_ASSETS
        if a.get("fund_code") and a["pool"] == "domestic"
    ]
    for code in (live_anchors or {}).values():
        if code and code not in codes:
            codes.append(code)
    return codes


def run_hsbc_sync(work_dir, request_timeout=60.0, total_timeout=90.0, log=None):
    """实时抓取基金搜索易 → hsbc_funds.json, 返回解析后的 dict。失败大声退出。"""
    log = log or _noop_log
    script = Path(__file__).resolve().parent / "sync_hsbc_funds.py"
    out = hsbc_json_path(work_dir)
    log("fetch_hsbc", f"实时抓取基金搜索易在售基金列表 (timeout={request_timeout:.0f}s)")
    cmd = [sys.executable, str(script), "--json", str(out),
           "--timeout", str(request_timeout)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True,
                       timeout=total_timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"基金搜索易抓取超时 (>{total_timeout:.0f}s)")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "")[-2000:]
        raise RuntimeError(f"基金搜索易抓取失败: {detail}")
    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    funds = data.get("funds", [])
    log("fetch_hsbc_done",
        f"基金搜索易: 在售 {data.get('total_count', len(funds))} 只, "
        f"开放申购 {data.get('open_count', '?')} 只",
        level="info")
    return data


def run_status_check(work_dir, plan, hsbc_data, request_timeout=12.0,
                     attempts=3, total_timeout=300.0, log=None):
    """按计划分组早停校验天天基金申赎状态 → availability.json, 返回 dict。"""
    log = log or _noop_log
    script = Path(__file__).resolve().parent / "check_fund_availability.py"
    plan_path = Path(work_dir) / PLAN_FILENAME
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    out = availability_json_path(work_dir)
    n_codes = sum(len(g.get("codes", [])) for g in plan)
    log("check_availability",
        f"天天基金实时校验: {len(plan)} 组 / 最多 {n_codes} 只候选 (分组早停, 每类查到开放即停)")
    cmd = [sys.executable, str(script),
           "--plan-file", str(plan_path),
           "--hsbc-codes-file", str(hsbc_json_path(work_dir)),
           "--json", str(out),
           "--timeout", str(request_timeout),
           "--attempts", str(attempts)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True,
                       timeout=total_timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"天天基金状态校验超时 (>{total_timeout:.0f}s)")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "")[-2000:]
        raise RuntimeError(f"天天基金状态校验失败: {detail}")
    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    stats = data.get("stats", {})
    log("check_availability_done",
        f"天天基金校验完成: 实际请求 {stats.get('checked', '?')} 只, "
        f"开放 {data.get('open_count', '?')} 只, "
        f"汇丰口径兜底 {stats.get('hsbc_fallback', 0)} 只, "
        f"耗时 {data.get('elapsed_seconds', '?')}s")
    return data


def ensure_pipeline(work_dir, live_anchors=None, hsbc_request_timeout=60.0,
                    hsbc_total_timeout=90.0, request_timeout=12.0, attempts=3,
                    check_total_timeout=300.0, log=None):
    """完整执行联网管线 (每次优化必跑), 返回 (hsbc_data, availability_data)。"""
    work = ensure_work_dir(work_dir)
    hsbc_data = run_hsbc_sync(work, request_timeout=hsbc_request_timeout,
                              total_timeout=hsbc_total_timeout, log=log)
    plan = build_check_plan(hsbc_data.get("funds", []),
                            live_anchors=live_anchors,
                            extra_codes=anchor_codes(live_anchors))
    avail_data = run_status_check(work, plan, hsbc_data,
                                  request_timeout=request_timeout,
                                  attempts=attempts,
                                  total_timeout=check_total_timeout, log=log)
    return hsbc_data, avail_data


def load_pipeline(work_dir, max_age_hours=12.0):
    """读取 work dir 里已有的管线产物; 缺文件或过期返回 None (调用方自行全量重跑)。"""
    hsbc_path = hsbc_json_path(work_dir)
    avail_path = availability_json_path(work_dir)
    if not (hsbc_path.exists() and avail_path.exists()):
        return None
    try:
        with open(hsbc_path, encoding="utf-8") as f:
            hsbc_data = json.load(f)
        with open(avail_path, encoding="utf-8") as f:
            avail_data = json.load(f)
    except (OSError, ValueError):
        return None
    now = datetime.now(timezone.utc)
    for data in (hsbc_data, avail_data):
        stamp = data.get("fetched_at") or data.get("checked_at")
        if not stamp:
            return None
        try:
            age = (now - datetime.fromisoformat(stamp)).total_seconds()
        except ValueError:
            return None
        if age > max_age_hours * 3600:
            return None
    if not isinstance(avail_data.get("funds"), dict) or not avail_data["funds"]:
        return None
    if not isinstance(avail_data.get("selection"), dict):
        return None
    return hsbc_data, avail_data


def filter_available_assets(assets, avail_funds, warnings, hsbc_status=None):
    """按天天基金口径过滤可投大类; 与汇丰搜索易口径冲突时显式提示, 强制采信天天基金。"""
    kept = []
    for a in assets:
        code = a.get("fund_code")
        if code is None:
            kept.append(a)
            continue
        info = avail_funds.get(code)
        hsbc = (hsbc_status or {}).get(code)
        if info is None:
            msg = f"基金 {a['name']}（{code}）未在天天基金校验结果中，已剔除。"
            if hsbc is not None and hsbc.get("open", True):
                msg += f"（汇丰搜索易显示{hsbc.get('status', '开放')}；两源冲突，以天天基金口径为准）"
            warnings.append(msg)
            continue
        if not info.get("open", False):
            msg = f"基金 {a['name']}（{code}）天天基金状态为 {info.get('status', '未知')}，已剔除"
            if hsbc is not None and hsbc.get("open", True):
                msg += "（汇丰搜索易显示开放申购；两源冲突，以天天基金口径为准）"
            warnings.append(msg + "。")
            continue
        kept.append(a)
    return kept
