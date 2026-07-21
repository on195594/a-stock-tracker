"""Readiness gates for TuShare primary-source shadow data."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from a_stock_tracker.config import WATCHLIST
from a_stock_tracker.data.tushare_primary_batch import request_fingerprint

SCOPES = ("valuation", "financial", "dividend", "all")


def _compact(value: Any) -> str:
    return str(value or "").replace("-", "")[:8]


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_observation(
    conn: sqlite3.Connection,
    table: str,
    code: str,
    order_column: str,
    as_of: str,
    endpoint: str | None = None,
) -> sqlite3.Row | None:
    endpoint_clause = " AND o.endpoint=?" if endpoint else ""
    query = f"""SELECT o.*, MAX(e.observed_at) AS observed_at,
        MAX(r.source_as_of) AS run_source_as_of
        FROM {table} o
        JOIN observation_events e ON e.record_key=o.record_key
        JOIN ingestion_runs r ON r.run_id=e.run_id AND r.status='completed'
        WHERE o.code=?{endpoint_clause} GROUP BY o.record_key
        ORDER BY REPLACE(o.{order_column}, '-', '') DESC, observed_at DESC"""
    cutoff = _compact(as_of)
    params = (code, endpoint) if endpoint else (code,)
    for row in conn.execute(query, params).fetchall():
        if _compact(row[order_column]) <= cutoff:
            return row
    return None


def _valuation_detail(conn: sqlite3.Connection, code: str, as_of: str) -> dict[str, Any]:
    row = _latest_observation(conn, "valuation_observations", code, "trade_date", as_of)
    if row is None:
        return {"status": "HOLD", "reasons": ["MISSING_VALUATION_OBSERVATION"]}
    reasons = [name for name in ("close", "pb", "total_mv", "circ_mv") if not _positive(row[name])]
    detail: dict[str, Any] = {"status": "READY" if not reasons else "HOLD", "reasons": reasons}
    if _compact(row["source_as_of"]) != _compact(as_of):
        reasons.append("STALE_VALUATION_DATE")
        detail["status"] = "HOLD"
    detail["pe_ttm_status"] = "READY" if _positive(row["pe_ttm"]) else "NOT_APPLICABLE_LOSS"
    detail["source_as_of"] = row["source_as_of"]
    return detail


def _financial_detail(conn: sqlite3.Connection, code: str, as_of: str) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT o.*, MAX(e.observed_at) AS observed_at,
        MAX(r.source_as_of) AS run_source_as_of
        FROM financial_observations o
        JOIN observation_events e ON e.record_key=o.record_key
        JOIN ingestion_runs r ON r.run_id=e.run_id AND r.status='completed'
        WHERE o.code=? AND o.endpoint='fina_indicator' GROUP BY o.record_key
        ORDER BY REPLACE(o.end_date, '-', '') DESC, observed_at DESC""",
        (code,),
    ).fetchall()
    if not rows:
        return {"status": "HOLD", "reasons": ["MISSING_FINANCIAL_OBSERVATION"]}
    cutoff = _compact(as_of)
    eligible_periods: list[sqlite3.Row] = []
    for row in rows:
        period = _compact(row["end_date"])
        if not period or period > cutoff:
            continue
        eligible_periods.append(row)
        effective = _compact(row["f_ann_date"] or row["ann_date"])
        if effective and effective <= cutoff:
            return {"status": "READY", "reasons": []}
    if not any(_compact(row["end_date"]) for row in rows):
        return {"status": "HOLD", "reasons": ["MISSING_REPORT_PERIOD"]}
    if not eligible_periods:
        return {"status": "HOLD", "reasons": ["FINANCIAL_REPORT_PERIOD_AFTER_AS_OF"]}
    if not any(_compact(row["f_ann_date"] or row["ann_date"]) for row in eligible_periods):
        return {"status": "HOLD", "reasons": ["MISSING_EFFECTIVE_ANN_DATE"]}
    return {"status": "HOLD", "reasons": ["FINANCIAL_ANNOUNCEMENT_AFTER_AS_OF"]}


def _dividend_run(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    fingerprint = request_fingerprint("dividend", code)
    return conn.execute(
        """SELECT * FROM ingestion_runs WHERE endpoint='dividend' AND request_fingerprint=?
        ORDER BY run_id DESC LIMIT 1""",
        (fingerprint,),
    ).fetchone()


def _latest_eligible_dividend_observation(conn: sqlite3.Connection, code: str, as_of: str) -> sqlite3.Row | None:
    rows = conn.execute(
        """SELECT o.*, MAX(e.observed_at) AS observed_at,
        MAX(r.source_as_of) AS run_source_as_of
        FROM dividend_observations o
        JOIN observation_events e ON e.record_key=o.record_key
        JOIN ingestion_runs r ON r.run_id=e.run_id AND r.status='completed'
        WHERE o.code=? GROUP BY o.record_key
        ORDER BY REPLACE(o.ex_date, '-', '') DESC, observed_at DESC""",
        (code,),
    ).fetchall()
    cutoff = _compact(as_of)
    for row in rows:
        ex_date = _compact(row["ex_date"])
        if row["div_proc"] == "实施" and ex_date and ex_date <= cutoff:
            return row
    return None


def _has_dividend_observation(conn: sqlite3.Connection, code: str) -> bool:
    return (
        conn.execute(
            """SELECT 1 FROM dividend_observations o
            JOIN observation_events e ON e.record_key=o.record_key
            JOIN ingestion_runs r ON r.run_id=e.run_id AND r.status='completed'
            WHERE o.code=? LIMIT 1""",
            (code,),
        ).fetchone()
        is not None
    )


def _dividend_detail(conn: sqlite3.Connection, code: str, as_of: str) -> dict[str, Any]:
    row = _latest_eligible_dividend_observation(conn, code, as_of)
    if row is not None:
        return {"status": "READY", "reasons": [], "source_as_of": row["run_source_as_of"]}
    run = _dividend_run(conn, code)
    if run is None:
        return {"status": "HOLD", "reasons": ["MISSING_DIVIDEND_RUN"]}
    if run["status"] != "completed":
        return {"status": "HOLD", "reasons": ["DIVIDEND_FETCH_FAILED"]}
    if int(run["row_count"]) == 0:
        return {"status": "BUSINESS_EMPTY", "reasons": [], "source_as_of": run["source_as_of"]}
    if _has_dividend_observation(conn, code):
        return {"status": "HOLD", "reasons": ["DIVIDEND_NOT_YET_IMPLEMENTED"]}
    return {"status": "HOLD", "reasons": ["MISSING_DIVIDEND_OBSERVATION"]}


def _domain_report(
    conn: sqlite3.Connection,
    codes: list[str],
    as_of: str,
    checker: Callable[[sqlite3.Connection, str, str], dict[str, Any]],
) -> dict[str, Any]:
    details = {code: checker(conn, code, as_of) for code in codes}
    ready = sum(item["status"] in {"READY", "BUSINESS_EMPTY"} for item in details.values())
    return {"status": "READY" if ready == len(codes) else "HOLD", "ready": ready, "details": details}


def assess_readiness(
    *,
    db_path: Path,
    as_of: str,
    scope: str,
    watchlist_codes: Iterable[str],
) -> dict[str, Any]:
    """Assess requested domains without mutating the shadow database."""
    if scope not in SCOPES:
        raise ValueError(f"unsupported scope: {scope}")
    codes = list(dict.fromkeys(str(code) for code in watchlist_codes))
    requested = SCOPES[:3] if scope == "all" else (scope,)
    checkers = {
        "valuation": _valuation_detail,
        "financial": _financial_detail,
        "dividend": _dividend_detail,
    }
    with _connection(db_path) as conn:
        domains = {name: _domain_report(conn, codes, as_of, checkers[name]) for name in requested}
    ready_codes = sum(
        all(domains[name]["details"][code]["status"] in {"READY", "BUSINESS_EMPTY"} for name in requested)
        for code in codes
    )
    status = "READY" if ready_codes == len(codes) else "HOLD"
    return {
        "status": status,
        "scope": scope,
        "as_of_date": as_of,
        "coverage": {"required": len(codes), "ready": ready_codes},
        "domains": domains,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check TuShare primary shadow readiness.")
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--scope", required=True, choices=SCOPES)
    parser.add_argument("--watchlist-codes")
    return parser


def _codes(value: str | None) -> list[str]:
    if value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item["code"]) for item in WATCHLIST]


def readiness_main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = assess_readiness(
        db_path=args.db_path,
        as_of=args.as_of_date,
        scope=args.scope,
        watchlist_codes=_codes(args.watchlist_codes),
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    return 0 if report["status"] == "READY" else 1
