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
        # 编排模式（ADR 0003 §5，P4 收尾）：request.deps 由装配层注入 → 本调用器负责
        # 「取数 → 装配 → 求解 → 结果汇总」，main() 只做 argparse / 写文件。
        if getattr(request, "deps", None) is not None:
            return self._run_pipeline(request)
        problem = assemble_problem(request)
        result: SolveResult = self.optimizer.optimize(problem)
        return self._outcome(result)

    # ── 编排模式（P4 收尾）：取数 → 装配 → 求解 → 结果汇总 ──────────────────────
    def _inject_test_assets(self, request, assets, work_dir, warnings,
                            held_keys=(), held_tickers=()):
        """新标的注入（实现仍在 NewAssetRunner；经 deps 注入，避免调用器互相 import）。"""
        deps = request.deps
        log = request.log
        return deps.inject_test_assets(
            assets,
            raw_tickers=request.test_tickers,
            test_class=request.test_class,
            work_dir=work_dir,
            resolve=deps.resolve_test_assets,
            internal_keys=deps.internal_keys,
            corr_keys=deps.corr_keys,
            class_rules=deps.class_rules,
            skip_warning=deps.test_skip_warning,
            warnings=warnings,
            held_keys=held_keys,
            held_tickers=held_tickers,
            log=(lambda message, level="info": log("test_tickers", message, level=level))
            if log is not None else None,
        )

    def _inject_held_assets(self, request, assets, work_dir, hsbc_pool):
        """持仓纳入：把候选池里没有的持仓注入为顶层 what-if 腿（契约 §1.4）。

        实现留在脚本层的 scripts/held_assets.py（它要读 params/market_data 的候选池表），
        callers 不得 import 上层 → 经 deps.inject_held_assets 注入。
        返回 (assets, corr_pairs, estimates, skipped)；不抛异常（单只失败只进 skipped）。
        """
        deps = request.deps
        log = request.log
        return deps.inject_held_assets(
            request.extract, assets,
            work_dir=work_dir,
            hsbc_pool=hsbc_pool,
            existing_keys={a["key"] for a in assets},
            corr_keys=deps.corr_keys,
            resolve=deps.resolve_test_assets,
            log=(lambda message, level="info": log("held_assets", message, level=level))
            if log is not None else None,
        )

    def _run_pipeline(self, request: RunRequest) -> RunOutcome:
        """完整流水线（原 optimize_portfolio.main() 的编排段，逐句等价搬迁）。"""
        deps = request.deps
        extract = dict(request.extract)
        warnings = request.warnings_out if request.warnings_out is not None else []
        deps.cap_hits.clear()
        warnings.extend(deps.calibration_warnings)
        log = request.log or (lambda *args, **kwargs: None)

        live_anchors = deps.live_anchors_from_extract(extract)
        if live_anchors:
            log("live_anchors", f"动态锚定 {len(live_anchors)} 个境内基金 (覆盖 params.py 静态锚定)")

        # ---- 联网管线: 每次优化完整执行 (抓汇丰搜索易 → 天天基金实时校验 → 开放池构建) ----
        work_dir = deps.ensure_work_dir(request.work_dir)
        try:
            hsbc_data, availability = deps.ensure_pipeline(
                work_dir,
                live_anchors=live_anchors,
                hsbc_request_timeout=request.hsbc_timeout,
                request_timeout=request.request_timeout,
                check_total_timeout=request.check_timeout,
                log=log,
            )
        except RuntimeError as exc:
            raise PipelineUnavailable(str(exc), warnings=warnings) from exc
        avail_funds = availability["funds"]
        hsbc_funds_live = hsbc_data.get("funds", [])
        # 汇丰原始在售状态, 供口径冲突提示 (汇丰开放 vs 天天基金暂停)
        hsbc_status = {str(f.get("code", "")).strip(): f for f in hsbc_funds_live}

        # 直接取分组早停校验 selection 中每类选中的开放代表基金
        hsbc_pool, pool_meta = deps.build_open_pool(
            hsbc_funds_live, availability, live_anchors=live_anchors)
        stats = availability.get("stats", {})
        log("pipeline_counts",
            f"在售 {hsbc_data.get('total_count', '?')} 只 / 开放申购 {hsbc_data.get('open_count', '?')} 只；"
            f"实时校验请求 {stats.get('checked', '?')} 只 / 开放 {availability.get('open_count', '?')} 只 / "
            f"兜底 {stats.get('hsbc_fallback', 0)} 只")
        log("build_open_pool",
            f"开放基金池：{len(hsbc_pool)} 个类别、代表基金 {pool_meta['kept']} 只"
            f"（{stats.get('checked', '?')} 只候选实时校验, 耗时 {availability.get('elapsed_seconds', '?')}s）")

        dom_w = float(extract["domestic_weight"])
        ov_w = float(extract["overseas_weight"])

        internal_weights = {h["ticker"]: h["weight"] for h in extract["us_equity"]["holdings"]}
        core_mu, core_vol = deps.us_core_params(internal_weights)

        assets = deps.build_investable_assets(
            hsbc_pool, pool_meta, avail_funds, hsbc_status, warnings,
            core_mu=core_mu, core_vol=core_vol, drop_note="已从第一阶段剔除")
        log("build_assets", f"构建大类资产 {len(assets)} 项")
        if not any(a["pool"] == "domestic" for a in assets):
            warnings.append(CodedWarning("没有可申购的境内基金", "fund.available_none"))
            raise NoDomesticAssets(warnings)

        # ---- 持仓纳入（Q1，契约 §1.4）：在可投大类装配之后、新标的测试之前 ----
        # 候选池里没有的持仓（例如某只实盘持有、但目录里没有的 ETF）过去被静默丢弃；
        # 现在按顶层 what-if 腿注入。
        # 全部依赖经 deps 注入（scripts/held_assets.py 属上层，callers 不得 import）。
        held_block = None
        held_corr_pairs, held_estimates = {}, []
        held_keys, held_tickers = set(), set()
        held_input = list(extract.get("held_assets") or [])
        held_wiring = (deps.inject_held_assets is not None
                       and deps.classify_held_assets is not None
                       and deps.held_skip_warning is not None
                       and deps.us_equity_outside_catalog_warning is not None)
        if held_wiring and held_input and extract.get("include_unmodeled_holdings"):
            modeled, _unmodeled = deps.classify_held_assets(extract, assets, hsbc_pool)
            assets, held_corr_pairs, held_estimates, held_skipped = self._inject_held_assets(
                request, assets, work_dir, hsbc_pool)
            for ticker, reason in held_skipped:
                warnings.append(deps.held_skip_warning(ticker, reason))
            us_outside = [str(e.get("ticker") or "") for e in held_estimates
                          if (e.get("asset_class") or "") == "us_equity"]
            if us_outside:
                warnings.append(deps.us_equity_outside_catalog_warning(us_outside))
            if held_estimates or held_skipped:
                held_block = {
                    "included": held_estimates,
                    "skipped": [{"ticker": t, "reason": r} for t, r in held_skipped],
                    # 恒等式：held = modeled + injected + skipped（未建模的每只都有归宿）
                    "counts": {"held": len(held_input), "modeled": len(modeled),
                               "injected": len(held_estimates),
                               "skipped": len(held_skipped)},
                    # 加法式：与 test 块同格式的相关性行，供 build_benchmark 把持仓腿
                    # 并入基准协方差（否则 Beta/Alpha 会把这些腿当空仓而失真）。
                    "corr": [[i, j, float(v)] for (i, j), v in (held_corr_pairs or {}).items()],
                }
                held_keys = {str(e.get("key") or "") for e in held_estimates}
                held_tickers = {str(e.get("ticker") or "").upper() for e in held_estimates}
            log("held_assets",
                f"持仓纳入：候选池内 {len(modeled)} 只 / 注入 {len(held_estimates)} 只 / "
                f"跳过 {len(held_skipped)} 只")

        # ---- 新标的测试: 临时标的作为新增资产注入第一阶段 (在可投性过滤之后, 不受其约束) ----
        assets, test_corr_pairs, test_skipped, test_estimates = self._inject_test_assets(
            request, assets, work_dir, warnings,
            held_keys=held_keys, held_tickers=held_tickers)

        # 缺 μ/σ 的标的（校准文件里没有它、也没有写死值可用）不能进优化：显式剔除并报警，
        # 不静默用 0 顶上（0 波动会直接霸占权重）。σ 已无人工下限，所以这类标的只能缺席。
        unusable = [a["key"] for a in assets if a.get("mu") is None or a.get("vol") is None]
        if unusable:
            warnings.append(
                "以下标的取不到 μ/σ（校准文件里没有、也没有写死值），本次不参与优化: "
                + "、".join(unusable))
            assets = [a for a in assets if a.get("mu") is not None and a.get("vol") is not None]

        # 相关性表：宽基基础表 + 持仓纳入（**始终**合并）+ 新标的测试（有测试时合并）。
        solve_corr = dict(deps.broad_corr)
        solve_corr.update(held_corr_pairs or {})
        if request.test_tickers and test_corr_pairs:
            solve_corr.update(test_corr_pairs)

        # ---- 完整求解：阶段一（大类）+ 袖内固定点迭代（2026-10-14）----
        total_assets = request.total_assets or float(extract["total_value"])
        solved = deps.solve_portfolio(
            assets,
            solve_corr,
            dom_w=dom_w, ov_w=ov_w, target=request.target_return, rf=request.rf,
            extract=extract, total_assets=total_assets, warnings=warnings, log=log)
        if solved is None:
            raise InfeasibleOptimization(warnings)

        assets = solved["assets"]
        keys, w1 = solved["keys"], solved["stage1_weights"]
        ret1, vol1, sharpe1 = (solved["stage1_ret"], solved["stage1_vol"],
                               solved["stage1_sharpe"])
        target = solved["target"]
        internal = solved["internal"]
        internal_tickers = solved["internal_tickers"]
        stage2_error = solved["stage2_error"]
        japan_selection = solved["japan_selection"]
        gc_selection = solved["gc_selection"]
        final = solved["final"]
        log("stage1_result", f"第一阶段完成 收益={ret1:.2%} 波动={vol1:.2%} 夏普={sharpe1:.3f}")
        log("stage2_internal", "第二阶段：美国权益内部标的优化")

        domestic_selection = select_domestic_funds(w1, assets, hsbc_pool, total_assets)
        log("select_domestic", "境内基金选择完成")

        target_gap = None
        if target is not None and final is not None:
            target_gap = float(final.get("ret", 0.0)) - float(target)
            if target_gap > 0.002:
                warnings.append(
                    f"目标收益 {target:.2%} 是收益**下限**（不是锚）：求解目标是最大化夏普，"
                    f"实际交付 {final.get('ret', 0.0):.2%}（高出 {target_gap:.2%}）。"
                    f"目标低于切点收益时结果不随滑杆变化，把目标提到切点收益以上才会沿前沿上移。")

        test_block = None
        if request.test_tickers:
            test_block = {
                "tickers": deps.parse_test_tickers(request.test_tickers),
                "test_class": request.test_class,
                "class_help": deps.class_param_help,
                "assets": test_estimates,
                "skipped": [{"ticker": t, "reason": r} for t, r in test_skipped],
                # 测试标的与各大类的相关性: 基准对比要用它把测试标的并入协方差,
                # 否则 Beta/Alpha 会因为权重质量不足 1 而失真 (2026-10-13 修)。
                "corr": [[i, j, float(v)] for (i, j), v in test_corr_pairs.items()],
            }

        payload = build_output_payload(
            solved=solved, keys=keys, stage1_weights=w1,
            stage1_stats=(ret1, vol1, sharpe1),
            extract=extract, dom_w=dom_w, ov_w=ov_w, total_assets=total_assets,
            core_mu=core_mu, core_vol=core_vol, internal_weights=internal_weights,
            work_dir=work_dir, hsbc_data=hsbc_data, availability=availability,
            avail_funds=avail_funds, hsbc_pool=hsbc_pool, pool_meta=pool_meta,
            domestic_selection=domestic_selection, internal=internal,
            internal_tickers=internal_tickers, stage2_error=stage2_error,
            japan_selection=japan_selection, gc_selection=gc_selection, final=final,
            target=target, target_gap=target_gap, warnings=warnings,
            test_block=test_block, held_block=held_block, cap_hits=deps.cap_hits,
            cap_hit_warning=deps.cap_hit_warning,
            warning_items_fn=deps.warning_items_fn, log=log)
        return RunOutcome(result=None, payload=payload, context={"hsbc_pool": hsbc_pool})


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
        # 持仓纳入的 what-if 腿同理：不进这张表就等于把它们当空仓（Q1，加法式）。
        held_block = output.get("held") or {}
        held_meta = {t["key"]: t for t in held_block.get("included", [])}
        for key, m in held_meta.items():
            broad.append({"key": key, "name": key, "mu": m["mu"], "vol": m["vol"],
                          "pool": m.get("pool")})
        keys = [a["key"] for a in broad]
        mu = np.array([a["mu"] if a["mu"] is not None else 0.0 for a in broad])
        vols = np.array([a["vol"] if a["vol"] is not None else 0.0 for a in broad])
        corr_pairs = dict(tables.broad_corr)
        for i, j, v in (test_block.get("corr", []) or []) + (held_block.get("corr", []) or []):
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


_EXEC_CODE = {
    "O_BTC": "BTC",
    "O_HYLB": "HYLB",
    "O_SG_EQ": "G3B.SI",
    "O_US_TLT": "TLH",
    "O_US_REIT": "REZ",
    "O_US_ENERGY": "XOM",
}

# 大中华二级风格桶 → 境内基金池类别（风格桶本身无单一成交代码，
# 执行代表基金取自对应类别的去重/最低费率代表，与境内大类同一套哲学）。
_GC_BUCKET_POOL = {
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


def _append_domestic_rows(assets, output, tables, rf):
    """境内大类 → 选中代表基金行（保持原清单顺序）。"""
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


def _append_us_core_rows(assets, output, tables, rf):
    """O_US_CORE 袖内拆分的执行标的行。"""
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


def _append_japan_rows(assets, output, tables, rf):
    """O_JP_EQ 袖内因子拆分行。"""
    jp = output.get("japan_selection")
    if jp is None:
        return
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


def _append_greater_china_rows(assets, output, tables, rf, hsbc_pool):
    """D_GREATER_CN 袖内风格桶行；同池多桶避免重复同一代表基金。"""
    gc = output.get("gc_selection")
    if gc is None:
        return
    gc_used = {}
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
            pool_key = _GC_BUCKET_POOL.get(ticker)
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


def _append_overseas_rows(assets, output, tables, rf):
    """其余境外/跨池大类行（已展开的袖/大类跳过）。"""
    listed = {a["key"] for a in assets}
    for key, w in output.get("stage1", {}).get("weights", {}).items():
        if key in listed or key in ("O_US_CORE", "O_JP_EQ", "O_HK_HSTECH", "D_GREATER_CN"):
            continue
        a = next((x for x in tables.broad_assets if x["key"] == key), None)
        if a is None:
            continue
        mu = a["mu"]
        vol = a["vol"]
        code = a.get("fund_code") or _EXEC_CODE.get(key)
        name = f"[{code}] {a['name']}" if code else a["name"]
        assets.append({
            "key": key,
            "name": name,
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - rf) / vol if vol else 0.0,
        })


def _append_test_rows(assets, output, rf):
    """新标的测试注入的顶层 what-if 腿（T_ 前缀）。"""
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


def _append_held_rows(assets, output, rf):
    """持仓纳入的顶层 what-if 腿（H_ 前缀）。"""
    held_meta = {t["key"]: t for t in ((output.get("held") or {}).get("included") or [])}
    listed = {a["key"] for a in assets}
    for key, w in output.get("stage1", {}).get("weights", {}).items():
        if key in listed:
            continue
        m = held_meta.get(key)
        if m is None:
            continue
        mu = float(m["mu"])
        vol = float(m["vol"])
        src = "天天基金" if m.get("source") == "eastmoney" else "Yahoo"
        pool = "境内池" if m.get("pool") == "domestic" else "境外池"
        name = m.get("name") or f"[持仓] {m['ticker']}"
        assets.append({
            "key": key,
            "name": f"{name}（{src} · {pool} · what-if，不进执行清单）",
            "weight": w,
            "expected_return": mu,
            "volatility": vol,
            "sharpe": (mu - rf) / vol if vol else 0.0,
            "is_held": True,
        })


def build_summary(output, *, tables, rf=0.0, hsbc_pool=None):
    """Build a clean Finance result file: per-asset weights/return/vol/sharpe + totals."""
    assets = []
    _append_domestic_rows(assets, output, tables, rf)
    _append_us_core_rows(assets, output, tables, rf)
    _append_japan_rows(assets, output, tables, rf)
    _append_greater_china_rows(assets, output, tables, rf, hsbc_pool)
    _append_overseas_rows(assets, output, tables, rf)
    _append_test_rows(assets, output, rf)
    _append_held_rows(assets, output, rf)

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
    held_block = output.get("held")
    return {
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
        # 持仓纳入（Q1，加法式；只在确有未建模持仓时出现 → 旧结果缺该键 → nil）
        **({"held_assets": {"included": held_block.get("included", []),
                            "skipped": held_block.get("skipped", [])}}
           if held_block is not None else {}),
        "target_return": output.get("target_return"),
        "target_gap": output.get("target_gap"),
        "objective": output.get("objective"),
        "deliverable": bool(output.get("deliverable", True)),
        "final_source": output.get("final_source", "delivered"),
        # 袖内参数 {袖 key: [μ, σ]}（Q2 敏感性锚定要用）。门控在 build_output_payload：
        # extract 没带 emit_sensitivity_anchor 时 output 里没有这个键 → summary 也不加，
        # 旧提取文件的结果与改造前逐字节一致。
        **({"sleeve_params": output["sleeve_params"]}
           if "sleeve_params" in output else {}),
        "warnings": summary_warnings,
        # 加法式告警明细（code + message）：Swift 只认 code，不再匹配文案。
        # 必须用**最终** summary_warnings 生成 —— 它包含本函数内追加的自检类告警，
        # 以及调用前已并入 output["warnings"] 的 CAP_HITS 类告警。
        "warning_items": warning_items(summary_warnings),
        "source_detail": None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# P4 收尾：main() 瘦身（ADR 0003 §5「结果汇总（键名契约唯一来源）」）
#
# 以下三类从 Optimizer/scripts/optimize_portfolio.py **逐句等价搬迁**：
#   ① select_domestic_funds   —— 选基业务规则（纯函数）
#   ② build_output_payload    —— 结果页 JSON 的唯一装配点
#   ③ PresetDeps + PresetRunner._run_pipeline —— 编排依赖与流水线（见类内）
# callers 不得 import params / asset_classes / 脚本 → 全部口径与数据表由装配层注入。
# ══════════════════════════════════════════════════════════════════════════════


class PipelineUnavailable(RuntimeError):
    """联网管线失败（CLI 层据此打印错误 JSON 并以退出码 2 结束）。"""

    def __init__(self, detail, warnings=()):
        super().__init__(str(detail))
        self.detail = str(detail)
        self.warnings = list(warnings)


class NoDomesticAssets(RuntimeError):
    """没有可申购的境内基金（CLI 层：错误 JSON + 退出码 1）。"""

    def __init__(self, warnings=()):
        super().__init__("没有可申购的境内基金")
        self.warnings = list(warnings)


class InfeasibleOptimization(RuntimeError):
    """求解不可行（CLI 层：错误 JSON + 退出码 1）。"""

    def __init__(self, warnings=()):
        super().__init__("Optimization infeasible")
        self.warnings = list(warnings)


def select_domestic_funds(stage1_weights, assets, hsbc_pool, total_assets):
    """For each allocated domestic broad category, pick the representative open fund.

    This is a simple deterministic selection from the 基金搜索易 open-fund pool:
    prefer the category's original anchor fund if it is in the pool, otherwise use
    the first open fund in that category. All open funds remain available as
    alternatives; a full per-fund internal optimizer can be added later.
    """
    selection = {}
    for i, a in enumerate(assets):
        if a["pool"] != "domestic":
            continue
        key = a["key"]
        weight = float(stage1_weights[i])
        if weight <= WEIGHT_ZERO_EPS:
            continue
        candidates = hsbc_pool.get(key, [])
        if not candidates:
            continue
        anchor = a.get("fund_code")
        chosen = None
        if anchor:
            for c in candidates:
                if c["code"] == anchor:
                    chosen = c
                    break
        if chosen is None:
            chosen = candidates[0]
        selection[key] = {
            "category_weight": weight,
            "amount": weight * total_assets,
            "selected": chosen,
            "candidate_count": len(candidates),
            "candidates": candidates,
        }
    return selection


@dataclass(frozen=True)
class PresetDeps:
    """PresetRunner 编排模式需要的外部依赖（全部由装配层/脚本注入）。

    callers 不得 import params / asset_classes / 脚本；参数口径与数据表一律显式传入
    （与 SummaryTables 同一哲学）。
    """

    calibration_warnings: Sequence[Any]
    us_core_params: Callable[..., Any]
    build_investable_assets: Callable[..., Any]
    build_open_pool: Callable[..., Any]
    ensure_pipeline: Callable[..., Any]
    ensure_work_dir: Callable[..., Any]
    live_anchors_from_extract: Callable[..., Any]
    inject_test_assets: Callable[..., Any]
    resolve_test_assets: Callable[..., Any]
    internal_keys: Sequence[str]
    corr_keys: Sequence[str]
    class_rules: Mapping[str, Any]
    test_skip_warning: Callable[[str, str], Any]
    solve_portfolio: Callable[..., Any]
    broad_corr: Mapping[Any, float]
    parse_test_tickers: Callable[..., Any]
    class_param_help: str
    cap_hits: list
    cap_hit_warning: Callable[..., Any]
    warning_items_fn: Callable[..., Any]
    # 持仓纳入（Q1，契约 §1.4）：实现留在 scripts/held_assets.py，经装配层注入。
    # 缺省 None = 未接线 → 该功能不启用（旧调用方/既有测试的 PresetDeps 构造保持可用，
    # 输出与之前逐字节一致）。
    inject_held_assets: Optional[Callable[..., Any]] = None
    classify_held_assets: Optional[Callable[..., Any]] = None
    held_skip_warning: Optional[Callable[[str, str], Any]] = None
    us_equity_outside_catalog_warning: Optional[Callable[[list], Any]] = None


def build_output_payload(*, solved, keys, stage1_weights, stage1_stats, extract, dom_w, ov_w,
                         total_assets, core_mu, core_vol, internal_weights, work_dir,
                         hsbc_data, availability, avail_funds, hsbc_pool, pool_meta,
                         domestic_selection, internal, internal_tickers, stage2_error,
                         japan_selection, gc_selection, final, target, target_gap,
                         warnings, test_block, cap_hits, cap_hit_warning,
                         warning_items_fn, log=None, held_block=None):
    """结果页 JSON 的**唯一装配点**（原 main() 的 output 字典逐句搬迁，键序/键名不变）。"""
    ret1, vol1, sharpe1 = stage1_stats
    stage1_detail = {
        "weights": {keys[i]: float(stage1_weights[i]) for i in range(len(keys))
                    if stage1_weights[i] > WEIGHT_ZERO_EPS},
        "ret": ret1,
        "vol": vol1,
        "sharpe": sharpe1,
        "floor": ret1 - 1.96 * vol1,
    }
    output = {
        "pool": {
            "mode": extract["pool_mode"],
            "domestic_weight": dom_w,
            "overseas_weight": ov_w,
            "domestic_value": float(extract["domestic_value"]),
            "overseas_value": float(extract["overseas_value"]),
            "total_assets_used": total_assets,
        },
        "us_core_aggregate": {
            "mu": core_mu,
            "vol": core_vol,
            "internal_from_file": internal_weights,
        },
        "pipeline": {
            "work_dir": str(work_dir),
            "hsbc": {
                "fetched_at": hsbc_data.get("fetched_at"),
                "total_count": hsbc_data.get("total_count"),
                "open_count": hsbc_data.get("open_count"),
            },
            "availability": {
                "checked_at": availability.get("checked_at"),
                "checked_codes": len(avail_funds),
                "open_count": availability.get("open_count"),
                "hsbc_fallback": availability.get("stats", {}).get("hsbc_fallback"),
                "elapsed_seconds": availability.get("elapsed_seconds"),
            },
            "open_pool": {
                "categories": len(hsbc_pool),
                "representatives": pool_meta["kept"],
                "category_detail": pool_meta["categories"],
            },
        },
        "availability": {
            "source": availability.get("source"),
            "base_url": availability.get("base_url"),
            "checked_at": availability.get("checked_at"),
            "funds": avail_funds,
        },
        "stage1": stage1_detail,
        "sleeve": {
            "params": solved["sleeve_params"],
            "iterations": solved["sleeve_iterations"],
            "converged": solved["sleeve_converged"],
            "history": solved["sleeve_history"],
        },
        "stage2": None,
        "domestic_selection": domestic_selection,
        "japan_selection": japan_selection,
        "gc_selection": gc_selection,
        "final": final,
        "target_return": target,
        "target_gap": target_gap,
        "objective": "max_sharpe",
        # 加法式：结果统计来源（delivered = 真交付；stage1_model = 不可交付的模型统计）
        "deliverable": bool(solved.get("deliverable", True)),
        "final_source": solved.get("final_source", "delivered"),
        "warnings": warnings,
    }

    # 加法式诊断（2026-10-17）：只在真的发生时出现，既有键名/键序不变。
    #   sleeve_restarts：多初值重启择优审计（chosen + 每候选摘要）
    #   corr_missing   ：模型里按 0 相关处理的缺失相关对（现金类资产确实近似零相关）
    #   cov_psd        ：PSD 修复统计（仅当确有改写或原本非 PSD 时出现）
    for _extra_key in ("sleeve_restarts", "corr_missing", "cov_psd"):
        _extra_val = solved.get(_extra_key)
        if _extra_val is not None:
            output[_extra_key] = _extra_val

    # Q2 敏感性锚定要用主结果的袖内参数。**门控**：只有 extract 显式带
    # emit_sensitivity_anchor=true（App 侧写入）时才出这个加法键 →
    # 旧提取文件 / CLI / canned 回放输出不多任何键（逐字节兼容）。
    if extract.get("emit_sensitivity_anchor"):
        output["sleeve_params"] = (output.get("sleeve") or {}).get("params")

    if cap_hits:
        warnings.append(cap_hit_warning(cap_hits))

    if test_block is not None:
        output["test"] = test_block

    # 持仓纳入（Q1，加法式）：只在真的存在未建模持仓（注入或跳过）时出现 →
    # 没有 held_assets / 全部已建模的旧提取文件输出与之前逐字节一致。
    if held_block is not None:
        output["held"] = held_block

    if internal is not None:
        x_internal, ret2, vol2, sharpe2, _w2, internal_tickers = internal
        internal_total = float(x_internal.sum())
        output["stage2"] = {
            "internal_total_weight": internal_total,
            "internal_weights_total": {t: float(x_internal[i])
                                       for i, t in enumerate(internal_tickers)},
            "internal_weights_normalized": {t: float(x_internal[i]) / internal_total
                                            for i, t in enumerate(internal_tickers)},
            "amounts": {t: float(x_internal[i]) * total_assets
                        for i, t in enumerate(internal_tickers)},
            "ret": ret2,
            "vol": vol2,
            "sharpe": sharpe2,
            "floor": ret2 - 1.96 * vol2,
        }
        if stage2_error:
            warnings.append(stage2_error)

    if log is not None:
        log("finalize", f"优化完成 共{len(solved['assets'])}大类 夏普={sharpe1:.3f}")
    # 加法式告警明细必须在**所有** append 之后重算（CAP_HITS 等是后面才追加的），
    # 否则 warnings 与 warning_items 条数会不一致。
    output["warning_items"] = warning_items_fn(warnings)
    return output
