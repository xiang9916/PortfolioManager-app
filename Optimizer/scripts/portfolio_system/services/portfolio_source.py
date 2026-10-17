"""持仓/配置载荷适配：解析 extract JSON，**不直连数据库**（ADR §4.4）。

P3-b3 真实接线：optimize_portfolio / sensitivity_analysis 的 extract 读取改走 load_extract
（唯一 JSON 读取入口；脚本层不再各自 json.load）。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Mapping


def load_extract(extract_path: str) -> Mapping[str, Any]:
    """读取 Swift 侧写出的 extract JSON（只读；不做任何数据库访问）。"""
    with open(extract_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


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
        return load_extract(extract_path)
