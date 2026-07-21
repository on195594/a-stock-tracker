#!/usr/bin/env python3
"""Run read-only qualitative-v2 production acceptance and rollback checks."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import a_stock_tracker.config as config  # noqa: E402
from a_stock_tracker.qualitative.production import ProductionV2Error  # noqa: E402
from a_stock_tracker.qualitative.production_acceptance import (  # noqa: E402
    BASELINE_SCHEMA_VERSION,
    AcceptanceError,
    run_acceptance,
)

DEFAULT_BASELINE = PROJECT_ROOT / "config" / "qualitative" / "production_acceptance_baseline.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(config.DB_PATH))
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--daily-log", type=Path, default=Path(config.LOG_DIR) / "daily.log")
    score_date = parser.add_mutually_exclusive_group()
    score_date.add_argument(
        "--require-score-date",
        help="Also require an exact 35-stock Framework A daily run and v2 adoption log evidence for YYYY-MM-DD.",
    )
    score_date.add_argument(
        "--require-today",
        action="store_true",
        help="Require today's exact Framework A daily run and v2 adoption log evidence (for managed cron).",
    )
    return parser


def resolve_required_score_date(
    require_score_date: str | None,
    require_today: bool,
    *,
    today: date | None = None,
) -> str | None:
    """Resolve the daily evidence date without shell date interpolation."""
    if require_today:
        return (today or date.today()).isoformat()
    return require_score_date


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    required_score_date = resolve_required_score_date(args.require_score_date, args.require_today)
    canary_codes = config.QUALITATIVE_V2_CANARY_CODES | config.QUALITATIVE_V2_PILOT_CODES
    try:
        report = run_acceptance(
            database_path=args.db,
            env_path=args.env_file,
            baseline_path=args.baseline,
            daily_log_path=args.daily_log,
            watchlist=config.WATCHLIST,
            canary_codes=canary_codes,
            required_score_date=required_score_date,
        )
    except (AcceptanceError, ProductionV2Error, OSError, sqlite3.Error, ValueError) as exc:
        report = {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "decision": "ROLLBACK",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    print(json.dumps(report, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
