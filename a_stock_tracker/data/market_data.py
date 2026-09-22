from __future__ import annotations

import os
from typing import Any

import pandas as pd

from a_stock_lib.market_data import (
    AUTH_MISSING,
    EMPTY_RESPONSE,
    INSUFFICIENT_WINDOW,
    MISSING_COLUMNS,
    PERMISSION_DENIED,
    RATE_LIMITED,
    REMOTE_DISCONNECTED,
    SCHEMA_CHANGED,
    SOURCE_DISABLED,
    SOURCE_STALE,
    TIMEOUT,
    UNKNOWN_ERROR,
    MarketDataProvider,
    MarketDataResult,
    MarketDataStatus,
    now,
)

_now = now

__all__ = [
    "AUTH_MISSING",
    "EMPTY_RESPONSE",
    "INSUFFICIENT_WINDOW",
    "MISSING_COLUMNS",
    "PERMISSION_DENIED",
    "RATE_LIMITED",
    "REMOTE_DISCONNECTED",
    "SCHEMA_CHANGED",
    "SOURCE_DISABLED",
    "SOURCE_STALE",
    "TIMEOUT",
    "UNKNOWN_ERROR",
    "MarketDataProvider",
    "MarketDataResult",
    "MarketDataStatus",
    "RemovedMarketDataProvider",
    "get_default_market_data_provider",
    "now",
    "_now",
]


class RemovedMarketDataProvider:
    """Disabled market-data provider used until a replacement source is enabled."""

    source = "market_data_provider.disabled"

    def _disabled(self, purpose: str) -> MarketDataResult[Any]:
        return MarketDataResult(
            None,
            "failed",
            self.source,
            _now(),
            error_code=SOURCE_DISABLED,
            error_message=f"{purpose} requires a replacement market data provider",
        )

    def fetch_score_price(self, code: str, score_date: str) -> MarketDataResult[float]:
        return self._disabled("score_price")

    def fetch_l3_bars(self, code: str, end_date: str, window: int) -> MarketDataResult[pd.DataFrame]:
        return self._disabled("l3_bars")

    def fetch_daily_bars_range(self, code: str, start_date: str, end_date: str) -> MarketDataResult[pd.DataFrame]:
        return self._disabled("daily_bars_range")

    def fetch_outcome_price(self, code: str, target_date: str) -> MarketDataResult[float]:
        return self._disabled("outcome_price")

    def fetch_index_bars(self, symbol: str) -> MarketDataResult[pd.DataFrame]:
        return self._disabled("benchmark_price")


def get_default_market_data_provider() -> MarketDataProvider:
    token = os.environ.get("TUSHARE_TOKEN", "")
    if token:
        from a_stock_lib.providers.tushare_quotes import TushareMarketDataProvider

        return TushareMarketDataProvider(token=token)
    return RemovedMarketDataProvider()
