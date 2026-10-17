"""新标的调用器：基础资产 + 人工指定新标的（先解析序列再求解）。"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence

from ..models.data_types import AssetSpec
from .base_runner import Runner, RunOutcome, RunRequest, assemble_problem


class TestAssetsUnavailable(RuntimeError):
    """所有测试标的均不可用（CLI 层据此打印错误 JSON 并以退出码 3 结束）。"""

    def __init__(self, detail):
        super().__init__(str(detail))
        self.detail = str(detail)


class NewAssetRunner(Runner):
    """what-if：把新标的作为额外 AssetSpec 追加后走与 PresetRunner 完全相同的求解路径。"""

    def __init__(self, optimizer, resolver=None):
        self.optimizer = optimizer
        self.resolver = resolver       # 可选：把 ticker 解析成 (mu, sigma)（联网在 services 层）

    def resolve(self, ticker: str, asset_class: str = "active") -> AssetSpec:
        mu, sigma, market = 0.0, 0.0, ""
        if self.resolver is not None:
            mu, sigma, market = self.resolver(ticker)
        return AssetSpec(key=ticker, mu=float(mu), sigma=float(sigma), market=str(market),
                         asset_class=str(asset_class),
                         extra={"pool": "overseas", "name": ticker, "test_class": asset_class})

    # ── 脚本路径（P3-b3 步骤③）：接管 optimize_portfolio._inject_test_assets ────
    def inject_test_assets(self, assets, *, raw_tickers, test_class, work_dir, resolve,
                           internal_keys, corr_keys, class_rules, skip_warning, warnings,
                           log=None):
        """新标的测试：临时标的作为新增资产注入第一阶段（在可投性过滤之后，不受其约束）。

        解析器 / 口径表 / 告警工厂由装配层注入（联网与 asset_classes 属上层；callers 只依赖
        core/models/services）。返回 (assets, test_corr_pairs, test_skipped, test_estimates)。
        全部标的不可用时抛 TestAssetsUnavailable —— 打印错误 JSON + 退 3 是 CLI 层契约。
        """
        test_estimates, test_skipped = [], []
        if not raw_tickers:
            return assets, None, test_skipped, test_estimates
        test_assets, test_corr_pairs, test_skipped, test_estimates = resolve(
            raw_tickers,
            work_dir,
            existing_keys={a['key'] for a in assets},
            internal_keys=set(internal_keys),
            # 相关性要覆盖全量大类 (不只是本次可投的那几个): 基准对比用
            # D_HSBC_SP500 / D_GREATER_CN 算组合 Beta, 缺了这些行 Beta 会被低估。
            corr_keys=set(corr_keys),
            test_class=test_class,
            log=log,
        )
        for t, reason in test_skipped:
            warnings.append(skip_warning(t, reason))
        # 测试标的没有登记先验：broad/strategy 会退化成纯历史，必须显式说明口径不同
        for e in test_estimates:
            if e.get("mu_note") == "history_only":
                warnings.append(
                    f"测试标的 {e['ticker']} 标为「{class_rules[e['test_class']]['label']}」，"
                    f"但该类的人工先验只对登记在册的标的有效；测试标的使用纯历史年化 "
                    f"μ={e['mu']:.2%}（组合内同类标的的口径是"
                    f"{class_rules[e['test_class']]['mu_hist_weight']:.0%} 历史 + "
                    f"{1-class_rules[e['test_class']]['mu_hist_weight']:.0%} 先验，两者不可直接横比）。")
        if not test_assets:
            detail = "; ".join(f"{t}: {r}" for t, r in test_skipped) or "无有效标的"
            raise TestAssetsUnavailable(detail)
        assets = assets + test_assets
        for a in test_assets:
            if a["pool"] == "domestic":
                warnings.append(
                    f"测试标的 {a['name']} 按境内池约束参与优化 (what-if 口径, "
                    f"不经汇丰在售校验, 也不出现在境内基金执行清单)。")
        if log is not None:
            log("新增 %d 个测试资产: %s" % (len(test_assets), ", ".join(a["key"] for a in test_assets)))
        return assets, test_corr_pairs, test_skipped, test_estimates

    def run(self, request: RunRequest, new_assets: Sequence[AssetSpec] = ()) -> RunOutcome:
        extra = tuple(request.assets) + tuple(new_assets)
        patched = replace(request, assets=extra)
        return self._outcome(self.optimizer.optimize(assemble_problem(patched)))
