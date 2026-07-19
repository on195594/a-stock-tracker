#!/usr/bin/env python3
"""Explicit, file-isolated entrypoint for qualitative-v2 shadow evaluation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_tracker.qualitative.client import DEFAULT_GEMINI_MODEL  # noqa: E402
from a_stock_tracker.qualitative.shadow import run_shadow_evaluation  # noqa: E402
from a_stock_tracker.qualitative.validator import validate_context_dict  # noqa: E402

MAX_INPUT_BYTES = 1_048_576
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "qualitative_v2_shadow.jsonl"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _load_json_object(path: Path, *, maximum_bytes: int = MAX_INPUT_BYTES) -> dict[str, object]:
    if path.stat().st_size > maximum_bytes:
        raise ValueError(f"input {path} exceeds {maximum_bytes} bytes")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"input {path} must contain one JSON object")
    return cast(dict[str, object], raw)


def _load_legacy_scores(path: Path | None) -> dict[str, int] | None:
    if path is None:
        return None
    raw = _load_json_object(path, maximum_bytes=4096)
    return cast(dict[str, int], raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a qualitative-v2 context and optionally execute one isolated Gemini shadow call."
    )
    parser.add_argument("--context", required=True, type=Path, help="Validated evidence context JSON file")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Isolated JSONL artifact path")
    parser.add_argument("--legacy-scores", type=Path, help="Optional JSON file with v1 moat/market_pos/sentiment")
    parser.add_argument("--model", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually call Gemini; without this flag the command only validates and previews",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        raw_context = _load_json_object(args.context)
        validation = validate_context_dict(raw_context)
        if not validation.valid or validation.context is None:
            raise ValueError(f"invalid context: {validation.rejection_reason}")
        context = validation.context
        legacy_scores = _load_legacy_scores(args.legacy_scores)

        if not args.execute:
            print(
                json.dumps(
                    {
                        "code": context.code,
                        "execution_enabled": False,
                        "input_hash": context.compute_input_hash(),
                        "model": args.model,
                        "output": str(args.output),
                        "validated": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        _load_env_file(PROJECT_ROOT / ".env")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        outcome = run_shadow_evaluation(
            context,
            api_key=api_key,
            output_path=args.output,
            model=args.model,
            legacy_scores=legacy_scores,
        )
        print(
            json.dumps(
                {
                    "api_called": outcome.api_called,
                    "code": context.code,
                    "input_hash": context.compute_input_hash(),
                    "output": str(args.output),
                    "persisted": outcome.persisted,
                    "validation_status": outcome.record["validation_status"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
