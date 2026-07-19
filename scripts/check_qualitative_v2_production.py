#!/usr/bin/env python3
"""Run read-only qualitative-v2 production acceptance and rollback checks."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from qualitative_v2_production import ProductionV2Error  # noqa: E402
from qualitative_v2_production_acceptance import AcceptanceError, run_acceptance  # noqa: E402

DEFAULT_BASELINE = PROJECT_ROOT / "qualitative_v2_production_acceptance_baseline.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(config.DB_PATH))
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--daily-log", type=Path, default=Path(config.LOG_DIR) / "daily.log")
    parser.add_argument(
        "--require-score-date",
        help="Also require an exact 35-stock Framework A daily run and v2 adoption log evidence for YYYY-MM-DD.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    canary_codes = config.QUALITATIVE_V2_CANARY_CODES | config.QUALITATIVE_V2_PILOT_CODES
    try:
        report = run_acceptance(
            database_path=args.db,
            env_path=args.env_file,
            baseline_path=args.baseline,
            daily_log_path=args.daily_log,
            watchlist=config.WATCHLIST,
            canary_codes=canary_codes,
            required_score_date=args.require_score_date,
        )
    except (AcceptanceError, ProductionV2Error, OSError, sqlite3.Error, ValueError) as exc:
        report = {
            "schema_version": "qualitative-v2-production-acceptance-v1",
            "decision": "ROLLBACK",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    print(json.dumps(report, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0 if report["decision"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
