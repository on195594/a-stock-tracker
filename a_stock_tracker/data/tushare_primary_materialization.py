"""PIT-safe materialization from TuShare shadow observations into tracker cache."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from a_stock_lib.valuation import compute_valuation_percentile
from a_stock_tracker.config import (
    FEATURE_FLAG_DIVIDEND_DOMAIN,
    FEATURE_FLAG_FINANCIAL_DOMAIN,
    FEATURE_FLAG_VALUATION_DOMAIN,
    WATCHLIST,
    get_materialization_feature_flag,
)


class MaterializationError(RuntimeError):
    """Base error for an unsafe materialization request."""


class MaterializationReadinessError(MaterializationError):
    """Raised when a requested domain is not ready for atomic publication."""


@dataclass(frozen=True)
class MaterializationResult:
    """Credential-free preview and execution result."""

    preview_json: str
    executed: bool
    changed_count: int


def _compact(value: Any) -> str:
    return str(value or "").replace("-", "")[:8]


def _iso_date(value: Any) -> str | None:
    compact = _compact(value)
    if len(compact) != 8:
        return None
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _load_rows(conn: sqlite3.Connection, table: str, code: str) -> list[dict[str, Any]]:
    payload_column = "payload_json" if table != "valuation_observations" else "NULL"
    query = f"""SELECT o.*, {payload_column} AS raw_payload,
        COALESCE(MAX(e.observed_at), '') AS observed_at,
        MAX(CASE WHEN r.status='completed' THEN r.source_as_of END) AS run_source_as_of
        FROM {table} o
        LEFT JOIN observation_events e ON e.record_key=o.record_key
        LEFT JOIN ingestion_runs r ON r.run_id=e.run_id
        WHERE o.code=? GROUP BY o.record_key"""
    rows: list[dict[str, Any]] = []
    for raw in conn.execute(query, (code,)).fetchall():
        row = dict(raw)
        payload = row.pop("raw_payload", None)
        if payload:
            row.update(json.loads(payload))
        rows.append(row)
    return rows


def _month_key(trade_date: Any) -> str:
    compact = _compact(trade_date)
    return compact[:6] if len(compact) >= 6 else ""


def _monthly_valuation(rows: Iterable[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
    cutoff = _compact(as_of)
    monthly: dict[str, dict[str, Any]] = {}
    for row in rows:
        trade_date = _compact(row.get("trade_date"))
        pb = _finite(row.get("pb"))
        if not trade_date or trade_date > cutoff or pb is None or pb <= 0:
            continue
        key = _month_key(trade_date)
        if key and (key not in monthly or trade_date > _compact(monthly[key].get("trade_date"))):
            monthly[key] = row
    return [monthly[key] for key in sorted(monthly)]


def _coverage_status(monthly: list[dict[str, Any]], as_of: str) -> str:
    if len(monthly) < 60:
        return "INSUFFICIENT_HISTORY"
    first = _month_key(monthly[0].get("trade_date"))
    cutoff = _month_key(as_of)
    month_span = (int(cutoff[:4]) - int(first[:4])) * 12 + int(cutoff[4:6]) - int(first[4:6])
    return "FULL_10Y" if len(monthly) >= 120 and month_span >= 120 else "SINCE_LISTING"


def _compute_valuation_percentile(rows: list[dict[str, Any]], as_of: str) -> float | None:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return None
    result = compute_valuation_percentile(frame, "pb", date.fromisoformat(as_of).isoformat())
    return result.percentile


def _valuation_patch(rows: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    cutoff = _compact(as_of)
    eligible = [row for row in rows if _compact(row.get("trade_date")) <= cutoff]
    if not eligible:
        return {}
    latest = max(eligible, key=lambda row: (_compact(row.get("trade_date")), row.get("observed_at", "")))
    monthly = _monthly_valuation(eligible, as_of)
    coverage = _coverage_status(monthly, as_of) if monthly else "INSUFFICIENT_HISTORY"
    total_mv = _finite(latest.get("total_mv"))
    circ_mv = _finite(latest.get("circ_mv"))
    ratio = round(circ_mv / total_mv * 100, 4) if total_mv and circ_mv is not None else None
    patch = {
        "pe_ttm": _finite(latest.get("pe_ttm")),
        "pb": _finite(latest.get("pb")),
        "dividend_yield": _finite(latest.get("dv_ttm")),
        "float_to_total_ratio": ratio,
        "pb_hist_monthly": [_finite(row.get("pb")) for row in monthly],
        "valuation_coverage_status": coverage,
        "valuation_valid_months": len(monthly),
        "valuation_window_start": _compact(monthly[0].get("trade_date")) if monthly else None,
        "valuation_window_end": _compact(monthly[-1].get("trade_date")) if monthly else None,
        "valuation_source": latest.get("source"),
        "valuation_source_as_of": latest.get("source_as_of"),
        "pb_percentile_10y": None,
    }
    if coverage == "FULL_10Y":
        patch["pb_percentile_10y"] = _compute_valuation_percentile(eligible, as_of)
    return patch


def _effective_date(row: dict[str, Any]) -> str:
    return _compact(row.get("f_ann_date") or row.get("ann_date"))


def _dedupe_financial(rows: list[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
    cutoff = _compact(as_of)
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        period = _compact(row.get("end_date"))
        effective = _effective_date(row)
        if not period or period > cutoff or not effective or effective > cutoff:
            continue
        previous = selected.get(period)
        rank = (effective, str(row.get("observed_at", "")), str(row.get("update_flag", "")))
        if previous is None or rank > (
            _effective_date(previous),
            str(previous.get("observed_at", "")),
            str(previous.get("update_flag", "")),
        ):
            selected[period] = row
    return [selected[key] for key in sorted(selected, reverse=True)]


def _mean(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [_finite(row.get(field)) for row in rows]
    valid = [value for value in values if value is not None]
    return round(sum(valid) / len(valid), 2) if valid else None


def _is_financial_industry(industry: str) -> bool:
    return any(word in industry for word in ("银行", "证券", "保险", "金融"))


def _financial_patch(rows: list[dict[str, Any]], as_of: str, industry: str) -> dict[str, Any]:
    selected = _dedupe_financial(rows, as_of)
    if not selected:
        return {}
    annual = [row for row in selected if _compact(row.get("end_date")).endswith("1231")][:3]
    latest_annual = annual[0] if annual else None
    latest = selected[0]
    gross_margin = None if _is_financial_industry(industry) else _finite(latest.get("grossprofit_margin"))
    return {
        "roe_3y_avg": _mean(annual, "roe_waa"),
        "roe_latest": _finite(latest_annual.get("roe_waa")) if latest_annual else None,
        "net_profit_growth": _mean(annual, "netprofit_yoy"),
        "debt_ratio": _finite(latest.get("debt_to_assets")),
        "gross_margin": gross_margin,
        "bps": _finite(latest.get("bps")),
        "report_period": _compact(latest.get("end_date")),
        "financial_annual_periods": [_compact(row.get("end_date")) for row in annual],
        "financial_effective_ann_date": _effective_date(latest),
        "financial_source": latest.get("source"),
        "financial_source_as_of": _iso_date(latest.get("source_as_of")) or _iso_date(_effective_date(latest)),
    }


def _dividend_patch(rows: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    cutoff = _compact(as_of)
    eligible = [
        row
        for row in rows
        if row.get("div_proc") == "实施" and _compact(row.get("ex_date")) and _compact(row.get("ex_date")) <= cutoff
    ]
    if not eligible:
        return {"dps": None, "dividend_status": "BUSINESS_EMPTY"}
    latest = max(eligible, key=lambda row: (_compact(row.get("ex_date")), str(row.get("observed_at", ""))))
    return {
        "dps": _finite(latest.get("cash_div_tax")),
        "dividend_status": "IMPLEMENTED",
        "dividend_ex_date": _iso_date(latest.get("ex_date")),
        "dividend_ann_date": _iso_date(latest.get("ann_date")),
        "dividend_source": latest.get("source"),
        "dividend_source_as_of": latest.get("run_source_as_of"),
    }


def build_stock_fundamentals_payload(
    *,
    shadow_db_path: Path,
    as_of_date: str,
    watchlist: Iterable[str],
    execute: bool,
    target_db_path: Path,
    valuation_enabled: bool | None = None,
    financial_enabled: bool | None = None,
    dividend_enabled: bool | None = None,
    watchlist_industries: dict[str, str] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Build deterministic domain patches without writing the target database."""
    del execute, target_db_path
    enabled = (
        True if valuation_enabled is None else valuation_enabled,
        True if financial_enabled is None else financial_enabled,
        True if dividend_enabled is None else dividend_enabled,
    )
    industries = watchlist_industries or {}
    payload: dict[str, dict[str, dict[str, Any]]] = {}
    with sqlite3.connect(f"file:{shadow_db_path.resolve()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for code in watchlist:
            domains: dict[str, dict[str, Any]] = {}
            if enabled[0]:
                domains["valuation"] = _valuation_patch(_load_rows(conn, "valuation_observations", code), as_of_date)
            if enabled[1]:
                rows = [
                    row
                    for row in _load_rows(conn, "financial_observations", code)
                    if row.get("endpoint") == "fina_indicator"
                ]
                domains["financial"] = _financial_patch(rows, as_of_date, industries.get(code, ""))
            if enabled[2]:
                domains["dividend"] = _dividend_patch(_load_rows(conn, "dividend_observations", code), as_of_date)
            payload[str(code)] = domains
    return payload


def _flatten_domains(domains: dict[str, dict[str, Any]]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for values in domains.values():
        patch.update(values)
    return patch


def _validate_payload(
    payload: dict[str, dict[str, dict[str, Any]]],
    enabled: tuple[bool, bool, bool],
    as_of_date: str,
) -> None:
    for code, domains in payload.items():
        if enabled[0]:
            values = domains.get("valuation", {})
            source_date = _compact(values.get("valuation_source_as_of"))
            if (
                not values
                or any(not values.get(key) for key in ("pb", "valuation_source_as_of"))
                or values.get("float_to_total_ratio") is None
                or source_date != _compact(as_of_date)
                or int(values.get("valuation_valid_months", 0)) <= 0
            ):
                raise MaterializationReadinessError(f"VALUATION_NOT_READY:{code}")
        if enabled[1]:
            values = domains.get("financial", {})
            periods = values.get("financial_annual_periods", [])
            if len(periods) < 3 or values.get("roe_3y_avg") is None or values.get("bps") is None:
                raise MaterializationReadinessError(f"FINANCIAL_NOT_READY:{code}")
        if enabled[2] and "dividend" not in domains:
            raise MaterializationReadinessError(f"DIVIDEND_NOT_READY:{code}")


def _write_payload(target_db_path: Path, payload: dict[str, dict[str, dict[str, Any]]]) -> int:
    changed = 0
    with sqlite3.connect(target_db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for code, domains in payload.items():
                row = conn.execute("SELECT data FROM stock_fundamentals WHERE code=?", (code,)).fetchone()
                if row is None:
                    raise MaterializationReadinessError(f"TARGET_CODE_MISSING:{code}")
                current = json.loads(row[0])
                current.update(_flatten_domains(domains))
                conn.execute(
                    "UPDATE stock_fundamentals SET data=?, updated_at=datetime('now') WHERE code=?",
                    (json.dumps(current, ensure_ascii=False, sort_keys=True), code),
                )
                changed += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return changed


def _resolve_enabled(value: bool | None, flag: str) -> bool:
    return get_materialization_feature_flag(flag) if value is None else value


def run_materialization(
    *,
    shadow_db_path: Path,
    as_of_date: str,
    target_db_path: Path,
    execute: bool,
    watchlist: Iterable[str],
    valuation_enabled: bool | None = None,
    financial_enabled: bool | None = None,
    dividend_enabled: bool | None = None,
    watchlist_industries: dict[str, str] | None = None,
) -> MaterializationResult:
    """Preview or atomically publish enabled TuShare domains."""
    if shadow_db_path.resolve() == target_db_path.resolve():
        raise MaterializationError("SHADOW_TARGET_COLLISION")
    watchlist_codes = list(watchlist)
    if not watchlist_codes:
        raise MaterializationReadinessError("EMPTY_WATCHLIST")
    enabled = (
        _resolve_enabled(valuation_enabled, FEATURE_FLAG_VALUATION_DOMAIN),
        _resolve_enabled(financial_enabled, FEATURE_FLAG_FINANCIAL_DOMAIN),
        _resolve_enabled(dividend_enabled, FEATURE_FLAG_DIVIDEND_DOMAIN),
    )
    if not any(enabled):
        raise MaterializationReadinessError("NO_DOMAIN_ENABLED")
    payload = build_stock_fundamentals_payload(
        shadow_db_path=shadow_db_path,
        as_of_date=as_of_date,
        watchlist=watchlist_codes,
        execute=False,
        target_db_path=target_db_path,
        valuation_enabled=enabled[0],
        financial_enabled=enabled[1],
        dividend_enabled=enabled[2],
        watchlist_industries=watchlist_industries,
    )
    if execute:
        _validate_payload(payload, enabled, as_of_date)
        changed = _write_payload(target_db_path, payload)
    else:
        changed = sum(1 for domains in payload.values() if _flatten_domains(domains))
    preview = {"as_of_date": as_of_date, "changed_count": changed, "domains": payload}
    return MaterializationResult(json.dumps(preview, ensure_ascii=False, sort_keys=True), execute, changed)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview or execute atomic TuShare materialization.")
    parser.add_argument("--shadow-db-path", required=True, type=Path)
    parser.add_argument("--target-db-path", required=True, type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    industries = {str(item["code"]): str(item.get("industry", "")) for item in WATCHLIST}
    try:
        result = run_materialization(
            shadow_db_path=args.shadow_db_path,
            target_db_path=args.target_db_path,
            as_of_date=args.as_of_date,
            execute=args.execute,
            watchlist=(str(item["code"]) for item in WATCHLIST),
            watchlist_industries=industries,
        )
    except MaterializationError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(result.preview_json + "\n")
    return 0
