"""在售/申赎状态流水线（包装 fund_pipeline；唯一子进程入口）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, Sequence


class Pipeline(ABC):
    @abstractmethod
    def ensure(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError


class FundPipeline(Pipeline):
    """包装既有 fund_pipeline（延迟 import）。"""

    def __init__(self, **options: Any):
        self.options = dict(options)

    def ensure(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        import fund_pipeline as FP
        fn = getattr(FP, "ensure_availability", None) or getattr(FP, "run", None)
        if fn is None:
            raise RuntimeError("fund_pipeline 未提供 ensure_availability/run")
        return dict(fn(dict(payload), **self.options))
