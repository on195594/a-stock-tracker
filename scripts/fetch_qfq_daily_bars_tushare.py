"""Fetch TuShare QFQ bars and the CSI 300 total-return benchmark."""

from __future__ import annotations

import argparse
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
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import tushare as ts

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_lib.providers.tushare_fundamentals import read_tushare_token
from a_stock_lib.providers.tushare_quotes import to_tushare_stock_code
from a_stock_tracker.config import WATCHLIST
from a_stock_tracker.data.cache import DB_PATH, upsert_daily_bars

logger = logging.getLogger(__name__)

PRODUCTION_ADJUSTED = "qfq"
SOURCE = "tushare.pro_bar.qfq"
BENCHMARK_SOURCE = "tushare.index_daily"
BENCHMARK_API_SYMBOL = "H00300.CSI"
BENCHMARK_DB_SYMBOL = "H00300"
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


def parse_args(argv: Sequence[str] | None = None) -> Args:
    parser = argparse.ArgumentParser(description="Fetch TuShare QFQ bars and the total-return benchmark.")
    parser.add_argument(
        "--backfill-days", type=int, default=200, help="Calendar days to fetch (default: 200 ≈ 134 trading days)"
    )
    parser.add_argument("--code", default=None, help="Single 6-digit stock code (omit to fetch full watchlist)")
    ns = parser.parse_args(argv)
    if ns.backfill_days <= 0:
        parser.error("--backfill-days must be positive")
    if ns.code is not None and not _CODE_RE.match(ns.code):
        parser.error("--code must be a 6-digit string")
    return Args(ns.backfill_days, ns.code)


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
    end = date.today()
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
