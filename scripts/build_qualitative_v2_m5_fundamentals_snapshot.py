#!/usr/bin/env python3
"""Build the authorization-bound read-only M5 fundamentals snapshot."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_tracker.qualitative.m5.fundamentals_snapshot import (  # noqa: E402
    FundamentalsSnapshotError,
    build_fundamentals_snapshot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build one D1-authorized read-only fundamentals snapshot.")
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--execute-read-only",
        action="store_true",
        help="Open only the authorization-bound tracker.db in SQLite mode=ro/query_only",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.execute_read_only:
            raise FundamentalsSnapshotError("snapshot requires --execute-read-only; database was not opened")
        result = build_fundamentals_snapshot(
            args.sample,
            args.authorization,
            args.checksum,
            args.output,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
