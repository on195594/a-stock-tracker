#!/usr/bin/env python3
"""Collect resumable BaoStock qfq daily files for L3 v2 backtests."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import baostock as bs
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.l3_v2_qfq_cache import (  # noqa: E402
    CacheInvalidError,
    CacheMissingError,
    CollectorLock,
    LockUnavailableError,
    QfqCacheError,
    CSV_COLUMNS,
    read_cache,
    validate_code,
    validate_cache_frame,
    write_cache,
)
from a_stock_lib.providers.baostock_quotes import to_baostock_stock_code  # noqa: E402


MIN_PRELOAD_TRADING_DAYS = 120


class CollectorError(RuntimeError):
    """Base error for collector failures."""


def _to_exchange_code(code: str) -> str:
    if code.startswith("6"):
        suffix = "SH"
    elif code.startswith(("4", "8")):
        suffix = "BJ"
    else:
        suffix = "SZ"
    return f"{code}.{suffix}"


@dataclass(frozen=True)
class CollectorConfig:
    db_path: Path
    start: date
    end: date
    codes: tuple[str, ...] | None
    cache_dir: Path
    preload_trading_days: int = MIN_PRELOAD_TRADING_DAYS


def parse_date(value: str) -> date:
    """Parse an ISO CLI date."""
    return date.fromisoformat(value)


def parse_codes(value: str | None) -> tuple[str, ...] | None:
    """Parse and validate a comma-separated stock subset."""
    if not value:
        return None
    codes = tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    for code in codes:
        validate_code(code)
    return codes or None


def parse_args(argv: list[str] | None = None) -> CollectorConfig:
    """Build collector configuration from CLI arguments."""
    parser = argparse.ArgumentParser(description="Collect the L3 v2 qfq file cache safely.")
    parser.add_argument("--db", default="tracker.db", type=Path, help="Path to tracker.db.")
    parser.add_argument("--start", required=True, type=parse_date, help="Score window start, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, type=parse_date, help="Inclusive window end, YYYY-MM-DD.")
    parser.add_argument("--codes", help="Optional comma-separated six-digit stock codes.")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/qfq_cache"))
    parser.add_argument("--preload-trading-days", type=int, default=MIN_PRELOAD_TRADING_DAYS)
    args = parser.parse_args(argv)
    if args.start > args.end:
        parser.error("--start must be <= --end")
    if args.preload_trading_days < MIN_PRELOAD_TRADING_DAYS:
        parser.error(f"--preload-trading-days must be >= {MIN_PRELOAD_TRADING_DAYS}")
    try:
        codes = parse_codes(args.codes)
    except CacheInvalidError as exc:
        parser.error(f"--codes contains an invalid code: {exc}")
    return CollectorConfig(args.db, args.start, args.end, codes, args.cache_dir, args.preload_trading_days)


def open_readonly_db(path: Path) -> sqlite3.Connection:
    """Open tracker.db through SQLite's immutable read-only access boundary."""
    absolute = path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{quote(str(absolute))}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
        conn.close()
        raise CollectorError("SQLITE_QUERY_ONLY_FAILED")
    return conn


def compute_preload_start(conn: sqlite3.Connection, start: date, preload_days: int) -> date:
    """Resolve a trading-day preload boundary from local read-only bars."""
    rows = conn.execute(
        """SELECT DISTINCT trade_date FROM daily_bars
           WHERE adjusted='none' AND trade_date <= ?
           ORDER BY trade_date DESC LIMIT ?""",
        (start.isoformat(), preload_days + 1),
    ).fetchall()
    return date.fromisoformat(rows[-1]["trade_date"]) if rows else start


def load_codes(conn: sqlite3.Connection, config: CollectorConfig) -> tuple[str, ...]:
    """Load the target prediction universe without mutating tracker.db."""
    if config.codes is not None:
        return tuple(sorted(config.codes))
    rows = conn.execute(
        """SELECT DISTINCT code FROM predictions
           WHERE framework='A' AND score_date BETWEEN ? AND ? ORDER BY code""",
        (config.start.isoformat(), config.end.isoformat()),
    ).fetchall()
    codes = tuple(str(row["code"]) for row in rows)
    for code in codes:
        validate_code(code)
    return codes


def fetch_baostock_frame(client: Any, code: str, start: str, end: str) -> pd.DataFrame:
    """Fetch qfq rows from BaoStock and normalize them to the cache schema."""
    fields = "date,open,high,low,close,volume,tradestatus"
    try:
        result = client.query_history_k_data_plus(
            to_baostock_stock_code(code),
            fields,
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="2",
        )
    except CollectorError:
        raise
    except Exception as exc:
        raise CollectorError(f"BAOSTOCK_QUERY_EXCEPTION: {exc}") from exc
    if result.error_code != "0":
        raise CollectorError(f"BaoStock query failed: {result.error_msg}")

    rows: list[list[str]] = []
    try:
        while result.next():
            rows.append(result.get_row_data())
    except CollectorError:
        raise
    except Exception as exc:
        raise CollectorError(f"BAOSTOCK_ITERATION_EXCEPTION: {exc}") from exc
    if not rows:
        raise CacheInvalidError("EMPTY_DATA")

    frame = pd.DataFrame(rows, columns=fields.split(","))
    frame = frame[frame["tradestatus"] == "1"].copy()
    numeric = ["open", "high", "low", "close", "volume"]
    frame[numeric] = frame[numeric].replace("", pd.NA)
    frame = frame.dropna(subset=numeric)
    if frame.empty:
        raise CacheInvalidError("EMPTY_DATA")
    for column in numeric:
        frame[column] = frame[column].astype(float)

    frame["volume"] = frame["volume"] / 100.0
    frame = frame.rename(columns={"date": "trade_date", "volume": "vol"})
    frame["trade_date"] = frame["trade_date"].str.replace("-", "", regex=False)
    frame["code"] = code
    frame["ts_code"] = _to_exchange_code(code)
    frame["adj_factor"] = 1.0
    frame["source"] = "baostock"
    frame["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return validate_cache_frame(frame[list(CSV_COLUMNS)], expected_code=code)


def collect(
    config: CollectorConfig,
    client: Any = bs,
    output: Callable[[str], None] = print,
) -> int:
    """Collect pending stocks once each through a shared BaoStock session."""
    conn = open_readonly_db(config.db_path)
    try:
        preload_start = compute_preload_start(conn, config.start, config.preload_trading_days)
        codes = load_codes(conn, config)
    finally:
        conn.close()
    start_s = preload_start.isoformat()
    end_s = config.end.isoformat()

    logged_in = False
    try:
        try:
            lock = CollectorLock(config.cache_dir)
            with lock:
                for code in codes:
                    try:
                        read_cache(config.cache_dir, code, preload_start, config.end)
                    except (CacheMissingError, CacheInvalidError):
                        pass
                    else:
                        output(f"SKIP cached complete: {code}")
                        continue

                    if not logged_in:
                        try:
                            login_result = client.login()
                        except CollectorError:
                            raise
                        except Exception as exc:
                            raise CollectorError(f"BAOSTOCK_LOGIN_EXCEPTION: {exc}") from exc
                        if login_result.error_code != "0":
                            raise CollectorError(f"BaoStock login failed: {login_result.error_msg}")
                        logged_in = True
                    try:
                        frame = fetch_baostock_frame(client, code, start_s, end_s)
                        write_cache(config.cache_dir, code, frame, preload_start, config.end)
                    except (QfqCacheError, OSError, CollectorError, ValueError) as exc:
                        output(f"{code}: BAOSTOCK_FETCH_OR_CACHE_WRITE_FAILED:{exc}")
                        return 1
                    output(f"CACHED complete: {code}")
        finally:
            if logged_in:
                try:
                    client.logout()
                except CollectorError:
                    raise
                except Exception as exc:
                    raise CollectorError(f"BAOSTOCK_LOGOUT_EXCEPTION: {exc}") from exc
    except LockUnavailableError as exc:
        output(str(exc))
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    config = parse_args(argv)
    try:
        return collect(config)
    except CollectorError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
