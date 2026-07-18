#!/usr/bin/env python3
"""Capture query-controller assets linked by successful D1 source pages."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qualitative_v2_m5_source_frontdoors import (  # noqa: E402
    SourceFrontdoorError,
    capture_source_ui_assets,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture authorization-bound official query-controller assets.")
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument(
        "--execute-network",
        action="store_true",
        help="Fetch exactly the controller assets named by the sealed official pages",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.execute_network:
            raise SourceFrontdoorError("UI-asset capture requires --execute-network; no request was sent")
        result = capture_source_ui_assets(args.sample, args.authorization, args.checksum)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0 if result["technical_error_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
