"""行情抓取：唯一网络入口（包装 market_data；ADR §4.4）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence


class DataFetcher(ABC):
    """唯一允许联网的接口。"""

    @abstractmethod
    def fetch_series(self, symbol: str, window: str = "") -> Any:
        raise NotImplementedError


class MarketDataFetcher(DataFetcher):
    """包装既有 market_data（延迟 import，避免 import 期联网/落盘）。"""

    def __init__(self, **options: Any):
        self.options = dict(options)

    def fetch_series(self, symbol: str, window: str = "") -> Any:
        import market_data as MD
        fn = getattr(MD, "fetch_series", None) or getattr(MD, "load_series", None)
        if fn is None:
            raise RuntimeError("market_data 未提供 fetch_series/load_series")
        return fn(symbol, window, **self.options) if window else fn(symbol, **self.options)
