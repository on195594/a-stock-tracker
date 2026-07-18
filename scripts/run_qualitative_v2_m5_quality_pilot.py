#!/usr/bin/env python3
"""Execute one authorized local-date attempt of the M5 v1.2 structural-quality pilot."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qualitative_v2_m5_quality_pilot import (  # noqa: E402
    QualityPilotError,
    load_quality_pilot_report,
    run_quality_pilot_day,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one authorized day of the CNINFO structural-quality pilot.")
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--capability-report", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument(
        "--execute-network",
        action="store_true",
        help="Perform the bounded CNINFO HTTPS requests for the current authorized local date",
    )
    parser.add_argument(
        "--verify-report",
        action="store_true",
        help="Rebuild and verify the terminal report without external access",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.execute_network == args.verify_report:
            raise QualityPilotError("select exactly one of --execute-network or --verify-report; no request was sent")
        if args.verify_report:
            result = load_quality_pilot_report(
                args.sample,
                args.capability_report,
                args.authorization,
                args.checksum,
            )
        else:
            result = run_quality_pilot_day(
                args.sample,
                args.capability_report,
                args.authorization,
                args.checksum,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
