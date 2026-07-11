"""Validated file cache and quota coordination for L3 v2 qfq data."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd


LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = 1
SOURCE = "tushare.daily+adj_factor"
MIN_ADJ_FACTOR_INTERVAL_SECONDS = 61.0
CODE_RE = re.compile(r"^[0-9]{6}$")
CSV_COLUMNS = (
    "code",
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "adj_factor",
    "source",
    "fetched_at",
)


class QfqCacheError(RuntimeError):
    """Base error for qfq cache operations."""


class CacheMissingError(QfqCacheError):
    """Raised when either half of a stock cache is absent."""


class CacheInvalidError(QfqCacheError):
    """Raised when a cache violates its persisted contract."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class CacheWriteError(QfqCacheError):
    """Raised when an atomic cache write cannot be completed."""


class LockUnavailableError(QfqCacheError):
    """Raised when another collector owns the cache lock."""


def validate_code(code: str) -> str:
    """Return a safe six-digit stock code or reject it."""
    if not CODE_RE.fullmatch(code):
        raise CacheInvalidError("INVALID_CODE")
    return code


def to_tushare_code(code: str) -> str:
    """Convert a validated mainland stock code to a Tushare code."""
    validate_code(code)
    suffix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
    return f"{code}.{suffix}"


def _compact_date(value: date | str) -> str:
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value)
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        try:
            return datetime.strptime(text, "%Y%m%d").strftime("%Y%m%d")
        except ValueError as exc:
            raise CacheInvalidError("INVALID_WINDOW_DATE") from exc


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def build_cache_frame(
    code: str,
    daily: pd.DataFrame,
    factors: pd.DataFrame,
    fetched_at: str | None = None,
) -> pd.DataFrame:
    """Align raw Tushare daily and factor results into the cache schema."""
    validate_code(code)
    if daily is None or factors is None or daily.empty or factors.empty:
        raise CacheInvalidError("EMPTY_DATA")
    daily_required = {"trade_date", "open", "high", "low", "close", "vol"}
    factor_required = {"trade_date", "adj_factor"}
    if not daily_required.issubset(daily.columns) or not factor_required.issubset(factors.columns):
        raise CacheInvalidError("MISSING_COLUMNS")
    daily_copy = daily[list(daily_required)].copy()
    factor_copy = factors[list(factor_required)].copy()
    daily_copy["trade_date"] = daily_copy["trade_date"].astype(str)
    factor_copy["trade_date"] = factor_copy["trade_date"].astype(str)
    if daily_copy["trade_date"].duplicated().any() or factor_copy["trade_date"].duplicated().any():
        raise CacheInvalidError("DUPLICATE_TRADE_DATE")
    if set(daily_copy["trade_date"]) != set(factor_copy["trade_date"]):
        raise CacheInvalidError("DATE_MISMATCH")
    merged = daily_copy.merge(factor_copy, on="trade_date", how="inner", validate="one_to_one")
    merged["code"] = code
    merged["ts_code"] = to_tushare_code(code)
    merged["source"] = SOURCE
    merged["fetched_at"] = fetched_at or datetime.now(timezone.utc).isoformat()
    merged = merged[list(CSV_COLUMNS)].sort_values("trade_date", kind="stable").reset_index(drop=True)
    return validate_cache_frame(merged, expected_code=code)


def validate_cache_frame(frame: pd.DataFrame, expected_code: str | None = None) -> pd.DataFrame:
    """Validate and normalize an in-memory raw qfq cache frame."""
    if tuple(frame.columns) != CSV_COLUMNS:
        raise CacheInvalidError("SCHEMA_COLUMNS")
    if frame.empty:
        raise CacheInvalidError("EMPTY_DATA")
    normalized = frame.copy()
    normalized["code"] = normalized["code"].astype(str).str.zfill(6)
    codes = set(normalized["code"])
    if len(codes) != 1:
        raise CacheInvalidError("MULTIPLE_CODES")
    code = next(iter(codes))
    validate_code(code)
    if expected_code is not None and code != expected_code:
        raise CacheInvalidError("CODE_MISMATCH")
    normalized["trade_date"] = normalized["trade_date"].astype(str)
    try:
        parsed_dates = pd.to_datetime(normalized["trade_date"], format="%Y%m%d", errors="raise")
    except (TypeError, ValueError) as exc:
        raise CacheInvalidError("INVALID_TRADE_DATE") from exc
    if normalized["trade_date"].duplicated().any():
        raise CacheInvalidError("DUPLICATE_TRADE_DATE")
    if not parsed_dates.is_monotonic_increasing:
        raise CacheInvalidError("UNSORTED_TRADE_DATE")
    numeric = ["open", "high", "low", "close", "vol", "adj_factor"]
    for column in numeric:
        try:
            normalized[column] = pd.to_numeric(normalized[column], errors="raise")
        except (TypeError, ValueError) as exc:
            raise CacheInvalidError(f"NON_NUMERIC_{column.upper()}") from exc
        if normalized[column].isna().any():
            raise CacheInvalidError(f"NON_NUMERIC_{column.upper()}")
    if (normalized["adj_factor"] <= 0).any():
        raise CacheInvalidError("NON_POSITIVE_ADJ_FACTOR")
    if set(normalized["source"].astype(str)) != {SOURCE}:
        raise CacheInvalidError("SOURCE_MISMATCH")
    expected_ts_code = to_tushare_code(code)
    if set(normalized["ts_code"].astype(str)) != {expected_ts_code}:
        raise CacheInvalidError("TS_CODE_MISMATCH")
    return normalized


def write_cache(
    cache_dir: Path | str,
    code: str,
    frame: pd.DataFrame,
    requested_start: date | str,
    requested_end: date | str,
) -> dict[str, Any]:
    """Atomically persist validated CSV data followed by complete metadata."""
    code = validate_code(code)
    normalized = validate_cache_frame(frame, expected_code=code)
    start_s = _compact_date(requested_start)
    end_s = _compact_date(requested_end)
    if start_s > end_s:
        raise CacheInvalidError("INVALID_WINDOW")
    root = Path(cache_dir)
    csv_path = root / f"{code}.csv"
    meta_path = root / f"{code}.meta.json"
    csv_payload = normalized.to_csv(index=False, lineterminator="\n").encode("utf-8")
    checksum = hashlib.sha256(csv_payload).hexdigest()
    fetched_values = normalized["fetched_at"].astype(str).tolist()
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "code": code,
        "ts_code": to_tushare_code(code),
        "requested_start": start_s,
        "requested_end": end_s,
        "min_trade_date": str(normalized["trade_date"].iloc[0]),
        "max_trade_date": str(normalized["trade_date"].iloc[-1]),
        "row_count": len(normalized),
        "source": SOURCE,
        "fetched_at": max(fetched_values),
        "status": "complete",
        "sha256": checksum,
    }
    csv_bak: Path | None = None
    if csv_path.exists() and meta_path.exists():
        csv_bak = csv_path.with_name(f"{code}.csv.bak")
        shutil.copy2(csv_path, csv_bak)
    try:
        _atomic_write_bytes(csv_path, csv_payload)
        _atomic_write_bytes(meta_path, _json_bytes(metadata))
    except Exception as exc:
        if csv_bak is not None and csv_bak.exists():
            try:
                csv_bak.replace(csv_path)
            except OSError:
                pass
        if isinstance(exc, OSError):
            raise CacheWriteError(f"ATOMIC_WRITE_FAILED:{exc}") from exc
        raise
    # Cleanup backup only after both writes succeed; failure is harmless.
    if csv_bak is not None and csv_bak.exists():
        try:
            csv_bak.unlink()
        except OSError:
            pass
    return metadata


def read_cache(
    cache_dir: Path | str,
    code: str,
    requested_start: date | str,
    requested_end: date | str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load a complete cache after checksum, content, and window validation."""
    code = validate_code(code)
    root = Path(cache_dir)
    csv_path = root / f"{code}.csv"
    meta_path = root / f"{code}.meta.json"
    if not csv_path.is_file() or not meta_path.is_file():
        raise CacheMissingError(code)
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CacheInvalidError("METADATA_UNREADABLE") from exc
    required_meta = {
        "schema_version", "code", "ts_code", "requested_start", "requested_end",
        "min_trade_date", "max_trade_date", "row_count", "source", "fetched_at", "status", "sha256",
    }
    if not required_meta.issubset(metadata):
        raise CacheInvalidError("METADATA_FIELDS")
    if metadata["schema_version"] != SCHEMA_VERSION:
        raise CacheInvalidError("SCHEMA_VERSION")
    if metadata["status"] != "complete":
        raise CacheInvalidError("STATUS_NOT_COMPLETE")
    if metadata["code"] != code or metadata["ts_code"] != to_tushare_code(code):
        raise CacheInvalidError("METADATA_CODE_MISMATCH")
    if metadata["source"] != SOURCE:
        raise CacheInvalidError("METADATA_SOURCE_MISMATCH")
    try:
        csv_payload = csv_path.read_bytes()
    except OSError as exc:
        raise CacheInvalidError("CSV_UNREADABLE") from exc
    if hashlib.sha256(csv_payload).hexdigest() != metadata["sha256"]:
        raise CacheInvalidError("CHECKSUM_MISMATCH")
    try:
        frame = pd.read_csv(csv_path, dtype={"code": str, "ts_code": str, "trade_date": str})
    except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise CacheInvalidError("CSV_UNREADABLE") from exc
    frame = validate_cache_frame(frame, expected_code=code)
    if len(frame) != metadata["row_count"]:
        raise CacheInvalidError("ROW_COUNT_MISMATCH")
    if str(frame["trade_date"].iloc[0]) != metadata["min_trade_date"] or str(frame["trade_date"].iloc[-1]) != metadata["max_trade_date"]:
        raise CacheInvalidError("TRADE_DATE_RANGE_MISMATCH")
    start_s = _compact_date(requested_start)
    end_s = _compact_date(requested_end)
    if str(metadata["requested_start"]) > start_s or str(metadata["requested_end"]) < end_s:
        raise CacheInvalidError("INCOMPLETE_WINDOW")
    window = frame[(frame["trade_date"] >= start_s) & (frame["trade_date"] <= end_s)].reset_index(drop=True)
    if window.empty:
        raise CacheInvalidError("EMPTY_REQUESTED_WINDOW")
    return window, metadata


def derive_qfq_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    """Purely derive qfq OHLCV using the latest factor in the supplied window."""
    required = {"trade_date", "open", "high", "low", "close", "vol", "adj_factor"}
    if raw.empty:
        raise CacheInvalidError("EMPTY_REQUESTED_WINDOW")
    if not required.issubset(raw.columns):
        raise CacheInvalidError("MISSING_COLUMNS")
    result = raw.copy(deep=True).sort_values("trade_date", kind="stable").reset_index(drop=True)
    numeric = ["open", "high", "low", "close", "vol", "adj_factor"]
    for column in numeric:
        try:
            result[column] = pd.to_numeric(result[column], errors="raise")
        except (TypeError, ValueError) as exc:
            raise CacheInvalidError(f"NON_NUMERIC_{column.upper()}") from exc
    if result[numeric].isna().any().any():
        raise CacheInvalidError("NON_NUMERIC_OHLCV_FACTOR")
    if (result["adj_factor"] <= 0).any():
        raise CacheInvalidError("NON_POSITIVE_ADJ_FACTOR")
    latest_factor = float(result["adj_factor"].iloc[-1])
    ratios = result["adj_factor"].astype(float) / latest_factor
    for column in ("open", "high", "low", "close"):
        result[column] = result[column].astype(float) * ratios
    result["vol"] = result["vol"].astype(float) / ratios
    return result


class FixedIntervalRateLimiter:
    """Enforce a persistent fixed interval between adj_factor request starts."""

    def __init__(self, cache_dir: Path | str, interval_seconds: float = MIN_ADJ_FACTOR_INTERVAL_SECONDS) -> None:
        self.state_path = Path(cache_dir) / ".adj_factor_rate_state.json"
        self.interval_seconds = float(interval_seconds)
        self._last_monotonic: float | None = None

    def acquire(self) -> None:
        """Wait if needed, then persist the resampled actual request start."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.state_path.open("a+b") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.seek(0)
                raw = fh.read()
                persistent_wait = 0.0
                now_monotonic = time.monotonic()
                now_wall = time.time()
                if raw:
                    try:
                        previous_state = json.loads(raw)
                        last_wall = float(previous_state["last_request_started_epoch"])
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                        raise CacheInvalidError("RATE_STATE_INVALID") from exc
                    persistent_wait = max(
                        0.0, self.interval_seconds - max(0.0, now_wall - last_wall)
                    )
                process_wait = 0.0
                if self._last_monotonic is not None:
                    process_wait = max(
                        0.0, self.interval_seconds - (now_monotonic - self._last_monotonic)
                    )
                wait = max(process_wait, persistent_wait)
                if wait > 0:
                    time.sleep(wait)
                actual_monotonic = time.monotonic()
                actual_wall = time.time()
                state = {
                    "last_request_started_epoch": actual_wall,
                    "last_request_started_utc": datetime.fromtimestamp(
                        actual_wall, timezone.utc
                    ).isoformat(),
                }
                fh.seek(0)
                fh.truncate()
                fh.write(_json_bytes(state))
                fh.flush()
        except OSError as exc:
            raise CacheWriteError(f"RATE_STATE_WRITE_FAILED:{exc}") from exc
        self._last_monotonic = actual_monotonic


class CollectorLock:
    """Fail-fast exclusive process lock for one cache directory."""

    def __init__(self, cache_dir: Path | str) -> None:
        self.path = Path(cache_dir) / ".collector.lock"
        self._handle: Any | None = None

    def acquire(self) -> "CollectorLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise LockUnavailableError(f"COLLECTOR_LOCKED:{self.path}") from exc
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "CollectorLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()
