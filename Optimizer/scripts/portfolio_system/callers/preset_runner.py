"""预设调用器：持仓 + 预设资产 → 一个问题 → 一次求解（ADR 0003 §4.3/§5）。"""

from __future__ import annotations

from typing import Optional

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
