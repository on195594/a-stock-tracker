#!/usr/bin/env python3
"""Credential-free fixture-first CLI for the M5 bounded shadow batch."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qualitative_v2_m5 import (  # noqa: E402
    load_report,
    preview_bundle,
    run_blind_review,
    run_shadow,
    synthetic_gemini_client,
    synthetic_reference_client,
    synthetic_support_client,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and run the credential-free synthetic M5 fixture-first batch."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview", help="Validate sample/bundle and print aggregate JSON only")
    preview.add_argument("--sample", required=True, type=Path)
    preview.add_argument("--bundle", required=True, type=Path)

    blind = subparsers.add_parser("blind-review", help="Seal 36 synthetic machine-blind references")
    blind.add_argument("--sample", required=True, type=Path)
    blind.add_argument("--bundle", required=True, type=Path)
    blind.add_argument("--run-root", required=True, type=Path)
    blind.add_argument("--claude-model", required=True)
    blind.add_argument(
        "--execute-claude",
        action="store_true",
        help="Execute the local synthetic fake; real Claude execution remains separately unauthorized",
    )

    shadow = subparsers.add_parser("shadow", help="Run synthetic Gemini fake and revealed support-audit fake")
    shadow.add_argument("--from-run", required=True, type=Path)
    shadow.add_argument(
        "--execute-gemini",
        action="store_true",
        help="Execute local synthetic fakes; real Gemini/Claude calls remain separately unauthorized",
    )

    report = subparsers.add_parser("report", help="Read a sealed aggregate report")
    report.add_argument("--from-run", required=True, type=Path)
    return parser


def _print_json(value: object) -> None:
    print(json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preview":
            _print_json(preview_bundle(args.sample, args.bundle))
            return 0
        if args.command == "blind-review":
            if not args.execute_claude:
                raise ValueError("blind-review requires --execute-claude; no artifact was created")
            summary = run_blind_review(
                args.sample,
                args.bundle,
                args.run_root,
                claude_model=args.claude_model,
                client=synthetic_reference_client,
            )
            _print_json(summary)
            return 0 if summary["terminal_status"] is None else 2
        if args.command == "shadow":
            if not args.execute_gemini:
                raise ValueError("shadow requires --execute-gemini; no external service is called")
            summary = run_shadow(
                args.from_run,
                gemini_client=synthetic_gemini_client,
                support_client=synthetic_support_client,
            )
            _print_json(summary)
            return 0 if summary["terminal_status"] in ("PASS", "PROVISIONAL") else 2
        if args.command == "report":
            _print_json(load_report(args.from_run))
            return 0
        raise ValueError(f"unknown command: {args.command}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
