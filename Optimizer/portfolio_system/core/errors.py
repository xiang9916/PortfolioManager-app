"""内核异常分级（ADR 0003 §6）。内核不抛裸异常；每次降级都要有带 code 的告警。"""

from __future__ import annotations

from typing import Any, Optional


class OptimizerError(Exception):
    """内核异常基类：带 key/取值/来源，便于定位（绝不静默继续）。"""

    code = "other"

    def __init__(self, message: str, *, key: Optional[str] = None,
                 value: Any = None, source: Optional[str] = None):
        super().__init__(message)
        self.key = key
        self.value = value
        self.source = source

    def as_warning(self):  # 延迟 import，避免 models 与 core 的循环
        from ..models.data_types import Warning
        return Warning(code=self.code, message=str(self), severity="critical")


class InputError(OptimizerError):
    """输入本身不合法（资产为空 / σ 非正或 NaN / 上下界冲突 / 目标为 NaN）。"""

    code = "other"


class NumericalError(OptimizerError):
    """数值层失败。"""

    code = "other"


class SingularCovariance(NumericalError):
    """相关性矩阵奇异/非 PSD：先 project_psd 修复，仍不可用则丢弃该资产并报警。"""

    code = "other"


class SolverFailure(NumericalError):
    """SLSQP 未收敛 / 返回越界点：换起点重试，全失败则标记该袖不可交付。"""

    code = "other"


class InfeasibleTarget(OptimizerError):
    """组合层目标不可行：交付最大可行收益解 + 显式告警（target.infeasible）。"""

    code = "target.infeasible"
