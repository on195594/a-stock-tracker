"""Bounded orchestration for TuShare primary-source shadow ingestion."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from a_stock_tracker.config import WATCHLIST
from a_stock_tracker.data.tushare_primary_ingestion import IngestionRequest, execute_ingestion

ExecuteFn = Callable[[IngestionRequest], Any]
FINANCIAL_ENDPOINTS = ("fina_indicator", "income", "balancesheet", "cashflow")
SCOPES = ("valuation", "financial", "dividend", "all")


def request_fingerprint(endpoint: str, code: str) -> str:
    """Return the Phase 2 fingerprint for a code-only request."""
    payload = {
        "endpoint": endpoint,
        "code": code,
        "start_date": None,
        "end_date": None,
        "trade_date": None,
        "period": None,
        "allowed_codes": None,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _request(
    endpoint: str,
    db_path: Path,
    artifact_root: Path,
    code: str | None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    trade_date: str | None = None,
    allowed_codes: frozenset[str] | None = None,
) -> IngestionRequest:
    return IngestionRequest(
        endpoint=endpoint,
        db_path=db_path,
        artifact_root=artifact_root,
        code=code,
        start_date=start_date,
        end_date=end_date,
        trade_date=trade_date,
        pit_status="backfilled_latest",
        allowed_codes=allowed_codes,
    )


def _start_date(as_of: str, days: int, explicit: str | None) -> str:
    if explicit:
        return explicit
    return (date.fromisoformat(as_of) - timedelta(days=days)).isoformat()


def _valuation_plan(
    db_path: Path,
    artifact_root: Path,
    as_of: str,
    codes: list[str],
    start: str,
) -> list[tuple[str, IngestionRequest]]:
    allowed = frozenset(codes)
    plan = [
        (
            "daily_basic",
            _request("daily_basic", db_path, artifact_root, None, trade_date=as_of, allowed_codes=allowed),
        )
    ]
    plan.extend(
        ("valuation-history", _request("daily_basic", db_path, artifact_root, code, start_date=start, end_date=as_of))
        for code in codes
    )
    return plan


def _financial_plan(
    db_path: Path,
    artifact_root: Path,
    as_of: str,
    codes: list[str],
    start: str,
    endpoints: tuple[str, ...],
) -> list[tuple[str, IngestionRequest]]:
    return [
        (endpoint, _request(endpoint, db_path, artifact_root, code, start_date=start, end_date=as_of))
        for code in codes
        for endpoint in endpoints
    ]


def _dividend_plan(
    db_path: Path,
    artifact_root: Path,
    codes: list[str],
) -> list[tuple[str, IngestionRequest]]:
    return [("dividend", _request("dividend", db_path, artifact_root, code)) for code in codes]


def _build_plan(
    scope: str,
    db_path: Path,
    artifact_root: Path,
    as_of: str,
    codes: list[str],
    start: str,
    endpoints: tuple[str, ...],
) -> list[tuple[str, IngestionRequest]]:
    plan: list[tuple[str, IngestionRequest]] = []
    if scope in {"valuation", "all"}:
        plan.extend(_valuation_plan(db_path, artifact_root, as_of, codes, start))
    if scope in {"financial", "all"}:
        plan.extend(_financial_plan(db_path, artifact_root, as_of, codes, start, endpoints))
    if scope in {"dividend", "all"}:
        plan.extend(_dividend_plan(db_path, artifact_root, codes))
    return plan


def _summary_dict(label: str, summary: Any) -> dict[str, Any]:
    return {
        "endpoint": label,
        "status": getattr(summary, "status", None),
        "source_as_of": getattr(summary, "source_as_of", None),
        "row_count": getattr(summary, "row_count", 0),
        "record_count": getattr(summary, "record_count", 0),
        "event_count": getattr(summary, "event_count", 0),
        "error_code": getattr(summary, "error_code", None),
    }


def run_tushare_primary_batch(
    *,
    scope: str,
    db_path: Path,
    artifact_root: Path,
    as_of: str,
    watchlist_codes: Iterable[str],
    execute: ExecuteFn = execute_ingestion,
    execute_mode: bool = False,
    financial_endpoints: tuple[str, ...] = FINANCIAL_ENDPOINTS,
    valuation_history_days: int = 3653,
    request_payload_start_date: str | None = None,
) -> dict[str, Any]:
    """Preview or execute a bounded ingestion request plan."""
    if db_path.name == "tracker.db":
        return {
            "executed": False,
            "success": False,
            "request_count": 0,
            "request_results": [],
            "failed_count": 0,
            "errors": ["refuse-production-db"],
        }
    if scope not in SCOPES:
        return {
            "executed": False,
            "success": False,
            "request_count": 0,
            "request_results": [],
            "failed_count": 0,
            "errors": ["invalid-scope"],
        }
    codes = list(dict.fromkeys(str(code) for code in watchlist_codes))
    start = _start_date(as_of, valuation_history_days, request_payload_start_date)
    plan = _build_plan(scope, db_path, artifact_root, as_of, codes, start, financial_endpoints)
    results = [_summary_dict(label, execute(request)) for label, request in plan] if execute_mode else []
    failed = sum(item["status"] != "completed" for item in results)
    return {
        "executed": execute_mode,
        "success": failed == 0,
        "request_count": len(plan),
        "request_results": results,
        "failed_count": failed,
        "errors": [],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview or execute bounded TuShare shadow ingestion.")
    parser.add_argument("--scope", required=True, choices=SCOPES)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--watchlist-codes")
    parser.add_argument("--request-payload-start-date")
    parser.add_argument("--valuation-history-days", type=int, default=3653)
    parser.add_argument("--financial-endpoints", default=",".join(FINANCIAL_ENDPOINTS))
    parser.add_argument("--execute", action="store_true")
    return parser


def _codes(value: str | None) -> list[str]:
    if value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item["code"]) for item in WATCHLIST]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_tushare_primary_batch(
        scope=args.scope,
        db_path=args.db_path,
        artifact_root=args.artifact_root,
        as_of=args.as_of_date,
        watchlist_codes=_codes(args.watchlist_codes),
        execute=execute_ingestion,
        execute_mode=args.execute,
        financial_endpoints=tuple(item for item in args.financial_endpoints.split(",") if item),
        valuation_history_days=args.valuation_history_days,
        request_payload_start_date=args.request_payload_start_date,
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    return 0 if report["success"] else 1
