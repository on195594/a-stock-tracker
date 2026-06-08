from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import time
from typing import Any, Generic, Literal, Protocol, TypeVar

import akshare as ak
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


class MarketDataProvider(Protocol):
    def fetch_score_price(self, code: str, score_date: str) -> MarketDataResult[float]:
        ...

    def fetch_l3_bars(self, code: str, end_date: str, window: int) -> MarketDataResult[pd.DataFrame]:
        ...

    def fetch_outcome_price(self, code: str, target_date: str) -> MarketDataResult[float]:
        ...

    def fetch_index_bars(self, symbol: str) -> MarketDataResult[pd.DataFrame]:
        ...


class AkshareMarketDataProvider:
    """AKShare boundary. Callers receive normalized values and structured failures."""

    def fetch_score_price(self, code: str, score_date: str) -> MarketDataResult[float]:
        prefix = "sh" if code.startswith("6") else "sz"
        start = (datetime.fromisoformat(score_date) - timedelta(days=5)).strftime("%Y%m%d")
        end = score_date.replace("-", "")
        result = self._fetch_tx_bars(f"{prefix}{code}", start, end, "score_price")
        if result.status == "failed" or result.value is None:
            return MarketDataResult(
                None,
                "failed",
                result.source,
                result.fetched_at,
                error_code=result.error_code,
                error_message=result.error_message,
            )
        row = result.value.iloc[-1]
        trade_date = str(row["date"])[:10]
        freshness = (datetime.fromisoformat(score_date) - datetime.fromisoformat(trade_date)).days
        if freshness > 5:
            return MarketDataResult(
                None,
                "failed",
                result.source,
                result.fetched_at,
                error_code=SOURCE_STALE,
                error_message=f"latest trade date {trade_date} is {freshness} days before {score_date}",
                freshness_days=freshness,
            )
        return MarketDataResult(float(row["close"]), "ok", result.source, result.fetched_at, freshness_days=freshness)

    def fetch_l3_bars(self, code: str, end_date: str, window: int) -> MarketDataResult[pd.DataFrame]:
        start = (datetime.fromisoformat(end_date) - timedelta(days=max(220, window * 2))).strftime("%Y%m%d")
        end = end_date.replace("-", "")
        prefix = "sh" if code.startswith("6") else "sz"
        primary = self._fetch_tx_bars(f"{prefix}{code}", start, end, "l3_bars")
        if primary.status != "failed" and primary.value is not None and len(primary.value) >= window:
            return primary

        fallback = self._fetch_em_bars(code, start, end, "l3_bars")
        if fallback.status != "failed":
            return MarketDataResult(
                fallback.value,
                "degraded",
                fallback.source,
                fallback.fetched_at,
                fallback_source=primary.source,
                fallback_reason=primary.error_code or INSUFFICIENT_WINDOW,
            )
        return fallback

    def fetch_outcome_price(self, code: str, target_date: str) -> MarketDataResult[float]:
        for delta in range(11):
            d = datetime.fromisoformat(target_date) - timedelta(days=delta)
            compact = d.strftime("%Y%m%d")
            result = self._fetch_em_bars(code, compact, compact, "outcome_price")
            if result.status != "failed" and result.value is not None and not result.value.empty:
                return MarketDataResult(
                    float(result.value.iloc[-1]["close"]),
                    "ok" if delta == 0 else "degraded",
                    result.source,
                    result.fetched_at,
                    fallback_reason=None if delta == 0 else "NEAREST_AVAILABLE_PRICE",
                    freshness_days=delta,
                )
        return MarketDataResult(None, "failed", "akshare.stock_zh_a_hist", _now(), error_code=EMPTY_RESPONSE)

    def fetch_index_bars(self, symbol: str) -> MarketDataResult[pd.DataFrame]:
        source = "akshare.stock_zh_index_daily_tx"
        try:
            df = ak.stock_zh_index_daily_tx(symbol=symbol)
            fetched_at = _now()
            if df is None or getattr(df, "empty", False):
                return MarketDataResult(None, "failed", source, fetched_at, error_code=EMPTY_RESPONSE)
            if not {"date", "close"}.issubset(set(df.columns)):
                return MarketDataResult(
                    None,
                    "failed",
                    source,
                    fetched_at,
                    error_code=MISSING_COLUMNS,
                    error_message=f"missing date/close columns, current columns: {list(df.columns)}",
                )
            return MarketDataResult(df[["date", "close"]].copy(), "ok", source, fetched_at)
        except Exception as exc:
            return _exception_result(source, exc)

    def _fetch_tx_bars(self, symbol: str, start: str, end: str, purpose: str) -> MarketDataResult[pd.DataFrame]:
        return _fetch_with_retries(
            lambda: ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end),
            "akshare.stock_zh_a_hist_tx",
            purpose,
        )

    def _fetch_em_bars(self, code: str, start: str, end: str, purpose: str) -> MarketDataResult[pd.DataFrame]:
        return _fetch_with_retries(
            lambda: ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust=""),
            "akshare.stock_zh_a_hist",
            purpose,
        )


def _fetch_with_retries(fetch_fn: Any, source: str, purpose: str, retries: int = 3) -> MarketDataResult[pd.DataFrame]:
    last_error: MarketDataResult[pd.DataFrame] | None = None
    for attempt in range(retries):
        try:
            df = fetch_fn()
            return _normalize_bars_result(df, source, purpose)
        except Exception as exc:
            last_error = _exception_result(source, exc)
            if attempt < retries - 1 and last_error.error_code in {REMOTE_DISCONNECTED, TIMEOUT, RATE_LIMITED, UNKNOWN_ERROR}:
                time.sleep(2 ** attempt)
                continue
            return last_error
    return last_error or MarketDataResult(None, "failed", source, _now(), error_code=UNKNOWN_ERROR)


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
    return MarketDataResult(normalized[keep], "ok", source, fetched_at)


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
        self.provider = provider or AkshareMarketDataProvider()

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
                    adjusted="",
                    quality_status=result.status,
                    fetched_at=result.fetched_at,
                    error_code=result.error_code,
                )
        self.conn.commit()
        ok = sum(1 for r in results.values() if r.status == "ok")
        degraded = sum(1 for r in results.values() if r.status == "degraded")
        failed = sum(1 for r in results.values() if r.status == "failed")
        return MarketDataCoverage(len(results), ok, degraded, failed, results)
