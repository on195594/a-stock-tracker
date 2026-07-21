"""Explicit CLI and orchestration for isolated TuShare primary-source ingestion."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from a_stock_tracker.data.tushare_primary_cache import (
    FINANCIAL_ENDPOINTS,
    complete_ingestion_run,
    create_ingestion_run,
    open_shadow_store,
    persist_observations,
    sanitize_error_message,
    write_raw_artifact,
)

LOGGER = logging.getLogger(__name__)
ProviderFactory = Callable[[str], Any]


@dataclass(frozen=True)
class IngestionRequest:
    """Validated inputs for one provider request and shadow checkpoint."""

    endpoint: str
    db_path: Path
    artifact_root: Path
    code: str | None
    start_date: str | None
    end_date: str | None
    trade_date: str | None
    pit_status: str
    period: str | None = None
    allowed_codes: frozenset[str] | None = None


@dataclass(frozen=True)
class IngestionSummary:
    """Credential-free terminal summary for one ingestion request."""

    run_id: int
    endpoint: str
    status: str
    row_count: int
    record_count: int
    event_count: int
    source_as_of: str | None
    db_path: Path
    artifact_path: str | None
    error_code: str | None = None
    error_message: str | None = None


def execute_ingestion(request: IngestionRequest, provider: Any | None = None) -> IngestionSummary:
    """Execute one bounded provider request against an explicit shadow database."""
    requested_at = _now()
    fingerprint = _request_fingerprint(request)
    with closing(open_shadow_store(request.db_path)) as conn:
        run_id = create_ingestion_run(conn, request.endpoint, fingerprint, requested_at)
        try:
            active_provider = provider or _default_provider_factory(request.endpoint)
            result = _fetch(active_provider, request)
        except Exception as exc:
            return _complete_unexpected_failure(conn, request, run_id, exc)
        if result.value is None or result.status == "failed":
            return _complete_provider_failure(conn, request, run_id, result)
        raw_rows = result.value.to_dict(orient="records")
        artifact = None
        try:
            artifact = write_raw_artifact(request.artifact_root, run_id, request.endpoint, raw_rows)
            stored_rows = _rows_for_store(request, raw_rows)
            record_count, event_count = persist_observations(
                conn,
                request.endpoint,
                stored_rows,
                run_id,
                _now(),
                request.pit_status,
                result.source,
                result.source_as_of,
                commit=False,
            )
            complete_ingestion_run(
                conn,
                run_id,
                status="completed",
                completed_at=_now(),
                row_count=len(raw_rows),
                source_as_of=result.source_as_of,
                payload_sha256=artifact.sha256,
                raw_artifact_path=artifact.relative_path,
            )
        except Exception as exc:
            if artifact is not None:
                _remove_artifact(artifact.path)
            return _complete_unexpected_failure(conn, request, run_id, exc)
    return IngestionSummary(
        run_id,
        request.endpoint,
        "completed",
        len(raw_rows),
        record_count,
        event_count,
        result.source_as_of,
        request.db_path.resolve(),
        str(artifact.path),
    )


def _remove_artifact(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError as exc:
        LOGGER.warning("failed to remove orphan artifact %s: %s", path, exc)


def _rows_for_store(request: IngestionRequest, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if request.endpoint != "daily_basic" or not request.trade_date or request.code:
        return rows
    allowed_codes = request.allowed_codes or _configured_watchlist_codes()
    return [row for row in rows if str(row.get("ts_code", "")).split(".", 1)[0] in allowed_codes]


def _configured_watchlist_codes() -> frozenset[str]:
    from a_stock_tracker.config import WATCHLIST

    return frozenset(str(item["code"]) for item in WATCHLIST)


def _fetch(provider: Any, request: IngestionRequest) -> Any:
    if request.endpoint == "daily_basic" and request.trade_date:
        return provider.fetch_daily_basic_by_trade_date(request.trade_date, request.code)
    if request.endpoint == "daily_basic":
        assert request.code and request.start_date and request.end_date
        return provider.fetch_valuation_history(request.code, request.start_date, request.end_date)
    if request.endpoint in FINANCIAL_ENDPOINTS:
        assert request.code
        method_name = {
            "fina_indicator": "fetch_indicator_history",
            "income": "fetch_income_history",
            "balancesheet": "fetch_balance_history",
            "cashflow": "fetch_cashflow_history",
        }[request.endpoint]
        return getattr(provider, method_name)(request.code, request.start_date, request.end_date, request.period)
    if request.endpoint == "dividend":
        assert request.code
        return provider.fetch_dividend_history(request.code)
    raise ValueError(f"unsupported endpoint: {request.endpoint}")


def _complete_provider_failure(
    conn: Any,
    request: IngestionRequest,
    run_id: int,
    result: Any,
) -> IngestionSummary:
    message = sanitize_error_message(result.error_message)
    complete_ingestion_run(
        conn,
        run_id,
        status="failed",
        completed_at=_now(),
        source_as_of=result.source_as_of,
        error_code=result.error_code,
        error_message=message,
    )
    return IngestionSummary(
        run_id,
        request.endpoint,
        "failed",
        0,
        0,
        0,
        result.source_as_of,
        request.db_path.resolve(),
        None,
        result.error_code,
        message,
    )


def _complete_unexpected_failure(
    conn: Any,
    request: IngestionRequest,
    run_id: int,
    exc: Exception,
) -> IngestionSummary:
    conn.rollback()
    message = sanitize_error_message(str(exc))
    complete_ingestion_run(
        conn,
        run_id,
        status="failed",
        completed_at=_now(),
        error_code="UNKNOWN_ERROR",
        error_message=message,
    )
    return IngestionSummary(
        run_id, request.endpoint, "failed", 0, 0, 0, None, request.db_path.resolve(), None, "UNKNOWN_ERROR", message
    )


def _default_provider_factory(endpoint: str) -> Any:
    if endpoint == "daily_basic":
        from a_stock_lib.providers.tushare_valuation import TushareValuationProvider

        return TushareValuationProvider()
    if endpoint in FINANCIAL_ENDPOINTS:
        from a_stock_lib.providers.tushare_financials import TushareFinancialProvider

        return TushareFinancialProvider()
    if endpoint == "dividend":
        from a_stock_lib.providers.tushare_financials import TushareDividendProvider

        return TushareDividendProvider()
    raise ValueError(f"unsupported endpoint: {endpoint}")


def _request_fingerprint(request: IngestionRequest) -> str:
    payload = {
        "endpoint": request.endpoint,
        "code": request.code,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "trade_date": request.trade_date,
        "period": request.period,
        "allowed_codes": sorted(request.allowed_codes) if request.allowed_codes else None,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write TuShare data only to an explicit isolated shadow SQLite database."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_valuation_history_parser(subparsers)
    _add_valuation_daily_parser(subparsers)
    _add_financial_parser(subparsers)
    _add_dividend_parser(subparsers)
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument(
        "--pit-status",
        choices=("backfilled_latest", "prospective_observed"),
        default="backfilled_latest",
    )


def _add_valuation_history_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("valuation-history")
    _add_common_arguments(parser)
    parser.add_argument("--code", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)


def _add_valuation_daily_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("valuation-daily")
    _add_common_arguments(parser)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--code")


def _add_financial_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("financial-history")
    _add_common_arguments(parser)
    parser.add_argument("--endpoint", required=True, choices=tuple(sorted(FINANCIAL_ENDPOINTS)))
    parser.add_argument("--code", required=True)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--period")


def _add_dividend_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("dividend-history")
    _add_common_arguments(parser)
    parser.add_argument("--code", required=True)


def _request_from_args(args: argparse.Namespace) -> IngestionRequest:
    endpoint = (
        args.endpoint
        if args.command == "financial-history"
        else {
            "valuation-history": "daily_basic",
            "valuation-daily": "daily_basic",
            "dividend-history": "dividend",
        }[args.command]
    )
    return IngestionRequest(
        endpoint=endpoint,
        db_path=args.db_path,
        artifact_root=args.artifact_root,
        code=getattr(args, "code", None),
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        trade_date=getattr(args, "trade_date", None),
        pit_status=args.pit_status,
        period=getattr(args, "period", None),
    )


def main(argv: list[str] | None = None, provider_factory: ProviderFactory | None = None) -> int:
    """Parse one explicit shadow request and emit a credential-free JSON summary."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    request = _request_from_args(_parser().parse_args(argv))
    LOGGER.info("shadow_db=%s", request.db_path.expanduser().resolve())
    provider = provider_factory(request.endpoint) if provider_factory else None
    summary = execute_ingestion(request, provider=provider)
    payload = asdict(summary)
    payload["db_path"] = str(summary.db_path)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return 0 if summary.status == "completed" else 1
