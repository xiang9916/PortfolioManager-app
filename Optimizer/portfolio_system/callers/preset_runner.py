"""预设调用器：持仓 + 预设资产 → 一个问题 → 一次求解（ADR 0003 §4.3/§5）。

P3-b3 步骤②：本调用器还接管**结果装配** —— build_summary（App 读的 summary 契约）与
build_benchmark 从 optimize_portfolio.py 等价搬入。外部数据表（BROAD_ASSETS / 各内部池 /
BROAD_CORR / build_cov）与 rf 一律**显式入参**（SummaryTables）；callers 不 import params。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from ..core.constants import CHECK_SUM_EPS, TINY_WEIGHT_EPS, WEIGHT_ZERO_EPS
from ..core.warnings import CodedWarning, warning_items

from ..models.data_types import SolveResult
from .base_runner import Runner, RunOutcome, RunRequest, assemble_problem


class PresetRunner(Runner):
    """最常用路径：一次完整流水线，一个问题，一次 optimize()。"""

    def __init__(self, optimizer, source=None, pipeline=None, fetcher=None):
        self.optimizer = optimizer
        self.source = source
        self.pipeline = pipeline
        self.fetcher = fetcher

    def run(self, request: RunRequest) -> RunOutcome:
        problem = assemble_problem(request)
        result: SolveResult = self.optimizer.optimize(problem)
        return self._outcome(result)


@dataclass(frozen=True)
class SummaryTables:
    """结果 summary / benchmark 需要的**外部数据表**（由装配层注入；callers 不 import params）。"""

    broad_assets: Sequence[Mapping[str, Any]]
    broad_corr: Mapping[Any, float]
    us_internal: Mapping[str, Any]
    jp_internal: Mapping[str, Any]
    gc_internal: Mapping[str, Any]
    build_cov: Callable[..., Any]

def build_benchmark(output, final, *, tables, rf=0.0):
    """Benchmark = 境内池 100% 沪深300ETF + 海外池 100% 标普500ETF."""
    try:
        pool = output.get("pool", {})
        dw = float(pool.get("domestic_weight", 0.0))
        ow = float(pool.get("overseas_weight", 0.0))
        core = output.get("us_core_aggregate", {})
        broad = []
        for a in tables.broad_assets:
            item = dict(a)
            if a["key"] == "O_US_CORE":
                item["mu"] = core.get("mu", a.get("mu"))
                item["vol"] = core.get("vol", a.get("vol"))
            broad.append(item)
        # 测试标的也要进这张协方差表: 组合 Beta 用的是"组合权重 × 基准权重"的协方差,
        # 漏掉测试标的等于把它们当空仓, Beta 被低估、Alpha 被高估 (2026-10-13 修)。
        test_block = output.get("test") or {}
        test_meta = {t["key"]: t for t in test_block.get("assets", [])}
        for key, m in test_meta.items():
            broad.append({"key": key, "name": key, "mu": m["mu"], "vol": m["vol"],
                          "pool": m.get("pool")})
        keys = [a["key"] for a in broad]
        mu = np.array([a["mu"] if a["mu"] is not None else 0.0 for a in broad])
        vols = np.array([a["vol"] if a["vol"] is not None else 0.0 for a in broad])
        corr_pairs = dict(tables.broad_corr)
        for i, j, v in test_block.get("corr", []) or []:
            corr_pairs[(i, j)] = float(v)
        cov = tables.build_cov([{"key": k, "vol": v} for k, v in zip(keys, vols)], corr_pairs)

        w_bench = np.zeros(len(keys))
        if "D_GREATER_CN" in keys:
            w_bench[keys.index("D_GREATER_CN")] = dw
        elif "D_HSBC_CSI300" in keys:
            w_bench[keys.index("D_HSBC_CSI300")] = dw
        if "D_HSBC_SP500" in keys:
            w_bench[keys.index("D_HSBC_SP500")] = ow

        bench_ret = float(w_bench @ mu)
        bench_vol = float(np.sqrt(w_bench @ cov @ w_bench))

        # Portfolio broad weights (stage1) for beta approximation.
        stage1 = output.get("stage1", {}).get("weights", {})
        w_p = np.zeros(len(keys))
        for i, k in enumerate(keys):
            w_p[i] = float(stage1.get(k, 0.0))
        cov_pb = float(w_p @ cov @ w_bench)
        var_b = float(w_bench @ cov @ w_bench)
        beta = cov_pb / var_b if var_b > 0 else 0.0

        ret_p = float(final.get("ret", 0.0))
        alpha = ret_p - (rf + beta * (bench_ret - rf))

        return {
            "weights": {"domestic_CSI300": dw, "overseas_SP500": ow},
            "expected_return": round(bench_ret, 6),
            "volatility": round(bench_vol, 6),
            "sharpe": round((bench_ret - rf) / bench_vol, 6) if bench_vol > 0 else 0.0,
            "worst_year_95": round(bench_ret - 1.96 * bench_vol, 6),
            "portfolio_alpha": round(alpha, 6),
            "portfolio_beta": round(beta, 6),
            "volatility_reduction": round(bench_vol - float(final.get("vol", 0.0)), 6),
            "worst_year_improvement": round(float(final.get("floor", 0.0)) - (bench_ret - 1.96 * bench_vol), 6),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def build_summary(output, *, tables, rf=0.0, hsbc_pool=None):
    """Build a clean Finance result file: per-asset weights/return/vol/sharpe + totals."""
    # 境外/跨池大类的执行代码（标的名前加 [代码] 前缀，便于直接下单；
    # 无单一成交代码的大类保持 None，不加前缀）
    exec_code = {
        "O_BTC": "BTC",
        "O_HYLB": "HYLB",
        "O_SG_EQ": "G3B.SI",
        "O_US_TLT": "TLH",
        "O_US_REIT": "REZ",
        "O_US_ENERGY": "XOM",
    }
    # 大中华二级风格桶 → 境内基金池类别（风格桶本身无单一成交代码，
    # 执行代表基金取自对应类别的去重/最低费率代表，与境内大类同一套哲学）
    gc_bucket_pool = {
        "CSI300": "D_HSBC_CSI300",
        "DIVLOWVOL": "D_HSBC_DIVLOWVOL",
        "DIVLOWVOL100": "D_CN_DIVLOWVOL100",
        "STOCK": "D_CN_STOCK",
        "MIXED": "D_CN_MIXED",
        "INDEX": "D_CN_INDEX",
        "HK_BROAD": "D_CN_HK",
        "HK_DIV": "D_CN_HK",
        "HK_TECH": "D_HSBC_HSTECH",
    }
    assets = []
    # Domestic categories with selected representative funds
    for key, sel in output.get("domestic_selection", {}).items():
        a = next((x for x in tables.broad_assets if x["key"] == key), None)
        if a is None:
            continue
        mu = a["mu"]
        vol = a["vol"]
        assets.append({
            "key": key,
            "name": f"[{sel['selected']['code']}] {a['name']} → {sel['selected']['name']}",
            "weight": sel["category_weight"],
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - rf) / vol if vol else 0.0,
        })
    # US core internal split
    stage2 = output.get("stage2") or {}
    for ticker, w in stage2.get("internal_weights_total", {}).items():
        if w <= TINY_WEIGHT_EPS:
            continue
        info = tables.us_internal.get(ticker)
        if info is None:
            continue
        mu = info["mu"]
        vol = info["vol"]
        assets.append({
            "key": ticker,
            "name": f"[{ticker}] {info['name']}",
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - rf) / vol if vol else 0.0,
        })
    # Japan equity internal factor split
    jp = output.get("japan_selection")
    if jp is not None:
        for ticker, w in jp.get("internal_weights_total", {}).items():
            if w <= TINY_WEIGHT_EPS:
                continue
            info = tables.jp_internal.get(ticker)
            if info is None:
                continue
            mu = info["mu"]
            vol = info["vol"]
            assets.append({
                "key": f"JP:{ticker}",
                "name": f"[{ticker}] 日本·{info['name']}",
                "weight": w,
                "expected_return": mu,
                "volatility": vol,
                "sharpe": (mu - rf) / vol if vol else 0.0,
            })

    # Greater China equity internal factor split
    # 风格桶无单一成交代码：前缀用对应境内基金池的代表基金代码
    gc = output.get("gc_selection")
    if gc is not None:
        gc_used = {}  # pool_key -> 已用 code 列表，同池多桶避免重复同一代表
        for ticker, w in gc.get("internal_weights_total", {}).items():
            if w <= TINY_WEIGHT_EPS:
                continue
            info = tables.gc_internal.get(ticker)
            if info is None:
                continue
            mu = info["mu"]
            vol = info["vol"]
            rep = None
            if hsbc_pool:
                pool_key = gc_bucket_pool.get(ticker)
                cands = hsbc_pool.get(pool_key) or []
                used = gc_used.setdefault(pool_key, [])
                rep = next((c for c in cands if c["code"] not in used), None)
                if rep is not None:
                    used.append(rep["code"])
            name = (f"[{rep['code']}] 大中华·{info['name']} → {rep['name']}"
                    if rep is not None else f"大中华·{info['name']}")
            assets.append({
                "key": f"GC:{ticker}",
                "name": name,
                "weight": w,
                "expected_return": mu,
                "volatility": vol,
                "sharpe": (mu - rf) / vol if vol else 0.0,
            })

    # Other overseas broad assets
    for key, w in output.get("stage1", {}).get("weights", {}).items():
        if key in [a["key"] for a in assets] or key in ("O_US_CORE", "O_JP_EQ", "O_HK_HSTECH", "D_GREATER_CN"):
            continue
        a = next((x for x in tables.broad_assets if x["key"] == key), None)
        if a is None:
            continue
        mu = a["mu"]
        vol = a["vol"]
        code = a.get("fund_code") or exec_code.get(key)
        name = f"[{code}] {a['name']}" if code else a["name"]
        assets.append({
            "key": key,
            "name": name,
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - rf) / vol if vol else 0.0,
        })
    # 测试标的 (T_ 前缀): 它们是"新标的测试"临时注入的大类, 不在 tables.broad_assets 里,
    # 但同样占用第一阶段权重。以前这里直接 continue 掉, 后果是:
    # (a) 权重合计 < 1(看起来"没买满"), (b) 用户看到的清单里永远没有新标的,
    # 于是"加入新标的值不值得"这个问题的答案被整段吞掉 (2026-10-13 修)。
    test_meta = {t["key"]: t for t in (output.get("test") or {}).get("assets", [])}
    listed = {a["key"] for a in assets}
    for key, w in output.get("stage1", {}).get("weights", {}).items():
        if key in listed:
            continue
        m = test_meta.get(key)
        if m is None:
            continue
        mu = float(m["mu"])
        vol = float(m["vol"])
        src = "天天基金" if m.get("source") == "eastmoney" else "Yahoo"
        pool = "境内池" if m.get("pool") == "domestic" else "境外池"
        assets.append({
            "key": key,
            "name": f"[测试] {m['ticker']}（{src} · {pool} · what-if，不进执行清单）",
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - rf) / vol if vol else 0.0,
            "is_test": True,
        })
    assets.sort(key=lambda x: -x["weight"])
    for a in assets:
        a["weight"] = round(float(a["weight"]), 6)
        a["expected_return"] = round(float(a["expected_return"]), 6)
        a["volatility"] = round(float(a["volatility"]), 6)
        a["sharpe"] = round(float(a["sharpe"]), 6)
    final = output.get("final") or {}
    # 自检: 报告清单必须覆盖第一阶段所有权重。历史上测试标的(T_)被静默丢弃过一次,
    # 于是"清单合计 82%、指标却按 100% 算"这种错能一路滑到界面上 —— 这里把它变成显式提示。
    # 注意有些 key 是**故意**不出现在清单里的: 它们被拆成了第二阶段的子行
    # (O_US_CORE→美股内部标的、O_JP_EQ/O_HK_HSTECH/D_GREATER_CN→各内部桶、境内大类→选中基金)。
    expanded = set(output.get("domestic_selection", {}).keys())
    for sel_key, broad_key in (("stage2", "O_US_CORE"), ("japan_selection", "O_JP_EQ"),
                               ("gc_selection", "D_GREATER_CN")):
        if output.get(sel_key) is not None:
            expanded.add(broad_key)
    emitted = {a["key"] for a in assets}
    missing = [k for k, w in output.get("stage1", {}).get("weights", {}).items()
               if w > WEIGHT_ZERO_EPS and k not in emitted and k not in expanded]
    total_w = sum(float(a["weight"]) for a in assets)
    # 优化器自身的提示(候选被剔除、池口径、数据源计数…)必须原样带上:
    # 它们解释了"为什么这次的可投大类和上次不一样", 是解读结果的前提。
    summary_warnings = list(output.get("warnings", []))
    if missing:
        summary_warnings.append(CodedWarning(
            "自检: 以下权重没有出现在结果清单里（映射漏项）: " + ", ".join(sorted(missing)),
            "summary.missing_weights"))
    if abs(total_w - 1.0) > CHECK_SUM_EPS:
        summary_warnings.append(CodedWarning(
            f"自检: 结果清单权重合计为 {total_w:.4f}，不等于 1 —— 请对照第一阶段权重核对映射是否漏项。",
            "summary.weight_sum_mismatch"))
    summary = {
        "generated_at": output.get("availability", {}).get("checked_at"),
        "portfolio": {
            "expected_return": round(float(final.get("ret", 0.0)), 6),
            "volatility": round(float(final.get("vol", 0.0)), 6),
            "sharpe": round(float(final.get("sharpe", 0.0)), 6),
            "worst_year_95": round(float(final.get("floor", 0.0)), 6),
        },
        "assets": assets,
        "benchmark": build_benchmark(output, final, tables=tables, rf=rf),
        "test_assets": (output.get("test") or {}).get("assets"),
        "target_return": output.get("target_return"),
        "target_gap": output.get("target_gap"),
        "objective": output.get("objective"),
        "deliverable": bool(output.get("deliverable", True)),
        "final_source": output.get("final_source", "delivered"),
        "warnings": summary_warnings,
        # 加法式告警明细（code + message）：Swift 只认 code，不再匹配文案。
        # 必须用**最终** summary_warnings 生成 —— 它包含本函数内追加的自检类告警，
        # 以及调用前已并入 output["warnings"] 的 CAP_HITS 类告警。
        "warning_items": warning_items(summary_warnings),
        "source_detail": None,
    }
    return summary
