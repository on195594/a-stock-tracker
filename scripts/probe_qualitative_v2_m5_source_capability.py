#!/usr/bin/env python3
"""Build or seal the offline M5 v1.2 source-capability report."""

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

from qualitative_v2_m5 import _canonical_json  # noqa: E402
from qualitative_v2_m5_capability_probe import (  # noqa: E402
    CapabilityProbeError,
    capability_report,
    seal_capability_report,
)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--frontdoors", required=True, type=Path)
    parser.add_argument("--ui-assets", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe sealed M5 source capability without external access.")
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview", help="Print the deterministic report and hash; create nothing")
    _common(preview)
    seal = commands.add_parser("seal", help="Publish one authorization-bound create-only report")
    _common(seal)
    seal.add_argument("--output", required=True, type=Path)
    return parser


def _print(value: object) -> None:
    print(json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preview":
            report = capability_report(
                args.sample,
                args.authorization,
                args.checksum,
                args.frontdoors,
                args.ui_assets,
            )
            raw = (_canonical_json(report) + "\n").encode()
            _print({"report": report, "report_sha256": hashlib.sha256(raw).hexdigest()})
            return 0
        if args.command == "seal":
            _print(
                seal_capability_report(
                    args.sample,
                    args.authorization,
                    args.checksum,
                    args.frontdoors,
                    args.ui_assets,
                    args.output,
                )
            )
            return 0
        raise CapabilityProbeError(f"unknown command: {args.command}")
    except (OSError, ValueError, json.JSONDecodeError, CapabilityProbeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
