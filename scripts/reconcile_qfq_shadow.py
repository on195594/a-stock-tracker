"""Reconcile BaoStock QFQ bars with the TuShare QFQ shadow partition."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_tracker.config import WATCHLIST
from a_stock_tracker.data.cache import DB_PATH

BASELINE_ADJUSTED = "qfq"
SHADOW_ADJUSTED = "qfq_tushare_shadow"
PRICE_FIELDS = ("open", "high", "low", "close")
ALL_FIELDS = (*PRICE_FIELDS, "volume")
DEFAULT_REPORT_DIR = PROJECT_ROOT / "artifacts" / "qfq-reconciliation"
_CODE_RE = re.compile(r"^\d{6}$")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile BaoStock and TuShare QFQ daily bars.")
    parser.add_argument("--code", default=None, help="Single 6-digit stock code (omit to check full watchlist)")
    args = parser.parse_args(argv)
    if args.code is not None and not _CODE_RE.match(args.code):
        parser.error("--code must be a 6-digit string")
    return args


def _read_rows(db_path: str | Path, codes: Sequence[str]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in codes)
    sql = f"""
        SELECT code, trade_date, open, high, low, close, volume, adjusted
        FROM daily_bars
        WHERE code IN ({placeholders}) AND adjusted IN (?, ?)
        ORDER BY code, trade_date, adjusted
    """
    uri = f"file:{Path(db_path)}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, (*codes, BASELINE_ADJUSTED, SHADOW_ADJUSTED)).fetchall()


def _field_violation(field: str, qfq_value: Any, shadow_value: Any) -> dict[str, Any] | None:
    if qfq_value is None or shadow_value is None:
        return {
            "field": field,
            "qfq_value": qfq_value,
            "shadow_value": shadow_value,
            "diff": None,
        }

    diff = abs(float(qfq_value) - float(shadow_value))
    tolerance = max(0.01, 0.001 * abs(float(qfq_value))) if field in PRICE_FIELDS else 1
    if diff <= tolerance:
        return None
    return {
        "field": field,
        "qfq_value": qfq_value,
        "shadow_value": shadow_value,
        "diff": diff,
    }


def build_report(rows: Sequence[sqlite3.Row], codes: Sequence[str], report_date: str) -> dict[str, Any]:
    partitions: dict[str, dict[str, dict[str, sqlite3.Row]]] = {
        code: {BASELINE_ADJUSTED: {}, SHADOW_ADJUSTED: {}} for code in codes
    }
    for row in rows:
        partitions[row["code"]][row["adjusted"]][row["trade_date"]] = row

    code_reports: dict[str, dict[str, Any]] = {}
    totals = {
        "common_rows_checked": 0,
        "out_of_tolerance_rows": 0,
        "only_qfq_dates": 0,
        "only_shadow_dates": 0,
        "zero_common_codes": 0,
    }
    for code in codes:
        qfq = partitions[code][BASELINE_ADJUSTED]
        shadow = partitions[code][SHADOW_ADJUSTED]
        only_qfq = sorted(set(qfq) - set(shadow))
        only_shadow = sorted(set(shadow) - set(qfq))
        common = sorted(set(qfq) & set(shadow))
        violations: list[dict[str, Any]] = []

        for trade_date in common:
            failed_fields = []
            for field in ALL_FIELDS:
                failure = _field_violation(field, qfq[trade_date][field], shadow[trade_date][field])
                if failure is not None:
                    failed_fields.append(failure)
            if failed_fields:
                violations.append({"trade_date": trade_date, "fields": failed_fields})

        code_reports[code] = {
            "common_dates_checked": len(common),
            "out_of_tolerance_rows": violations,
            "only_qfq": only_qfq,
            "only_shadow": only_shadow,
        }
        totals["common_rows_checked"] += len(common)
        totals["out_of_tolerance_rows"] += len(violations)
        totals["only_qfq_dates"] += len(only_qfq)
        totals["only_shadow_dates"] += len(only_shadow)
        totals["zero_common_codes"] += int(not common)

    return {
        "report_date": report_date,
        "baseline_adjusted": BASELINE_ADJUSTED,
        "shadow_adjusted": SHADOW_ADJUSTED,
        "codes_checked": list(codes),
        "per_code": code_reports,
        "overall": totals,
    }


def run_reconciliation(
    db_path: str | Path,
    codes: Sequence[str],
    report_path: str | Path,
    *,
    report_date: str | None = None,
) -> int:
    effective_date = report_date or date.today().isoformat()
    report = build_report(_read_rows(db_path, codes), codes, effective_date)
    output_path = Path(report_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for code in codes:
        result = report["per_code"][code]
        suffix = " ZERO_COMMON_DATES" if result["common_dates_checked"] == 0 else ""
        print(
            f"{code}: common={result['common_dates_checked']} "
            f"tolerance_violations={len(result['out_of_tolerance_rows'])} "
            f"only_qfq={len(result['only_qfq'])} only_shadow={len(result['only_shadow'])}{suffix}"
        )
    totals = report["overall"]
    print(
        f"OVERALL: common={totals['common_rows_checked']} "
        f"tolerance_violations={totals['out_of_tolerance_rows']} "
        f"only_qfq={totals['only_qfq_dates']} only_shadow={totals['only_shadow_dates']} "
        f"zero_common_codes={totals['zero_common_codes']}"
    )
    return int(totals["out_of_tolerance_rows"] > 0 or totals["zero_common_codes"] > 0)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    codes = [args.code] if args.code else [item["code"] for item in WATCHLIST]
    today = date.today().isoformat()
    report_path = DEFAULT_REPORT_DIR / f"report-{today}.json"
    return run_reconciliation(DB_PATH, codes, report_path, report_date=today)


if __name__ == "__main__":
    raise SystemExit(main())
