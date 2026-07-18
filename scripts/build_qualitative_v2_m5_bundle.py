#!/usr/bin/env python3
"""Build one offline, create-only real M5 static bundle."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qualitative_v2_m5_bundle import build_real_bundle  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and self-validate a credential-free real M5 bundle from approved static inputs."
    )
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path, help="Real bundle input manifest")
    parser.add_argument("--output", required=True, type=Path, help="New create-only bundle root")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_real_bundle(args.sample, args.input, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
