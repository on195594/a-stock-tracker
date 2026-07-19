#!/usr/bin/env python3
"""Run or offline-verify the M4 v1.2 non-adoptable frame-source probe."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_tracker.qualitative.archive_m4.capability import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    CapabilityAuthorizationError,
    CapabilityError,
    probe_m4_frame_source,
    verify_capability_probe,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe = subparsers.add_parser("probe", help="execute the exact authorized three-call matrix once")
    probe.add_argument("--authorization", type=Path, required=True)
    probe.add_argument("--authorization-sha256", type=Path, required=True)
    probe.add_argument("--attempt-id", required=True)
    probe.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    verify = subparsers.add_parser("verify", help="verify a sealed probe with no network or token access")
    verify.add_argument("--attempt-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "verify":
            result = verify_capability_probe(args.attempt_dir)
        else:
            result = probe_m4_frame_source(
                authorization_path=args.authorization,
                authorization_hash_path=args.authorization_sha256,
                attempt_id=args.attempt_id,
                output_root=args.output_root,
            )
    except CapabilityAuthorizationError as exc:
        print(f"M4_CAPABILITY_NOT_EXECUTED: {exc.error_code}", file=sys.stderr)
        return 2
    except CapabilityError as exc:
        print(f"M4_CAPABILITY_BLOCKED: {exc.error_code}", file=sys.stderr)
        return 2
    except Exception:
        print("M4_CAPABILITY_BLOCKED: unexpected_internal_error", file=sys.stderr)
        return 2
    print(f"attempt={result.attempt_dir}")
    print(f"manifest_sha256={result.manifest_sha256}")
    print(f"complete={str(result.complete).lower()}")
    print(f"capability_pass={str(result.capability_pass).lower()}")
    print(f"terminal_state={result.terminal_state}")
    return 0 if result.capability_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
