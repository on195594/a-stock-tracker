from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from lib.market_data import (
    EMPTY_RESPONSE,
    MISSING_COLUMNS,
    REMOTE_DISCONNECTED,
    SCHEMA_CHANGED,
    INSUFFICIENT_WINDOW,
    UNKNOWN_ERROR,
    MarketDataResult,
)

BAOSTOCK_SOURCE = "baostock.query_history_k_data_plus"
BAOSTOCK_VOLUME_UNIT = "share"


class BaoStockMarketDataProvider:
    """BaoStock daily-bar provider for fallback/backfill use."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self._is_logged_in = False
        self._in_context = False

    def __enter__(self) -> BaoStockMarketDataProvider:
        self._in_context = True
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._in_context = False
        self.close()

    def close(self) -> None:
        if self._is_logged_in and self._client is not None:
            try:
                self._client.logout()
            except Exception:
                pass
            self._is_logged_in = False

    def fetch_score_price(self, code: str, score_date: str) -> MarketDataResult[float]:
        start_date = (_parse_date(score_date) - timedelta(days=10)).isoformat()
        result = self._fetch_bars(code, start_date, score_date, "score_price")
        if result.value is None or result.value.empty:
            return _scalar_failure(result)
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
        start_date = (_parse_date(end_date) - timedelta(days=max(365, window * 3))).isoformat()
        result = self._fetch_bars(code, start_date, end_date, "l3_bars")
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
        return self._fetch_bars(code, start_date, end_date, "l3_bars")

    def fetch_outcome_price(self, code: str, target_date: str) -> MarketDataResult[float]:
        start_date = (_parse_date(target_date) - timedelta(days=10)).isoformat()
        result = self._fetch_bars(code, start_date, target_date, "outcome_price")
        if result.value is None or result.value.empty:
            return _scalar_failure(result)
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

    def fetch_index_bars(self, symbol: str) -> MarketDataResult[pd.DataFrame]:
        start_date = (date.today() - timedelta(days=3650)).isoformat()
        return self._fetch_bars(to_baostock_index_code(symbol), start_date, date.today().isoformat(), "index_bars")

    def _fetch_bars(self, code: str, start_date: str, end_date: str, purpose: str) -> MarketDataResult[pd.DataFrame]:
        client = self._client
        if client is None:
            try:
                import baostock as client
            except Exception as exc:
                return _exception_result(exc)

        if not self._is_logged_in:
            login = client.login()
            if getattr(login, "error_code", "0") != "0":
                return MarketDataResult(
                    None,
                    "failed",
                    BAOSTOCK_SOURCE,
                    _now(),
                    error_code=REMOTE_DISCONNECTED,
                    error_message=getattr(login, "error_msg", "baostock login failed"),
                )
            self._is_logged_in = True
            self._client = client
        try:
            rs = self._client.query_history_k_data_plus(
                to_baostock_stock_code(code),
                "date,code,open,high,low,close,volume,amount,adjustflag,tradestatus",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3",
            )
            if getattr(rs, "error_code", "0") != "0":
                return MarketDataResult(
                    None,
                    "failed",
                    BAOSTOCK_SOURCE,
                    _now(),
                    error_code=UNKNOWN_ERROR,
                    error_message=getattr(rs, "error_msg", "baostock query failed"),
                )
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            df = pd.DataFrame(rows, columns=rs.fields)
            return _normalize_baostock_bars(df, purpose)
        except Exception as exc:
            return _exception_result(exc)
        finally:
            if not self._in_context:
                try:
                    self._client.logout()
                except Exception:
                    pass
                self._is_logged_in = False


def to_baostock_stock_code(code: str) -> str:
    normalized = code.strip().lower()
    if normalized.startswith(("sh.", "sz.")):
        return normalized
    if len(normalized) != 6 or not normalized.isdigit():
        raise ValueError(f"unsupported stock code: {code}")
    if normalized.startswith("6"):
        return f"sh.{normalized}"
    if normalized.startswith(("0", "3")):
        return f"sz.{normalized}"
    raise ValueError(f"unsupported stock code: {code}")


def to_baostock_index_code(symbol: str) -> str:
    normalized = symbol.strip().lower()
    if normalized in {"000300", "sh000300", "sh.000300"}:
        return "sh.000300"
    return to_baostock_stock_code(symbol)


def _normalize_baostock_bars(df: Any, purpose: str) -> MarketDataResult[pd.DataFrame]:
    fetched_at = _now()
    if df is None or getattr(df, "empty", False):
        return MarketDataResult(None, "failed", BAOSTOCK_SOURCE, fetched_at, error_code=EMPTY_RESPONSE)
    required = {"date", "close"}
    if purpose == "l3_bars":
        required.add("volume")
    missing = required - set(df.columns)
    if missing:
        return MarketDataResult(
            None,
            "failed",
            BAOSTOCK_SOURCE,
            fetched_at,
            error_code=MISSING_COLUMNS,
            error_message=f"missing columns: {sorted(missing)}",
        )
    keep = [col for col in ["date", "open", "high", "low", "close", "volume"] if col in df.columns]
    normalized = df[keep].copy()
    try:
        for col in [c for c in ["open", "high", "low", "close", "volume"] if c in normalized.columns]:
            normalized[col] = pd.to_numeric(normalized[col], errors="raise")
    except Exception as exc:
        return MarketDataResult(
            None,
            "failed",
            BAOSTOCK_SOURCE,
            fetched_at,
            error_code=SCHEMA_CHANGED,
            error_message=str(exc),
        )
    normalized = normalized.sort_values("date").reset_index(drop=True)
    return MarketDataResult(
        normalized,
        "ok",
        BAOSTOCK_SOURCE,
        fetched_at,
        adjusted="none",
        volume_unit=BAOSTOCK_VOLUME_UNIT,
    )


def _scalar_failure(result: MarketDataResult[pd.DataFrame]) -> MarketDataResult[float]:
    return MarketDataResult(
        None,
        "failed",
        result.source,
        result.fetched_at,
        error_code=result.error_code,
        error_message=result.error_message,
        adjusted=result.adjusted,
        volume_unit=result.volume_unit,
    )


def _exception_result(exc: Exception) -> MarketDataResult[pd.DataFrame]:
    message = str(exc)
    return MarketDataResult(None, "failed", BAOSTOCK_SOURCE, _now(), error_code=UNKNOWN_ERROR, error_message=message)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _now() -> str:
    return datetime.now().isoformat()
