#!/usr/bin/env python3
"""Prepare or verify the bounded M5 data-readiness authorization."""

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

from a_stock_tracker.qualitative.m5.data_auth import (  # noqa: E402
    DataAuthorizationError,
    authorization_value,
    preflight_data_authorization,
    seal_data_authorization,
)


def _window_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--data-run-id", required=True)
    parser.add_argument("--not-before", required=True)
    parser.add_argument("--not-after", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the zero-call M5 D1 authorization boundary.")
    commands = parser.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview", help="Print canonical authorization and digest; create nothing")
    _window_arguments(preview)

    seal = commands.add_parser("seal", help="Create one authorization/checksum pair after user approval")
    _window_arguments(seal)
    seal.add_argument("--output", required=True, type=Path)
    seal.add_argument("--checksum-output", required=True, type=Path)

    preflight = commands.add_parser("preflight", help="Validate a sealed authorization without external access")
    preflight.add_argument("--sample", required=True, type=Path)
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
                authorization_id=args.authorization_id,
                data_run_id=args.data_run_id,
                not_before=args.not_before,
                not_after=args.not_after,
            )
            raw = (
                json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
            ).encode()
            _print({"authorization": value, "authorization_sha256": hashlib.sha256(raw).hexdigest()})
            return 0
        if args.command == "seal":
            authorization = seal_data_authorization(
                args.sample,
                args.output,
                args.checksum_output,
                authorization_id=args.authorization_id,
                data_run_id=args.data_run_id,
                not_before=args.not_before,
                not_after=args.not_after,
            )
            _print({"authorization_id": authorization.authorization_id, "authorization_sha256": authorization.sha256})
            return 0
        if args.command == "preflight":
            _print(
                preflight_data_authorization(
                    args.sample,
                    args.authorization,
                    args.checksum,
                    require_active=args.require_active,
                )
            )
            return 0
        raise DataAuthorizationError(f"unknown command: {args.command}")
    except (OSError, ValueError, json.JSONDecodeError, DataAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
