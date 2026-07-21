"""Production cycles for bounded TuShare ingestion and materialization."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Any

from a_stock_tracker.config import WATCHLIST
from a_stock_tracker.data.tushare_primary_batch import run_tushare_primary_batch
from a_stock_tracker.data.tushare_primary_ingestion import IngestionRequest, execute_ingestion
from a_stock_tracker.data.tushare_primary_materialization import run_materialization
from a_stock_tracker.data.tushare_primary_readiness import assess_readiness
from a_stock_tracker.paths import PROJECT_ROOT

SHADOW_DB = PROJECT_ROOT / "data/tushare-primary.db"
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts/tushare-ingestion"
TRACKER_DB = PROJECT_ROOT / "tracker.db"


def _codes() -> list[str]:
    return [str(item["code"]) for item in WATCHLIST]


def _require_ready(report: dict[str, Any], label: str) -> None:
    if report.get("status") != "READY":
        raise RuntimeError(f"{label}_NOT_READY")


def run_daily_cycle(as_of_date: str) -> dict[str, Any]:
    codes = _codes()
    summary = execute_ingestion(
        IngestionRequest(
            endpoint="daily_basic",
            db_path=SHADOW_DB,
            artifact_root=ARTIFACT_ROOT,
            code=None,
            start_date=None,
            end_date=None,
            trade_date=as_of_date,
            pit_status="prospective_observed",
            allowed_codes=frozenset(codes),
        )
    )
    if summary.status != "completed":
        raise RuntimeError(f"VALUATION_INGESTION_FAILED:{summary.error_code}")
    readiness = assess_readiness(
        db_path=SHADOW_DB,
        as_of=as_of_date,
        scope="valuation",
        watchlist_codes=codes,
    )
    _require_ready(readiness, "VALUATION")
    result = run_materialization(
        shadow_db_path=SHADOW_DB,
        target_db_path=TRACKER_DB,
        as_of_date=as_of_date,
        watchlist=codes,
        valuation_enabled=True,
        financial_enabled=False,
        dividend_enabled=False,
        execute=True,
    )
    return {"mode": "daily", "ingestion": asdict(summary), "changed_count": result.changed_count}


def _run_batch(scope: str, as_of_date: str, codes: list[str]) -> dict[str, Any]:
    report = run_tushare_primary_batch(
        scope=scope,
        db_path=SHADOW_DB,
        artifact_root=ARTIFACT_ROOT,
        as_of=as_of_date,
        watchlist_codes=codes,
        execute_mode=True,
    )
    if not report["success"]:
        raise RuntimeError(f"{scope.upper()}_INGESTION_FAILED")
    return report


def run_weekly_cycle(as_of_date: str) -> dict[str, Any]:
    codes = _codes()
    financial = _run_batch("financial", as_of_date, codes)
    dividend = _run_batch("dividend", as_of_date, codes)
    for scope in ("financial", "dividend"):
        readiness = assess_readiness(
            db_path=SHADOW_DB,
            as_of=as_of_date,
            scope=scope,
            watchlist_codes=codes,
        )
        _require_ready(readiness, scope.upper())
    result = run_materialization(
        shadow_db_path=SHADOW_DB,
        target_db_path=TRACKER_DB,
        as_of_date=as_of_date,
        watchlist=codes,
        valuation_enabled=False,
        financial_enabled=True,
        dividend_enabled=True,
        execute=True,
    )
    return {
        "mode": "weekly",
        "financial_requests": financial["request_count"],
        "dividend_requests": dividend["request_count"],
        "changed_count": result.changed_count,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run one TuShare production data cycle.")
    parser.add_argument("mode", choices=("daily", "weekly"))
    parser.add_argument("--as-of-date", default=date.today().isoformat())
    args = parser.parse_args(argv)
    try:
        report = run_daily_cycle(args.as_of_date) if args.mode == "daily" else run_weekly_cycle(args.as_of_date)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False) + "\n")
        return 1
    sys.stdout.write(
        json.dumps({"status": "completed", **report}, ensure_ascii=False, sort_keys=True, default=str) + "\n"
    )
    return 0
