"""行情抓取：唯一网络入口（包装 market_data；ADR §4.4）。

P3-b3 真实接线：按 market_data 的真实函数名转发（cached_series / series_for_broad_key /
yahoo_history / eastmoney_nav）。延迟 import 保持 import 期无副作用。

⚠️ 运行时**没有**生产调用点：优化器运行时只读校准 JSON（params.py 加载），
行情抓取发生在校准阶段（calibrate_params.py ↔ market_data）。本模块是给 P4 编排
（callers/services 接管取数）预留的唯一网络入口，签名与 market_data 一致。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


def yahoo_history(ticker: str, years: Optional[int] = None, adjusted: bool = False) -> Any:
    import market_data as MD
    return MD.yahoo_history(ticker, years=years, adjusted=adjusted)


def eastmoney_nav(code: str, years: Optional[int] = None) -> Any:
    import market_data as MD
    return MD.eastmoney_nav(code, years=years)


def cached_series(*args: Any, **kwargs: Any) -> Any:
    import market_data as MD
    return MD.cached_series(*args, **kwargs)


def series_for_broad_key(*args: Any, **kwargs: Any) -> Any:
    import market_data as MD
    return MD.series_for_broad_key(*args, **kwargs)


class DataFetcher(ABC):
    """唯一允许联网的接口。"""

    @abstractmethod
    def fetch_series(self, symbol: str, window: str = "") -> Any:
        raise NotImplementedError


class MarketDataFetcher(DataFetcher):
    """包装既有 market_data（延迟 import）。"""

    def __init__(self, work_dir: str = "", log: Any = None):
        self.work_dir = work_dir
        self.log = log

    def fetch_series(self, symbol: str, window: str = "") -> Any:
        return cached_series(self.work_dir, symbol, log=self.log)
