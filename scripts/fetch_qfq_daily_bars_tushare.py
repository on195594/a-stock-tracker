"""Fetch TuShare QFQ bars and the CSI 300 total-return benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import queue
import random
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import tushare as ts

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_lib.providers.tushare_fundamentals import read_tushare_token
from a_stock_lib.providers.tushare_quotes import to_tushare_stock_code
from a_stock_tracker.config import WATCHLIST
from a_stock_tracker.data.cache import DB_PATH, upsert_daily_bars
from a_stock_tracker.paths import RUNTIME_TRADING_CALENDAR_PATH, TRACKED_TRADING_CALENDAR_PATH

logger = logging.getLogger(__name__)

PRODUCTION_ADJUSTED = "qfq"
SOURCE = "tushare.pro_bar.qfq"
BENCHMARK_SOURCE = "tushare.index_daily"
BENCHMARK_API_SYMBOL = "H00300.CSI"
BENCHMARK_DB_SYMBOL = "H00300"
CALENDAR_SOURCE = "tushare.trade_cal"
CALENDAR_OFFICIAL_REFERENCE = "上证公告〔2025〕45号"
CALENDAR_OFFICIAL_CLOSURES = (
    (date(2026, 1, 1), date(2026, 1, 3), "元旦"),
    (date(2026, 2, 15), date(2026, 2, 23), "春节"),
    (date(2026, 4, 4), date(2026, 4, 6), "清明节"),
    (date(2026, 5, 1), date(2026, 5, 5), "劳动节"),
    (date(2026, 6, 19), date(2026, 6, 21), "端午节"),
    (date(2026, 9, 25), date(2026, 9, 27), "中秋节"),
    (date(2026, 10, 1), date(2026, 10, 7), "国庆节"),
)
CALENDAR_SOURCE_DIR = RUNTIME_TRADING_CALENDAR_PATH.parent / "trading-calendar-sources"
SHANGHAI_ZONE = ZoneInfo("Asia/Shanghai")
MAX_ATTEMPTS = 3
RETRY_BUDGET_SECONDS = 60.0
OVERLAP_BUFFER_DAYS = 40
OVERLAP_TRADING_DAYS = 20
DRIFT_RATIO_THRESHOLD = 0.0035
DRIFT_MIN_RUN_LENGTH = 5
_CODE_RE = re.compile(r"^\d{6}$")
_PERMISSION_MARKERS = ("40203", "权限", "permission")
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "rate limit",
    "rate-limit",
    "too many requests",
    "429",
    "频率",
    "限流",
    "网络",
)


@dataclass(frozen=True)
class Args:
    backfill_days: int
    code: str | None
    calendar_only: bool = False


def parse_args(argv: Sequence[str] | None = None) -> Args:
    parser = argparse.ArgumentParser(description="Fetch TuShare QFQ bars and the total-return benchmark.")
    parser.add_argument(
        "--backfill-days", type=int, default=200, help="Calendar days to fetch (default: 200 ≈ 134 trading days)"
    )
    parser.add_argument("--code", default=None, help="Single 6-digit stock code (omit to fetch full watchlist)")
    parser.add_argument(
        "--calendar-only", action="store_true", help="Refresh local SSE calendar evidence without DB writes"
    )
    ns = parser.parse_args(argv)
    if ns.backfill_days <= 0:
        parser.error("--backfill-days must be positive")
    if ns.code is not None and not _CODE_RE.match(ns.code):
        parser.error("--code must be a 6-digit string")
    if ns.code is not None and ns.calendar_only:
        parser.error("--calendar-only cannot be combined with --code")
    return Args(ns.backfill_days, ns.code, ns.calendar_only)


def _is_permission_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _PERMISSION_MARKERS)


def _is_transient_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)


def _normalize_bars(frame: Any, code: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"expected pandas.DataFrame for {code}, got {type(frame).__name__}")
    if frame.empty:
        raise RuntimeError(f"no QFQ trading rows for {code}")

    required = {"trade_date", "open", "high", "low", "close", "vol"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(f"missing TuShare columns for {code}: {', '.join(missing)}")

    out = frame[["trade_date", "open", "high", "low", "close", "vol"]].rename(
        columns={"trade_date": "date", "vol": "volume"}
    )
    out = out.copy()
    parsed_dates = pd.to_datetime(out["date"], format="%Y%m%d", errors="coerce")
    out["date"] = parsed_dates.dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close", "volume"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out["volume"] = out["volume"] * 100
    out = out.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    if out.empty:
        raise RuntimeError(f"no valid QFQ trading rows for {code}")
    return out


def _call_pro_bar(api: Any, code: str, start_date: str, end_date: str, timeout: float) -> Any:
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put(
                (
                    True,
                    ts.pro_bar(
                        ts_code=to_tushare_stock_code(code),
                        start_date=start_date,
                        end_date=end_date,
                        adj="qfq",
                        api=api,
                    ),
                )
            )
        except Exception as exc:
            result_queue.put((False, exc))

    threading.Thread(target=worker, name=f"tushare-qfq-{code}", daemon=True).start()
    try:
        succeeded, result = result_queue.get(timeout=max(timeout, 0.001))
    except queue.Empty as exc:
        raise TimeoutError(f"TuShare pro_bar timed out for {code}") from exc
    if not succeeded:
        raise result
    return result


def _query_qfq(api: Any, code: str, start_date: str, end_date: str) -> pd.DataFrame:
    started = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            remaining_budget = RETRY_BUDGET_SECONDS - (time.monotonic() - started)
            if remaining_budget <= 0:
                raise TimeoutError(f"retry budget exhausted for {code}")
            frame = _call_pro_bar(api, code, start_date, end_date, remaining_budget)
            return _normalize_bars(frame, code)
        except Exception as exc:
            if _is_permission_error(exc):
                logger.error("QFQ_TUSHARE_PERMISSION_DENIED code=%s detail=%s", code, exc)
                raise
            if not _is_transient_error(exc) or attempt == MAX_ATTEMPTS:
                raise

            delay = min(2 ** (attempt - 1) + random.uniform(0.0, 1.0), RETRY_BUDGET_SECONDS)
            elapsed = time.monotonic() - started
            if elapsed + delay > RETRY_BUDGET_SECONDS:
                raise TimeoutError(f"retry budget exhausted for {code} after {elapsed:.1f}s") from exc
            logger.warning(
                "QFQ_TUSHARE_RETRY code=%s attempt=%d/%d delay=%.2fs detail=%s",
                code,
                attempt + 1,
                MAX_ATTEMPTS,
                delay,
                exc,
            )
            time.sleep(delay)

    raise AssertionError("unreachable")


def _partition_date_bounds(conn: sqlite3.Connection, code: str) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT MIN(trade_date), MAX(trade_date) FROM daily_bars WHERE code = ? AND adjusted = ?",
        (code, PRODUCTION_ADJUSTED),
    ).fetchone()
    return row[0], row[1]


def _partition_dates(conn: sqlite3.Connection, code: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT trade_date FROM daily_bars WHERE code = ? AND adjusted = ?",
            (code, PRODUCTION_ADJUSTED),
        )
    }


def _detect_drift(
    conn: sqlite3.Connection,
    code: str,
    frame: pd.DataFrame,
    existing_max_date: str,
) -> tuple[str, str, int] | None:
    overlap = frame.loc[frame["date"] <= existing_max_date, ["date", "close"]]
    if overlap.empty:
        return None

    new_closes = {str(row.date): float(row.close) for row in overlap.itertuples(index=False)}
    placeholders = ",".join("?" for _ in new_closes)
    rows = conn.execute(
        f"SELECT trade_date, close FROM daily_bars "
        f"WHERE code = ? AND adjusted = ? AND trade_date IN ({placeholders}) "
        "ORDER BY trade_date DESC LIMIT ?",
        (code, PRODUCTION_ADJUSTED, *new_closes, OVERLAP_TRADING_DAYS),
    ).fetchall()

    ratios: list[tuple[str, float | None]] = []
    for trade_date, existing_close in reversed(rows):
        if existing_close is None or existing_close == 0:
            logger.warning(
                "QFQ_TUSHARE_DRIFT_SKIP code=%s trade_date=%s existing_close=%s",
                code,
                trade_date,
                existing_close,
            )
            ratios.append((trade_date, None))
            continue
        ratio_diff = abs(new_closes[trade_date] - existing_close) / abs(existing_close)
        if not math.isfinite(ratio_diff):
            logger.warning(
                "QFQ_TUSHARE_DRIFT_SKIP code=%s trade_date=%s reason=non_finite_ratio",
                code,
                trade_date,
            )
            ratios.append((trade_date, None))
            continue
        ratios.append((trade_date, ratio_diff))

    best_run: tuple[str, str, int] | None = None
    for start_index, (run_start, first_ratio) in enumerate(ratios):
        if first_ratio is None or first_ratio <= DRIFT_RATIO_THRESHOLD:
            continue
        run_min = first_ratio
        run_max = first_ratio
        for end_index in range(start_index, len(ratios)):
            run_end, ratio = ratios[end_index]
            if ratio is None or ratio <= DRIFT_RATIO_THRESHOLD:
                break
            run_min = min(run_min, ratio)
            run_max = max(run_max, ratio)
            if run_max - run_min > DRIFT_RATIO_THRESHOLD:
                break
            run_length = end_index - start_index + 1
            if best_run is None or run_length > best_run[2]:
                best_run = (run_start, run_end, run_length)

    if best_run is not None and best_run[2] >= DRIFT_MIN_RUN_LENGTH:
        return best_run
    return None


def _upsert_frame(conn: sqlite3.Connection, code: str, frame: pd.DataFrame) -> int:
    return upsert_daily_bars(
        conn,
        code,
        frame,
        source=SOURCE,
        adjusted=PRODUCTION_ADJUSTED,
        volume_unit="share",
    )


def _fetch_one(conn: sqlite3.Connection, api: Any, code: str, start_date: str, end_date: str) -> tuple[int, str]:
    existing_min_date, existing_max_date = _partition_date_bounds(conn, code)
    fetch_start = start_date
    if existing_max_date is not None:
        fetch_start = (date.fromisoformat(existing_max_date) - timedelta(days=OVERLAP_BUFFER_DAYS)).strftime("%Y%m%d")

    frame = _query_qfq(api, code, fetch_start, end_date)
    latest_date = str(frame["date"].max())
    if existing_max_date is None:
        with conn:
            count = _upsert_frame(conn, code, frame)
        return count, latest_date

    drift_run = _detect_drift(conn, code, frame, existing_max_date)
    if drift_run is None:
        incremental = frame.loc[frame["date"] > existing_max_date]
        with conn:
            count = _upsert_frame(conn, code, incremental)
        return count, latest_date

    run_start, run_end, run_length = drift_run
    logger.warning(
        "QFQ_TUSHARE_DRIFT_DETECTED code=%s run_start=%s run_end=%s run_length=%d",
        code,
        run_start,
        run_end,
        run_length,
    )
    baseline_dates = _partition_dates(conn, code)
    assert existing_min_date is not None
    reprocessed = _query_qfq(api, code, existing_min_date.replace("-", ""), end_date)
    reprocessed_dates = set(reprocessed["date"].astype(str))
    missing_dates = sorted(baseline_dates - reprocessed_dates)
    if missing_dates:
        logger.error(
            "QFQ_TUSHARE_REPROCESS_INCOMPLETE code=%s missing_dates=%s",
            code,
            missing_dates,
        )
        raise RuntimeError(f"full reprocess missing existing dates: {', '.join(missing_dates)}")

    with conn:
        count = _upsert_frame(conn, code, reprocessed)
    logger.info("QFQ_TUSHARE_REPROCESS_OK code=%s rows=%d", code, count)
    return count, str(reprocessed["date"].max())


def _collect_codes(
    conn: sqlite3.Connection,
    api: Any,
    codes: Sequence[str],
    start_date: str,
    end_date: str,
) -> tuple[dict[str, str], list[str]]:
    failures: dict[str, str] = {}
    latest_dates: list[str] = []
    for code in codes:
        try:
            count, latest_date = _fetch_one(conn, api, code, start_date, end_date)
            latest_dates.append(latest_date)
            logger.info("QFQ_TUSHARE_FETCH_OK code=%s rows=%d latest=%s", code, count, latest_date)
        except Exception as exc:  # broad catch: per-code fault isolation
            conn.rollback()
            failures[code] = f"{type(exc).__name__}: {exc}"
            logger.error("QFQ_TUSHARE_CODE_FAILED code=%s detail=%s", code, failures[code])
            if _is_permission_error(exc):
                break
    return failures, latest_dates


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    rollback = path.with_name(f".{path.name}.{os.getpid()}.rollback")
    previous = path.read_bytes() if path.is_file() else None
    replaced = False
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if replaced:
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    with rollback.open("wb") as handle:
                        handle.write(previous)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(rollback, path)
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except Exception as rollback_error:
                raise RuntimeError(f"atomic write failed and rollback failed: {path}") from rollback_error
        raise
    finally:
        temporary.unlink(missing_ok=True)
        rollback.unlink(missing_ok=True)


def _calendar_seed_start(path: Path) -> date:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != {"dates", "covered_from", "covered_to", "as_of", "source"}:
            raise ValueError("invalid shape")
        if not isinstance(payload["dates"], list) or not isinstance(payload["source"], str) or not payload["source"]:
            raise ValueError("invalid metadata")
        return date.fromisoformat(payload["covered_from"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid tracked calendar seed: {path}") from exc


def _compact_calendar_date(value: Any, field: str) -> date:
    text = str(value)
    if len(text) != 8 or not text.isdigit():
        raise RuntimeError(f"invalid {field}: {value}")
    try:
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    except ValueError as exc:
        raise RuntimeError(f"invalid {field}: {value}") from exc


def _refresh_trading_calendar(
    api: Any,
    as_of: date,
    *,
    tracked_path: Path = TRACKED_TRADING_CALENDAR_PATH,
    runtime_path: Path = RUNTIME_TRADING_CALENDAR_PATH,
    source_dir: Path = CALENDAR_SOURCE_DIR,
) -> Path:
    covered_from = _calendar_seed_start(tracked_path)
    if as_of < covered_from:
        raise RuntimeError("calendar as_of precedes tracked coverage start")
    frame = api.trade_cal(
        exchange="SSE",
        start_date=covered_from.strftime("%Y%m%d"),
        end_date=as_of.strftime("%Y%m%d"),
        fields="exchange,cal_date,is_open,pretrade_date",
    )
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise RuntimeError("no SSE trading-calendar rows")
    missing = {"cal_date", "is_open"} - set(frame.columns)
    if missing:
        raise RuntimeError(f"missing SSE trading-calendar columns: {', '.join(sorted(missing))}")

    rows: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        exchange = str(raw.get("exchange") or "")
        if exchange not in {"", "SSE"}:
            raise RuntimeError(f"unexpected trading-calendar exchange: {exchange}")
        calendar_date = _compact_calendar_date(raw.get("cal_date"), "cal_date")
        raw_is_open = raw.get("is_open")
        if isinstance(raw_is_open, bool) or raw_is_open not in (0, 1):
            raise RuntimeError(f"invalid is_open for {calendar_date}: {raw_is_open}")
        raw_pretrade = raw.get("pretrade_date")
        pretrade_date = None
        if raw_pretrade is not None and not pd.isna(raw_pretrade) and str(raw_pretrade):
            pretrade_date = _compact_calendar_date(raw_pretrade, "pretrade_date").isoformat()
        rows.append(
            {
                "exchange": "SSE",
                "cal_date": calendar_date.isoformat(),
                "is_open": int(raw_is_open),
                "pretrade_date": pretrade_date,
            }
        )
    rows.sort(key=lambda item: item["cal_date"])
    observed_dates = [date.fromisoformat(item["cal_date"]) for item in rows]
    if len(observed_dates) != len(set(observed_dates)):
        raise RuntimeError("duplicate SSE trading-calendar dates")
    expected_dates = [covered_from + timedelta(days=offset) for offset in range((as_of - covered_from).days + 1)]
    if observed_dates != expected_dates:
        raise RuntimeError("SSE trading calendar does not cover every natural date in the requested range")
    open_dates = [date.fromisoformat(item["cal_date"]) for item in rows if item["is_open"] == 1]
    open_set = set(open_dates)
    weekend_open = [item.isoformat() for item in open_dates if item.weekday() >= 5]
    if weekend_open:
        raise RuntimeError(f"SSE calendar marks weekend open: {','.join(weekend_open)}")
    closure_conflicts: list[str] = []
    for closure_start, closure_end, label in CALENDAR_OFFICIAL_CLOSURES:
        current = max(closure_start, covered_from)
        last = min(closure_end, as_of)
        while current <= last:
            if current in open_set:
                closure_conflicts.append(f"{label}:{current.isoformat()}:marked_open")
            current += timedelta(days=1)
    if closure_conflicts:
        raise RuntimeError(f"SSE calendar conflicts with {CALENDAR_OFFICIAL_REFERENCE}: {','.join(closure_conflicts)}")

    official_range_start = max(covered_from, date(2026, 1, 1))
    official_range_end = min(as_of, date(2026, 12, 31))
    official_cross_check = (
        {
            "notice": CALENDAR_OFFICIAL_REFERENCE,
            "covered_from": official_range_start.isoformat(),
            "covered_to": official_range_end.isoformat(),
            "closure_conflicts": [],
        }
        if official_range_start <= official_range_end
        else None
    )
    fetched_at = datetime.now(SHANGHAI_ZONE).isoformat()
    raw_payload = {
        "schema_version": 1,
        "source": CALENDAR_SOURCE,
        "exchange": "SSE",
        "request": {"start_date": covered_from.isoformat(), "end_date": as_of.isoformat()},
        "fetched_at": fetched_at,
        "official_cross_check": official_cross_check,
        "rows": rows,
    }
    raw_bytes = (json.dumps(raw_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    raw_path = source_dir / f"{raw_hash}.json"
    if raw_path.exists() and raw_path.read_bytes() != raw_bytes:
        raise RuntimeError(f"calendar source hash collision: {raw_hash}")
    if not raw_path.exists():
        _atomic_write_bytes(raw_path, raw_bytes)
    try:
        raw_reference = raw_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        raw_reference = str(raw_path)
    official_source = (
        f";official_cross_check={CALENDAR_OFFICIAL_REFERENCE};"
        f"official_cross_check_range={official_range_start.isoformat()}..{official_range_end.isoformat()}"
        if official_cross_check is not None
        else ""
    )
    calendar_payload = {
        "dates": [item.isoformat() for item in open_dates],
        "covered_from": covered_from.isoformat(),
        "covered_to": as_of.isoformat(),
        "as_of": as_of.isoformat(),
        "source": (
            f"tushare.trade_cal:SSE;raw_path={raw_reference};raw_sha256={raw_hash};"
            f"fetched_at={fetched_at}{official_source}"
        ),
    }
    calendar_bytes = (json.dumps(calendar_payload, ensure_ascii=False, indent=2) + "\n").encode()
    _atomic_write_bytes(runtime_path, calendar_bytes)
    logger.info(
        "TRADING_CALENDAR_REFRESH_OK as_of=%s open_dates=%d raw_sha256=%s",
        as_of.isoformat(),
        len(open_dates),
        raw_hash,
    )
    return runtime_path


def _create_api() -> Any:
    token = os.environ.get("TUSHARE_TOKEN") or read_tushare_token()
    return ts.pro_api(token) if token else ts.pro_api()


def _fetch_total_return_index(
    conn: sqlite3.Connection,
    api: Any,
    start_date: str,
    end_date: str,
) -> tuple[int, str]:
    frame = api.index_daily(
        ts_code=BENCHMARK_API_SYMBOL,
        start_date=start_date,
        end_date=end_date,
    )
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise RuntimeError("no CSI 300 total-return rows")
    missing = {"trade_date", "close"} - set(frame.columns)
    if missing:
        raise RuntimeError(f"missing CSI 300 total-return columns: {', '.join(sorted(missing))}")

    rows: list[tuple[str, float]] = []
    for raw_date, raw_close in frame[["trade_date", "close"]].itertuples(index=False, name=None):
        trade_date = pd.to_datetime(str(raw_date), format="%Y%m%d", errors="coerce")
        try:
            close = float(raw_close)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid CSI 300 total-return close on {raw_date}") from exc
        if pd.isna(trade_date) or not math.isfinite(close) or close <= 0:
            raise RuntimeError(f"invalid CSI 300 total-return row on {raw_date}")
        rows.append((trade_date.strftime("%Y-%m-%d"), close))
    if len({trade_date for trade_date, _close in rows}) != len(rows):
        raise RuntimeError("duplicate CSI 300 total-return dates")

    with conn:
        conn.executemany(
            """INSERT INTO index_prices(symbol,date,close) VALUES (?,?,?)
               ON CONFLICT(symbol,date) DO UPDATE SET close=excluded.close""",
            ((BENCHMARK_DB_SYMBOL, trade_date, close) for trade_date, close in rows),
        )
    return len(rows), max(trade_date for trade_date, _close in rows)


def run(args: Args) -> int:
    end = datetime.now(SHANGHAI_ZONE).date()
    start = end - timedelta(days=args.backfill_days)
    codes = [args.code] if args.code else [item["code"] for item in WATCHLIST]

    try:
        api = _create_api()
    except Exception as exc:
        logger.error("QFQ_TUSHARE_CLIENT_FAILED detail=%s", exc)
        print(f"QFQ_TUSHARE_BATCH_FAILED: client initialization: {exc}", file=sys.stderr)
        for code in codes:
            print(f"  {code}: client initialization failed", file=sys.stderr)
        return 1

    if args.code is None or args.calendar_only:
        try:
            _refresh_trading_calendar(api, end)
        except Exception as exc:
            logger.error("TRADING_CALENDAR_REFRESH_FAILED as_of=%s detail=%s", end.isoformat(), exc)
            print(f"TRADING_CALENDAR_REFRESH_FAILED: {exc}", file=sys.stderr)
            return 1
        if args.calendar_only:
            return 0

    benchmark_failure: str | None = None
    with sqlite3.connect(DB_PATH) as conn:
        try:
            benchmark_count, benchmark_latest = _fetch_total_return_index(
                conn,
                api,
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
            )
            logger.info(
                "QFQ_BENCHMARK_FETCH_OK symbol=%s rows=%d latest=%s source=%s",
                BENCHMARK_DB_SYMBOL,
                benchmark_count,
                benchmark_latest,
                BENCHMARK_SOURCE,
            )
        except Exception as exc:
            conn.rollback()
            benchmark_failure = f"{type(exc).__name__}: {exc}"
            logger.error("QFQ_BENCHMARK_FAILED symbol=%s detail=%s", BENCHMARK_DB_SYMBOL, benchmark_failure)
        failures, latest_dates = _collect_codes(
            conn,
            api,
            codes,
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
        )

    if args.code is None and latest_dates and max(latest_dates) != end.isoformat():
        logger.warning(
            "QFQ_TUSHARE_FRESHNESS_WARNING expected_today=%s latest_returned=%s; same-day publication may be delayed",
            end.isoformat(),
            max(latest_dates),
        )

    if failures or benchmark_failure:
        print("QFQ_TUSHARE_BATCH_FAILED:", file=sys.stderr)
        if benchmark_failure:
            print(f"  {BENCHMARK_DB_SYMBOL}: {benchmark_failure}", file=sys.stderr)
        for code, reason in failures.items():
            print(f"  {code}: {reason}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
