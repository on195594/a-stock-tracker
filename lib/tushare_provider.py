from __future__ import annotations

import os
import random
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd

from lib.market_data import (
    AUTH_MISSING,
    EMPTY_RESPONSE,
    INSUFFICIENT_WINDOW,
    MISSING_COLUMNS,
    PERMISSION_DENIED,
    RATE_LIMITED,
    SCHEMA_CHANGED,
    TIMEOUT,
    UNKNOWN_ERROR,
    MarketDataResult,
)

DAILY_SOURCE = "tushare.daily"
INDEX_DAILY_SOURCE = "tushare.index_daily"
TRADE_CAL_SOURCE = "tushare.trade_cal"
TUSHARE_VOLUME_UNIT = "hand"


class TushareMarketDataProvider:
    """Tushare Pro implementation of the market-data provider boundary."""

    def __init__(
        self,
        token: str | None = None,
        client: Any | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.token = os.environ.get("TUSHARE_TOKEN") if token is None else token
        self._client = client
        self._client_factory = client_factory

    def fetch_score_price(self, code: str, score_date: str) -> MarketDataResult[float]:
        start_date = _compact(_parse_date(score_date) - timedelta(days=10))
        result = self._fetch_daily(code, start_date, _compact(score_date), "score_price")
        if result.value is None or result.value.empty:
            return _scalar_failure(result, DAILY_SOURCE)
        row = result.value.iloc[-1]
        freshness = (_parse_date(score_date) - _parse_date(str(row["date"]))).days
        return MarketDataResult(
            float(row["close"]),
            "ok" if freshness == 0 else "degraded",
            result.source,
            result.fetched_at,
            fallback_reason=None if freshness == 0 else "NEAREST_AVAILABLE_PRICE",
            freshness_days=freshness,
            adjusted=result.adjusted,
            volume_unit=result.volume_unit,
        )

    def fetch_l3_bars(self, code: str, end_date: str, window: int) -> MarketDataResult[pd.DataFrame]:
        lookback_days = max(365, window * 3)
        start_date = _compact(_parse_date(end_date) - timedelta(days=lookback_days))
        result = self._fetch_daily(code, start_date, _compact(end_date), "l3_bars")
        if result.value is None:
            return result
        if len(result.value) < window:
            return MarketDataResult(
                None,
                "failed",
                result.source,
                result.fetched_at,
                error_code=INSUFFICIENT_WINDOW,
                error_message=f"expected at least {window} rows, got {len(result.value)}",
            )
        return result

    def fetch_daily_bars_range(self, code: str, start_date: str, end_date: str) -> MarketDataResult[pd.DataFrame]:
        return self._fetch_daily(code, _compact(start_date), _compact(end_date), "l3_bars")

    def fetch_outcome_price(self, code: str, target_date: str) -> MarketDataResult[float]:
        start_date = _compact(_parse_date(target_date) - timedelta(days=10))
        result = self._fetch_daily(code, start_date, _compact(target_date), "outcome_price")
        if result.value is None or result.value.empty:
            return _scalar_failure(result, DAILY_SOURCE)
        row = result.value.iloc[-1]
        freshness = (_parse_date(target_date) - _parse_date(str(row["date"]))).days
        return MarketDataResult(
            float(row["close"]),
            "ok" if freshness == 0 else "degraded",
            result.source,
            result.fetched_at,
            fallback_reason=None if freshness == 0 else "NEAREST_AVAILABLE_PRICE",
            freshness_days=freshness,
            adjusted=result.adjusted,
            volume_unit=result.volume_unit,
        )

    def _retry_call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        max_retries = 3
        base_delay = 1.0
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                err_msg = str(exc).lower()
                is_rate_limit = "rate" in err_msg or "limit" in err_msg or "频次" in err_msg or "限频" in err_msg
                is_transient = is_rate_limit or "timeout" in err_msg or "timed out" in err_msg or "connection" in err_msg or "disconnect" in err_msg
                if is_transient and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    time.sleep(delay)
                    continue
                raise exc

    def fetch_index_bars(self, symbol: str) -> MarketDataResult[pd.DataFrame]:
        client_result = self._client_or_failure(INDEX_DAILY_SOURCE)
        if isinstance(client_result, MarketDataResult):
            return client_result
        client = client_result
        try:
            df = self._retry_call(client.index_daily, ts_code=to_tushare_index_code(symbol))
        except Exception as exc:
            return _exception_result(INDEX_DAILY_SOURCE, exc)
        return _normalize_tushare_bars(df, INDEX_DAILY_SOURCE, "index_bars")

    def fetch_trade_calendar(self, start_date: str, end_date: str) -> MarketDataResult[pd.DataFrame]:
        client_result = self._client_or_failure(TRADE_CAL_SOURCE)
        if isinstance(client_result, MarketDataResult):
            return client_result
        client = client_result
        try:
            df = self._retry_call(
                client.trade_cal,
                exchange="SSE",
                is_open="1",
                start_date=_compact(start_date),
                end_date=_compact(end_date),
                fields="cal_date",
            )
        except Exception as exc:
            return _exception_result(TRADE_CAL_SOURCE, exc)
        fetched_at = _now()
        if df is None or getattr(df, "empty", False):
            return MarketDataResult(None, "failed", TRADE_CAL_SOURCE, fetched_at, error_code=EMPTY_RESPONSE)
        if "cal_date" not in df.columns:
            return MarketDataResult(None, "failed", TRADE_CAL_SOURCE, fetched_at, error_code=MISSING_COLUMNS)
        normalized = df[["cal_date"]].rename(columns={"cal_date": "date"}).copy()
        normalized["date"] = normalized["date"].map(_format_tushare_date)
        return MarketDataResult(normalized.sort_values("date").reset_index(drop=True), "ok", TRADE_CAL_SOURCE, fetched_at)

    def _fetch_daily(self, code: str, start_date: str, end_date: str, purpose: str) -> MarketDataResult[pd.DataFrame]:
        client_result = self._client_or_failure(DAILY_SOURCE)
        if isinstance(client_result, MarketDataResult):
            return client_result
        client = client_result
        try:
            df = self._retry_call(
                client.daily,
                ts_code=to_tushare_stock_code(code),
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,trade_date,open,high,low,close,vol,amount",
            )
        except Exception as exc:
            return _exception_result(DAILY_SOURCE, exc)
        return _normalize_tushare_bars(df, DAILY_SOURCE, purpose)

    def _client_or_failure(self, source: str) -> Any | MarketDataResult[Any]:
        if not self.token:
            return MarketDataResult(
                None,
                "failed",
                source,
                _now(),
                error_code=AUTH_MISSING,
                error_message="TUSHARE_TOKEN is not configured",
            )
        if self._client is None:
            try:
                if self._client_factory is not None:
                    self._client = self._client_factory(self.token)
                else:
                    import tushare as ts

                    self._client = ts.pro_api(self.token)
            except Exception as exc:
                return _exception_result(source, exc)
        return self._client


def to_tushare_stock_code(code: str) -> str:
    normalized = code.strip().upper()
    if normalized.endswith((".SH", ".SZ")):
        return normalized
    if len(normalized) != 6 or not normalized.isdigit():
        raise ValueError(f"unsupported stock code: {code}")
    if normalized.startswith("6"):
        return f"{normalized}.SH"
    if normalized.startswith(("0", "3")):
        return f"{normalized}.SZ"
    raise ValueError(f"unsupported stock code: {code}")


def to_tushare_index_code(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized.endswith((".SH", ".SZ")):
        return normalized
    if normalized in {"000300", "SH000300"}:
        return "000300.SH"
    if len(normalized) == 6 and normalized.isdigit():
        suffix = "SH" if normalized.startswith("0") else "SZ"
        return f"{normalized}.{suffix}"
    raise ValueError(f"unsupported index symbol: {symbol}")


def _normalize_tushare_bars(df: Any, source: str, purpose: str) -> MarketDataResult[pd.DataFrame]:
    fetched_at = _now()
    if df is None or getattr(df, "empty", False):
        return MarketDataResult(None, "failed", source, fetched_at, error_code=EMPTY_RESPONSE)
    normalized = df.rename(columns={"trade_date": "date", "vol": "volume"}).copy()
    required = {"date", "close"}
    if purpose == "l3_bars":
        required.add("volume")
    missing = required - set(normalized.columns)
    if missing:
        return MarketDataResult(
            None,
            "failed",
            source,
            fetched_at,
            error_code=MISSING_COLUMNS,
            error_message=f"missing columns: {sorted(missing)}",
        )
    keep = [col for col in ["date", "open", "high", "low", "close", "volume"] if col in normalized.columns]
    try:
        normalized["date"] = normalized["date"].map(_format_tushare_date)
        for col in [c for c in ["open", "high", "low", "close", "volume"] if c in normalized.columns]:
            normalized[col] = pd.to_numeric(normalized[col], errors="raise")
    except Exception as exc:
        return MarketDataResult(None, "failed", source, fetched_at, error_code=SCHEMA_CHANGED, error_message=str(exc))
    return MarketDataResult(
        normalized[keep].sort_values("date").reset_index(drop=True),
        "ok",
        source,
        fetched_at,
        adjusted="none",
        volume_unit=TUSHARE_VOLUME_UNIT,
    )


def _scalar_failure(result: MarketDataResult[pd.DataFrame], source: str) -> MarketDataResult[float]:
    return MarketDataResult(
        None,
        "failed",
        result.source or source,
        result.fetched_at,
        error_code=result.error_code,
        error_message=result.error_message,
        adjusted=result.adjusted,
        volume_unit=result.volume_unit,
    )


def _exception_result(source: str, exc: Exception) -> MarketDataResult[pd.DataFrame]:
    message = str(exc)
    lowered = message.lower()
    if "timeout" in lowered or "timed out" in lowered:
        code = TIMEOUT
    elif "rate" in lowered or "limit" in lowered or "频次" in message or "限频" in message:
        code = RATE_LIMITED
    elif "权限" in message or "积分" in message or "permission" in lowered:
        code = PERMISSION_DENIED
    elif "token" in lowered or "auth" in lowered:
        code = AUTH_MISSING
    else:
        code = UNKNOWN_ERROR
    return MarketDataResult(None, "failed", source, _now(), error_code=code, error_message=message)


def _compact(value: str | date) -> str:
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return value.replace("-", "")[:8]


def _format_tushare_date(value: Any) -> str:
    raw = str(value)
    if "-" in raw:
        return raw[:10]
    return datetime.strptime(raw[:8], "%Y%m%d").date().isoformat()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _now() -> str:
    return datetime.now().isoformat()
