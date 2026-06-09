from __future__ import annotations

from dataclasses import dataclass
import os
from datetime import datetime
from typing import Any, Generic, Literal, Protocol, TypeVar

import pandas as pd

from lib.cache import insert_market_data_audit, upsert_daily_bars

T = TypeVar("T")

MarketDataStatus = Literal["ok", "degraded", "failed"]

REMOTE_DISCONNECTED = "REMOTE_DISCONNECTED"
TIMEOUT = "TIMEOUT"
RATE_LIMITED = "RATE_LIMITED"
EMPTY_RESPONSE = "EMPTY_RESPONSE"
SCHEMA_CHANGED = "SCHEMA_CHANGED"
MISSING_COLUMNS = "MISSING_COLUMNS"
INSUFFICIENT_WINDOW = "INSUFFICIENT_WINDOW"
SOURCE_STALE = "SOURCE_STALE"
MIXED_SOURCE_VOLUME_UNSAFE = "MIXED_SOURCE_VOLUME_UNSAFE"
UNKNOWN_ERROR = "UNKNOWN_ERROR"
SOURCE_DISABLED = "SOURCE_DISABLED"
AUTH_MISSING = "AUTH_MISSING"
PERMISSION_DENIED = "PERMISSION_DENIED"


@dataclass(frozen=True)
class MarketDataResult(Generic[T]):
    value: T | None
    status: MarketDataStatus
    source: str
    fetched_at: str
    fallback_source: str | None = None
    fallback_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    freshness_days: int | None = None
    adjusted: str = "none"
    volume_unit: str = "unknown"


class MarketDataProvider(Protocol):
    def fetch_score_price(self, code: str, score_date: str) -> MarketDataResult[float]:
        ...

    def fetch_l3_bars(self, code: str, end_date: str, window: int) -> MarketDataResult[pd.DataFrame]:
        ...

    def fetch_daily_bars_range(self, code: str, start_date: str, end_date: str) -> MarketDataResult[pd.DataFrame]:
        ...

    def fetch_outcome_price(self, code: str, target_date: str) -> MarketDataResult[float]:
        ...

    def fetch_index_bars(self, symbol: str) -> MarketDataResult[pd.DataFrame]:
        ...


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
    """Disabled market-data provider used until a replacement source is implemented."""

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


class CompositeMarketDataProvider:
    """Primary/fallback provider. Fallback results are marked degraded."""

    def __init__(self, primary: MarketDataProvider, fallback: MarketDataProvider | None = None):
        self.primary = primary
        self.fallback = fallback

    def fetch_score_price(self, code: str, score_date: str) -> MarketDataResult[float]:
        return self._fetch("fetch_score_price", code, score_date)

    def fetch_l3_bars(self, code: str, end_date: str, window: int) -> MarketDataResult[pd.DataFrame]:
        return self._fetch("fetch_l3_bars", code, end_date, window)

    def fetch_daily_bars_range(self, code: str, start_date: str, end_date: str) -> MarketDataResult[pd.DataFrame]:
        return self._fetch("fetch_daily_bars_range", code, start_date, end_date)

    def fetch_outcome_price(self, code: str, target_date: str) -> MarketDataResult[float]:
        return self._fetch("fetch_outcome_price", code, target_date)

    def fetch_index_bars(self, symbol: str) -> MarketDataResult[pd.DataFrame]:
        return self._fetch("fetch_index_bars", symbol)

    def _fetch(self, method: str, *args: Any) -> MarketDataResult[Any]:
        primary_result = getattr(self.primary, method)(*args)
        if primary_result.status != "failed" or self.fallback is None:
            return primary_result
        fallback_result = getattr(self.fallback, method)(*args)
        if fallback_result.status == "failed":
            return fallback_result
        return MarketDataResult(
            fallback_result.value,
            "degraded",
            fallback_result.source,
            fallback_result.fetched_at,
            fallback_source=primary_result.source,
            fallback_reason=primary_result.error_code or primary_result.fallback_reason or "PRIMARY_FAILED",
            freshness_days=fallback_result.freshness_days,
            adjusted=fallback_result.adjusted,
            volume_unit=fallback_result.volume_unit,
        )


def get_default_market_data_provider() -> MarketDataProvider:
    if os.environ.get("TUSHARE_TOKEN"):
        from lib.tushare_provider import TushareMarketDataProvider

        fallback = None
        try:
            from lib.baostock_provider import BaoStockMarketDataProvider

            fallback = BaoStockMarketDataProvider()
        except Exception:
            fallback = None
        return CompositeMarketDataProvider(TushareMarketDataProvider(), fallback)
    return RemovedMarketDataProvider()


def get_market_data_backfill_provider() -> MarketDataProvider:
    if os.environ.get("TUSHARE_TOKEN"):
        return get_default_market_data_provider()
    if os.environ.get("MARKET_DATA_ALLOW_BAOSTOCK_ONLY") == "1":
        from lib.baostock_provider import BaoStockMarketDataProvider

        return BaoStockMarketDataProvider()
    return RemovedMarketDataProvider()


def _normalize_bars_result(df: Any, source: str, purpose: str) -> MarketDataResult[pd.DataFrame]:
    fetched_at = _now()
    if df is None or getattr(df, "empty", False):
        return MarketDataResult(None, "failed", source, fetched_at, error_code=EMPTY_RESPONSE)
    rename_map = {
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
    }
    normalized = df.rename(columns=rename_map).copy()
    required = {"date", "close"}
    if purpose == "l3_bars":
        required.add("volume")
    if not required.issubset(set(normalized.columns)):
        return MarketDataResult(
            None,
            "failed",
            source,
            fetched_at,
            error_code=MISSING_COLUMNS,
            error_message=f"missing columns: {sorted(required - set(normalized.columns))}",
        )
    keep = [col for col in ["date", "open", "high", "low", "close", "volume"] if col in normalized.columns]
    return MarketDataResult(normalized[keep], "ok", source, fetched_at, adjusted="none", volume_unit="share")


def _exception_result(source: str, exc: Exception) -> MarketDataResult[pd.DataFrame]:
    message = str(exc)
    lowered = message.lower()
    if "timeout" in lowered:
        code = TIMEOUT
    elif "disconnect" in lowered or "connection" in lowered:
        code = REMOTE_DISCONNECTED
    elif "rate" in lowered or "limit" in lowered:
        code = RATE_LIMITED
    else:
        code = UNKNOWN_ERROR
    return MarketDataResult(None, "failed", source, _now(), error_code=code, error_message=message)


def _now() -> str:
    return datetime.now().isoformat()


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
