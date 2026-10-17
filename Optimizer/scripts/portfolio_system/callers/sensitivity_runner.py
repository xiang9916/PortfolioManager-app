"""敏感性分析调用器：多目标点，逐点一次完整流水线（可热启动/可并行）。

只依赖 core/models/services，不 import 其它调用器（ADR R3）；装配复用 base_runner 的函数。
"""

from __future__ import annotations

from typing import List, Tuple

from .base_runner import Runner, RunOutcome, RunRequest, assemble_problem


class SensitivityRunner(Runner):
    """目标点扫描：每个点都走 optimize()（与结果页同源，见 ADR §5）。"""

    def __init__(self, optimizer):
        self.optimizer = optimizer

    def run_targets(self, request: RunRequest, targets: Tuple[float, ...] | None = None):
        pts = tuple(targets if targets is not None else request.targets)
        return [(float(t), self.optimizer.optimize(assemble_problem(request, target_return=t)))
                for t in pts]

    def run(self, request: RunRequest) -> RunOutcome:
        """单点运行时与 PresetRunner 同路径；多点用 run_targets（保留首个点作为 outcome）。"""
        pts = self.run_targets(request)
        if not pts:
            return self._outcome(self.optimizer.optimize(assemble_problem(request)))
        return self._outcome(pts[0][1])
