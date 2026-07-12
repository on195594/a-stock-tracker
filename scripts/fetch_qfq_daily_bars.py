"""Fetch BaoStock QFQ (前复权) daily bars and upsert into daily_bars (adjusted='qfq')."""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

import baostock as bs
import pandas as pd

from a_stock_lib.providers.baostock_quotes import to_baostock_stock_code
from config import WATCHLIST
from lib.cache import DB_PATH, upsert_daily_bars

logger = logging.getLogger(__name__)

_FIELDS = "date,code,open,high,low,close,volume,amount,adjustflag,tradestatus"
_CODE_RE = re.compile(r"^\d{6}$")


class QfqFetchError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class Args:
    backfill_days: int
    code: str | None


def parse_args(argv: Sequence[str] | None = None) -> Args:
    parser = argparse.ArgumentParser(
        description="Fetch BaoStock QFQ daily bars for watchlist codes."
    )
    parser.add_argument(
        "--backfill-days", type=int, default=200,
        help="Calendar days to fetch (default: 200 ≈ 134 trading days)"
    )
    parser.add_argument(
        "--code", default=None,
        help="Single 6-digit stock code (omit to fetch full watchlist)"
    )
    ns = parser.parse_args(argv)
    if ns.backfill_days <= 0:
        parser.error("--backfill-days must be positive")
    if ns.code is not None and not _CODE_RE.match(ns.code):
        parser.error("--code must be a 6-digit string")
    return Args(ns.backfill_days, ns.code)


def _query_qfq(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    rs = bs.query_history_k_data_plus(
        to_baostock_stock_code(code),
        _FIELDS,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="2",  # QFQ = 前复权
    )
    if rs.error_code != "0":
        raise QfqFetchError("QFQ_QUERY_FAILED", rs.error_msg)
    rows: list[list[str]] = []
    while rs.next():
        row = rs.get_row_data()
        # tradestatus=="1" means normal trading day; skip suspended days
        if row[-1] == "1" and row[5]:
            rows.append(row)
    if not rows:
        raise QfqFetchError("QFQ_EMPTY_RESULT", f"no trading rows for {code}")
    frame = pd.DataFrame(rows, columns=_FIELDS.split(","))
    cols = ["date", "open", "high", "low", "close", "volume"]
    out = frame[cols].copy()
    for col in cols[1:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["date", "close"])


def _fetch_one(conn: sqlite3.Connection, code: str, start_date: str, end_date: str) -> int:
    frame = _query_qfq(code, start_date, end_date)
    count = upsert_daily_bars(
        conn, code, frame,
        source="baostock.qfq",
        adjusted="qfq",
        volume_unit="share",
    )
    conn.commit()
    return count


def _collect_codes(
    conn: sqlite3.Connection,
    codes: Sequence[str],
    start_date: str,
    end_date: str,
) -> None:
    for code in codes:
        try:
            count = _fetch_one(conn, code, start_date, end_date)
            logger.info("QFQ_FETCH_OK code=%s rows=%d", code, count)
        except Exception as exc:  # broad catch: per-code fault isolation
            conn.rollback()
            error_code = getattr(exc, "error_code", "QFQ_CODE_FAILED")
            logger.warning("%s code=%s detail=%s", error_code, code, exc)


def run(args: Args) -> int:
    end = date.today()
    start = end - timedelta(days=args.backfill_days)
    codes = [args.code] if args.code else [item["code"] for item in WATCHLIST]
    login = bs.login()
    if login.error_code != "0":
        logger.error("QFQ_LOGIN_FAILED detail=%s", login.error_msg)
        return 1
    try:
        with sqlite3.connect(DB_PATH) as conn:
            _collect_codes(conn, codes, start.isoformat(), end.isoformat())
    finally:
        bs.logout()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
