#!/usr/bin/env python3
"""Prepare or verify the bounded M5 v1.2 evidence-quality pilot authorization."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qualitative_v2_m5_quality_pilot_auth import (  # noqa: E402
    QualityPilotAuthorizationError,
    authorization_value,
    preflight_quality_pilot_authorization,
    seal_quality_pilot_authorization,
)


def _proposal_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--capability-report", required=True, type=Path)
    parser.add_argument("--capability-report-sha256", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--pilot-run-id", required=True)
    parser.add_argument("--not-before", required=True)
    parser.add_argument("--not-after", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the zero-call M5 v1.2 quality-pilot boundary.")
    commands = parser.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview", help="Print canonical proposal and digest; create nothing")
    _proposal_arguments(preview)

    seal = commands.add_parser("seal", help="Create one authorization/checksum pair after exact-SHA approval")
    _proposal_arguments(seal)
    seal.add_argument("--output", required=True, type=Path)
    seal.add_argument("--checksum-output", required=True, type=Path)

    preflight = commands.add_parser("preflight", help="Validate a sealed authorization without external access")
    preflight.add_argument("--sample", required=True, type=Path)
    preflight.add_argument("--capability-report", required=True, type=Path)
    preflight.add_argument("--authorization", required=True, type=Path)
    preflight.add_argument("--checksum", required=True, type=Path)
    preflight.add_argument("--require-active", action="store_true")
    return parser


def _print(value: object) -> None:
    print(json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preview":
            value = authorization_value(
                args.sample,
                args.capability_report,
                capability_report_sha256=args.capability_report_sha256,
                authorization_id=args.authorization_id,
                pilot_run_id=args.pilot_run_id,
                not_before=args.not_before,
                not_after=args.not_after,
            )
            raw = (
                json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
            ).encode()
            _print({"authorization": value, "authorization_sha256": hashlib.sha256(raw).hexdigest()})
            return 0
        if args.command == "seal":
            authorization = seal_quality_pilot_authorization(
                args.sample,
                args.capability_report,
                args.output,
                args.checksum_output,
                capability_report_sha256=args.capability_report_sha256,
                authorization_id=args.authorization_id,
                pilot_run_id=args.pilot_run_id,
                not_before=args.not_before,
                not_after=args.not_after,
            )
            _print({"authorization_id": authorization.authorization_id, "authorization_sha256": authorization.sha256})
            return 0
        if args.command == "preflight":
            _print(
                preflight_quality_pilot_authorization(
                    args.sample,
                    args.capability_report,
                    args.authorization,
                    args.checksum,
                    require_active=args.require_active,
                )
            )
            return 0
        raise QualityPilotAuthorizationError(f"unknown command: {args.command}")
    except (OSError, ValueError, json.JSONDecodeError, QualityPilotAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
