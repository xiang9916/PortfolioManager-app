"""敏感性分析调用器：多目标点，逐点一次完整流水线（可热启动/可并行）。

只依赖 core/models/services，不 import 其它调用器（ADR R3）；装配复用 base_runner 的函数。

P3-b3（步骤④）：**敏感性扫描的求解路径改经本调用器**直连 core/engine，
sensitivity_analysis.py 不再 import optimize_portfolio（P3 的正式门）。
装配（extract → assets）仍由 sensitivity_analysis 自己那套 load_extract_assets 负责 ——
本类只吃装配好的 assets 与显式 universe。
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence, Tuple

from ..core.warnings import CodedWarning
from .base_runner import Runner, RunOutcome, RunRequest, assemble_problem


def anchor_mismatch_warning(target, point, main):
    """锚点一致性告警工厂（契约 §2.2，2026-10-18 Q2）。

    锚点目标点必须与主结果**逐位一致**（该点直接用主结果的袖参数作初值求解）；
    不一致时由敏感性脚本调用本工厂。消息中文说明；code 必须是字面量
    `"sensitivity.anchor_mismatch"` —— `test_warning_codes.py` 用 AST 扫 package 目录里的
    字面量来核对「登记 ↔ 发出」，所以工厂必须放在 package 内（脚本层不能发 code）。
    """
    def _pct(value):
        return f"{float(value):.2%}" if value is not None else "—"

    def _num(value):
        return f"{float(value):.3f}" if value is not None else "—"

    return CodedWarning(
        f"敏感性锚点目标点与主结果不一致：目标 {_pct(target)} 处 收益 {_pct(point.get('ret'))}"
        f" / 波动 {_pct(point.get('vol'))} / 夏普 {_num(point.get('sharpe'))}；"
        f"主结果 收益 {_pct(main.get('ret'))} / 波动 {_pct(main.get('vol'))}"
        f" / 夏普 {_num(main.get('sharpe'))}。锚点目标点本应与主结果逐位一致"
        f"（同一固定点），请检查锚定初值/参数口径后重跑。",
        "sensitivity.anchor_mismatch")


class SensitivityRunner(Runner):
    """目标点扫描：每个点都走 optimize()（与结果页同源，见 ADR §5）。"""

    def __init__(self, optimizer: Any = None, universe: Any = None):
        self.optimizer = optimizer
        #: 内部池表（core.sleeve_params.SleeveUniverse）；由装配层显式传入，core 不 import params。
        self.universe = universe

    # ── 脚本路径（P3-b3）：逐点直连 core 引擎 ─────────────────────────────────
    def solve_point(self, assets: Sequence[Mapping[str, Any]],
                    corr_pairs: Mapping[Tuple[str, str], float], *,
                    dom_w: float, ov_w: float, target: float, rf: float,
                    extract: Mapping[str, Any], total_assets: float,
                    sleeve_start: Optional[Mapping[str, Any]] = None,
                    warnings: Optional[List[Any]] = None,
                    alt_starts: Optional[Sequence[Tuple[str, Mapping[str, Any]]]] = None,
                    ) -> Optional[Mapping[str, Any]]:
        """单个目标点的完整求解（阶段一 + 袖内固定点 + 阶段二）。

        返回与旧 optimize_portfolio.solve_portfolio **同形**的 dict（键/语义不变），
        供扫描逐点取 sleeve_params / mu / cov / final。

        `alt_starts`（契约 §2.1/§2.2，2026-10-18 Q2）：透传给 `core.engine.solve_portfolio` 的
        额外确定性初值（敏感性锚定用）；None = 与旧版完全一致。
        """
        if self.universe is None:
            raise ValueError(
                "SensitivityRunner 需要显式 universe（内部池表由装配层 sensitivity_analysis "
                "传入；core 不 import params）")
        from ..core import engine
        return engine.solve_portfolio(
            assets, corr_pairs, dom_w=dom_w, ov_w=ov_w, target=target, rf=rf,
            extract=extract, total_assets=total_assets, universe=self.universe,
            warnings=warnings, sleeve_start=sleeve_start, alt_starts=alt_starts)

    # ── DTO 路径（P4 用）：逐点走 optimizer ──────────────────────────────────
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
