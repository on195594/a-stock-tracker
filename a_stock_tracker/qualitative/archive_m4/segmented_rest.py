"""Public M4 v1.3.1 segmented REST API and minimal CLI."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from a_stock_tracker.qualitative.archive_m4.segmented_core import (
    AttemptResult,
    CallReceipt,
    DateEvidenceAuthorization,
    FrameAuthorization,
    SegmentedRestCall,
    VerificationError,
    date_evidence_call_matrix,
    frame_call_matrix,
    load_authorization,
)
from a_stock_tracker.qualitative.archive_m4.segmented_runtime import execute_supervised
from a_stock_tracker.qualitative.archive_m4.segmented_verify import verify_segmented_rest_attempt


def execute_segmented_rest_attempt(
    *, authorization_path: str | Path, authorization_hash_path: str | Path
) -> AttemptResult:
    """Execute exactly one externally authorized attempt under the private supervisor."""
    return execute_supervised(Path(authorization_path), Path(authorization_hash_path))


def require_capture_input_eligible(attempt_dir: str | Path) -> AttemptResult:
    """Return only a post-publication verified, formally eligible capture."""
    result = verify_segmented_rest_attempt(attempt_dir)
    if (
        result.attempt_kind != "capture"
        or not result.complete
        or not result.overall_pass
        or not result.capture_input_eligible
        or result.disposition != "FRAME_CAPTURE_ELIGIBLE"
    ):
        raise VerificationError("capture_input_not_eligible")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qualitative-v2-m4-segmented-rest", allow_abbrev=False)
    subcommands = parser.add_subparsers(dest="command", required=True)
    execute = subcommands.add_parser("execute", allow_abbrev=False)
    execute.add_argument("--authorization", required=True, type=Path)
    execute.add_argument("--authorization-sha256", required=True, type=Path)
    verify = subcommands.add_parser("verify", allow_abbrev=False)
    verify.add_argument("--attempt-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the exact execute/verify CLI with protocol-defined exit statuses."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "execute":
            result = execute_segmented_rest_attempt(
                authorization_path=args.authorization,
                authorization_hash_path=args.authorization_sha256,
            )
        else:
            result = verify_segmented_rest_attempt(args.attempt_dir)
    except Exception as exc:
        print(f"BLOCKED: {getattr(exc, 'code', type(exc).__name__)}")
        return 2
    print(f"{result.disposition}: {result.manifest_sha256}")
    return 0 if result.overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AttemptResult",
    "CallReceipt",
    "DateEvidenceAuthorization",
    "FrameAuthorization",
    "SegmentedRestCall",
    "date_evidence_call_matrix",
    "execute_segmented_rest_attempt",
    "frame_call_matrix",
    "load_authorization",
    "require_capture_input_eligible",
    "verify_segmented_rest_attempt",
]
