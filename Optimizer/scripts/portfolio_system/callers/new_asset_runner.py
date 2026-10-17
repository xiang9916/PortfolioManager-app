"""新标的调用器：基础资产 + 人工指定新标的（先解析序列再求解）。"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence

from ..models.data_types import AssetSpec
from .base_runner import Runner, RunOutcome, RunRequest, assemble_problem


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

    def run(self, request: RunRequest, new_assets: Sequence[AssetSpec] = ()) -> RunOutcome:
        extra = tuple(request.assets) + tuple(new_assets)
        patched = replace(request, assets=extra)
        return self._outcome(self.optimizer.optimize(assemble_problem(patched)))
