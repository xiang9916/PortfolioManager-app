"""在售/申赎状态流水线（包装 fund_pipeline；**唯一子进程入口**）。

P3-b3 起是**真实接线**：本模块按 fund_pipeline 的真实函数名做薄转发（不再用
"方法存在就调"的宽松猜测）；optimize_portfolio / sensitivity_analysis 改为只 import 本模块，
脚本层不再直接 import fund_pipeline。
延迟 import 保持 import 期无副作用（不联网、不开子进程）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional, Sequence


# ── 真实转发（与 fund_pipeline 同名同签名；*a/**kw 原样透传，行为逐字节不变）──────────
def ensure_work_dir(path: Optional[str] = None) -> str:
    import fund_pipeline as FP
    return FP.ensure_work_dir(path)


def ensure_pipeline(*args: Any, **kwargs: Any) -> Any:
    import fund_pipeline as FP
    return FP.ensure_pipeline(*args, **kwargs)


def load_pipeline(*args: Any, **kwargs: Any) -> Any:
    import fund_pipeline as FP
    return FP.load_pipeline(*args, **kwargs)


def live_anchors_from_extract(extract: Mapping[str, Any]) -> Any:
    import fund_pipeline as FP
    return FP.live_anchors_from_extract(extract)


def build_investable_assets(*args: Any, **kwargs: Any) -> Any:
    import fund_pipeline as FP
    return FP.build_investable_assets(*args, **kwargs)


# ── ADR §4.4 接口（供未来替换实现；现有实现 = 上面四个真实转发）────────────────────
class Pipeline(ABC):
    """在售/申赎状态流水线接口：唯一允许开子进程/联网获取基金状态的入口。"""

    @abstractmethod
    def ensure(self, work_dir: str, live_anchors: Optional[Sequence[str]] = None) -> Any:
        raise NotImplementedError

    @abstractmethod
    def load(self, work_dir: str) -> Any:
        raise NotImplementedError


class FundPipeline(Pipeline):
    """包装既有 fund_pipeline（默认实现）。"""

    def __init__(self, **options: Any):
        self.options = dict(options)

    def ensure(self, work_dir: str, live_anchors: Optional[Sequence[str]] = None) -> Any:
        return ensure_pipeline(work_dir, live_anchors=live_anchors, **self.options)

    def load(self, work_dir: str) -> Any:
        return load_pipeline(work_dir, **self.options)
