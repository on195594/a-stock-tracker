"""Fetch TuShare QFQ daily bars into an isolated shadow partition."""

from __future__ import annotations

import argparse
import logging
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

from a_stock_lib.providers.tushare_quotes import to_tushare_stock_code
from a_stock_tracker.config import WATCHLIST
from a_stock_tracker.data.cache import DB_PATH, upsert_daily_bars

logger = logging.getLogger(__name__)

SHADOW_ADJUSTED = "qfq_tushare_shadow"
SOURCE = "tushare.pro_bar.qfq"
MAX_ATTEMPTS = 3
RETRY_BUDGET_SECONDS = 60.0
_CODE_RE = re.compile(r"^\d{6}$")
_PERMISSION_MARKERS = ("40203", "权限", "permission")
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "rate limit",
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
    parser = argparse.ArgumentParser(description="Fetch TuShare QFQ shadow daily bars for watchlist codes.")
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


def _fetch_one(conn: sqlite3.Connection, api: Any, code: str, start_date: str, end_date: str) -> tuple[int, str]:
    frame = _query_qfq(api, code, start_date, end_date)
    count = upsert_daily_bars(
        conn,
        code,
        frame,
        source=SOURCE,
        adjusted=SHADOW_ADJUSTED,
        volume_unit="share",
    )
    conn.commit()
    return count, str(frame["date"].max())


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
    return failures, latest_dates


def _create_api() -> Any:
    token = os.environ.get("TUSHARE_TOKEN")
    return ts.pro_api(token) if token else ts.pro_api()


def run(args: Args) -> int:
    end = date.today()
    start = end - timedelta(days=args.backfill_days)
    codes = [args.code] if args.code else [item["code"] for item in WATCHLIST]

    try:
        api = _create_api()
    except Exception as exc:
        logger.error("QFQ_TUSHARE_CLIENT_FAILED detail=%s", exc)
        print(f"QFQ_TUSHARE_BATCH_FAILED: client initialization: {exc}", file=sys.stderr)
        return 1

    with sqlite3.connect(DB_PATH) as conn:
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

    if failures:
        print("QFQ_TUSHARE_BATCH_FAILED:", file=sys.stderr)
        for code, reason in failures.items():
            print(f"  {code}: {reason}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
