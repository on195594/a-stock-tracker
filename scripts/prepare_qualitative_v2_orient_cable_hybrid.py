#!/usr/bin/env python3
"""Materialize the approved 603606 hybrid context from sealed local evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qualitative_v2_orient_cable_hybrid import (  # noqa: E402
    HybridAuthorizationError,
    build_authorized_hybrid_context,
    load_active_hybrid_authorization,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the authorized source-network-free 603606 hybrid context.")
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--execute-local", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.execute_local:
            raise HybridAuthorizationError("--execute-local is required; no artifact was created")
        authorization = load_active_hybrid_authorization(PROJECT_ROOT, args.authorization_id)
        result = build_authorized_hybrid_context(PROJECT_ROOT, authorization)
    except (OSError, HybridAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
