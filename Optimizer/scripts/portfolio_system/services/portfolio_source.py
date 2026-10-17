"""持仓/配置载荷适配：解析 extract JSON，**不直连数据库**（ADR §4.4）。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Mapping


class PortfolioSource(ABC):
    """只读载荷：Swift 侧读库后写成 extract JSON 交给优化器。"""

    @abstractmethod
    def load(self, extract_path: str) -> Mapping[str, Any]:
        raise NotImplementedError


class JsonPayloadSource(PortfolioSource):
    """从文件或已在内存里的 JSON 载荷读取（不连接任何数据库）。"""

    def __init__(self, payload: Mapping[str, Any] | None = None):
        self._payload = dict(payload) if payload is not None else None

    def load(self, extract_path: str = "") -> Mapping[str, Any]:
        if self._payload is not None:
            return dict(self._payload)
        if not extract_path:
            raise ValueError("需要 extract_path 或构造时提供 payload")
        with open(extract_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
