from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import pandas as pd

from a_stock_lib.market_data import (
    AUTH_MISSING,
    EMPTY_RESPONSE,
    INSUFFICIENT_WINDOW,
    MISSING_COLUMNS,
    MIXED_SOURCE_VOLUME_UNSAFE,
    PERMISSION_DENIED,
    RATE_LIMITED,
    REMOTE_DISCONNECTED,
    SCHEMA_CHANGED,
    SOURCE_DISABLED,
    SOURCE_STALE,
    TIMEOUT,
    UNKNOWN_ERROR,
    CompositeMarketDataProvider,
    MarketDataProvider,
    MarketDataResult,
    MarketDataStatus,
    exception_result,
    normalize_bars_result,
    now,
)

from lib.cache import insert_market_data_audit, upsert_daily_bars

_normalize_bars_result = normalize_bars_result
_exception_result = exception_result
_now = now

__all__ = [
    "AUTH_MISSING",
    "EMPTY_RESPONSE",
    "INSUFFICIENT_WINDOW",
    "MISSING_COLUMNS",
    "MIXED_SOURCE_VOLUME_UNSAFE",
    "PERMISSION_DENIED",
    "RATE_LIMITED",
    "REMOTE_DISCONNECTED",
    "SCHEMA_CHANGED",
    "SOURCE_DISABLED",
    "SOURCE_STALE",
    "TIMEOUT",
    "UNKNOWN_ERROR",
    "CompositeMarketDataProvider",
    "MarketDataCacheService",
    "MarketDataCoverage",
    "MarketDataProvider",
    "MarketDataResult",
    "MarketDataStatus",
    "RemovedMarketDataProvider",
    "ak",
    "exception_result",
    "get_default_market_data_provider",
    "get_market_data_backfill_provider",
    "normalize_bars_result",
    "now",
    "_exception_result",
    "_normalize_bars_result",
    "_now",
]


class _RemovedAkshareShim:
    """Test compatibility shim. Production code must not call this object."""

    def stock_zh_a_hist_tx(self, **kwargs: Any) -> Any:
        raise RuntimeError("AKShare market-data interface has been removed")

    def stock_zh_a_hist(self, **kwargs: Any) -> Any:
        raise RuntimeError("AKShare/Eastmoney market-data interface has been removed")

    def stock_zh_index_daily_tx(self, **kwargs: Any) -> Any:
        raise RuntimeError("AKShare index market-data interface has been removed")


ak = _RemovedAkshareShim()


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
        from a_stock_lib.providers.baostock_quotes import BaoStockMarketDataProvider
        from a_stock_lib.providers.tushare_quotes import TushareMarketDataProvider

        fallback = BaoStockMarketDataProvider()
        return CompositeMarketDataProvider(TushareMarketDataProvider(token=token), fallback)
    return RemovedMarketDataProvider()


def get_market_data_backfill_provider() -> MarketDataProvider:
    if os.environ.get("TUSHARE_TOKEN"):
        return get_default_market_data_provider()
    if os.environ.get("MARKET_DATA_ALLOW_BAOSTOCK_ONLY") == "1":
        from a_stock_lib.providers.baostock_quotes import BaoStockMarketDataProvider

        return BaoStockMarketDataProvider()
    return RemovedMarketDataProvider()


@dataclass(frozen=True)
class MarketDataCoverage:
    total: int
    ok: int
    degraded: int
    failed: int
    by_code: dict[str, MarketDataResult[pd.DataFrame]]


class MarketDataCacheService:
    def __init__(self, conn: Any, provider: MarketDataProvider | None = None):
        self.conn = conn
        self.provider = provider or get_default_market_data_provider()

    def refresh_daily_bars(self, codes: list[str], end_date: str, window: int = 120) -> MarketDataCoverage:
        results: dict[str, MarketDataResult[pd.DataFrame]] = {}
        for code in codes:
            result = self.provider.fetch_l3_bars(code, end_date, window)
            results[code] = result
            insert_market_data_audit(self.conn, result, "l3_bars", code, end_date)
            if result.status != "failed" and result.value is not None:
                upsert_daily_bars(
                    self.conn,
                    code,
                    result.value,
                    result.source,
                    adjusted=result.adjusted,
                    volume_unit=result.volume_unit,
                    quality_status=result.status,
                    fetched_at=result.fetched_at,
                    error_code=result.error_code,
                )
        self.conn.commit()
        ok = sum(1 for r in results.values() if r.status == "ok")
        degraded = sum(1 for r in results.values() if r.status == "degraded")
        failed = sum(1 for r in results.values() if r.status == "failed")
        return MarketDataCoverage(len(results), ok, degraded, failed, results)
