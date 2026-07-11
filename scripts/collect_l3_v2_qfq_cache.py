#!/usr/bin/env python3
"""Collect resumable raw daily and adj_factor files for L3 v2 backtests."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.l3_v2_qfq_cache import (  # noqa: E402
    CacheInvalidError,
    CacheMissingError,
    CollectorLock,
    FixedIntervalRateLimiter,
    LockUnavailableError,
    QfqCacheError,
    build_cache_frame,
    read_cache,
    to_tushare_code,
    validate_code,
    write_cache,
)


MIN_PRELOAD_TRADING_DAYS = 120


class CollectorError(RuntimeError):
    """Base error for collector failures."""


class TushareRateLimitError(CollectorError):
    """Raised when Tushare rejects an adj_factor request for quota."""


class TushareFetchError(CollectorError):
    """Raised when a remote fetch fails for another reason."""


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


def is_rate_limit(exc: Exception) -> bool:
    """Recognize Tushare's Chinese and English quota messages."""
    message = str(exc).lower()
    return "频率超限" in message or "rate limit" in message or "每分钟" in message


def collect(
    config: CollectorConfig,
    pro: Any,
    output: Callable[[str], None] = print,
    limiter: FixedIntervalRateLimiter | None = None,
) -> int:
    """Collect pending stocks once each, stopping immediately on rate limit."""
    limiter = limiter or FixedIntervalRateLimiter(config.cache_dir)
    conn = open_readonly_db(config.db_path)
    try:
        preload_start = compute_preload_start(conn, config.start, config.preload_trading_days)
        codes = load_codes(conn, config)
    finally:
        conn.close()
    start_s = preload_start.strftime("%Y%m%d")
    end_s = config.end.strftime("%Y%m%d")

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

                ts_code = to_tushare_code(code)
                try:
                    daily = pro.daily(ts_code=ts_code, start_date=start_s, end_date=end_s)
                    limiter.acquire()
                    factors = pro.adj_factor(ts_code=ts_code, start_date=start_s, end_date=end_s)
                except Exception as exc:
                    if is_rate_limit(exc):
                        output(f"{code}: TUSHARE_RATE_LIMIT; recoverable, rerun to resume from completed cache")
                        return 2
                    output(f"{code}: TUSHARE_FETCH_FAILED:{str(exc).replace(chr(10), ' ').strip()}")
                    return 1
                try:
                    frame = build_cache_frame(code, daily, factors)
                    write_cache(config.cache_dir, code, frame, preload_start, config.end)
                except (QfqCacheError, OSError) as exc:
                    output(f"{code}: CACHE_WRITE_OR_VALIDATION_FAILED:{exc}")
                    return 1
                output(f"CACHED complete: {code}")
    except LockUnavailableError as exc:
        output(str(exc))
        return 3
    return 0


def build_tushare_client() -> Any:
    """Load .env without override and create an authenticated Tushare client."""
    load_project_dotenv(PROJECT_ROOT / ".env")
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise CollectorError("TUSHARE_TOKEN_MISSING")
    try:
        import tushare as ts  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CollectorError(f"TUSHARE_IMPORT_FAILED:{exc}") from exc
    return ts.pro_api(token)


def load_project_dotenv(path: Path) -> None:
    """Use python-dotenv, with a minimal compatibility path for bootstrap environments."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        if not path.is_file():
            return
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return
    load_dotenv(path, override=False)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    config = parse_args(argv)
    try:
        pro = build_tushare_client()
        return collect(config, pro)
    except CollectorError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
