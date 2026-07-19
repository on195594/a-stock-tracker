#!/usr/bin/env python3
"""Preview or execute a bounded qualitative-v2 production batch."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from lib.cache import get_db  # noqa: E402
from qualitative_v2_client import DEFAULT_GEMINI_MODEL  # noqa: E402
from qualitative_v2_production import (  # noqa: E402
    ProductionV2Error,
    context_missing_score_dimensions,
    promote_shadow_record,
)
from qualitative_v2_shadow import run_shadow_evaluation  # noqa: E402
from qualitative_v2_types import QualitativeContext  # noqa: E402
from qualitative_v2_validator import validate_context_dict  # noqa: E402

MAX_CONTEXT_BYTES = 1_048_576
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


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


def _target_companies(scope: str) -> dict[str, str]:
    watchlist = {str(item["code"]): str(item["name"]) for item in config.WATCHLIST}
    if len(watchlist) != len(config.WATCHLIST):
        raise ProductionV2Error("production watchlist contains duplicate codes")
    if scope == "all":
        return watchlist
    missing = config.QUALITATIVE_V2_CANARY_CODES - watchlist.keys()
    if missing:
        raise ProductionV2Error(f"configured canary codes are outside the watchlist: {sorted(missing)}")
    return {code: watchlist[code] for code in sorted(config.QUALITATIVE_V2_CANARY_CODES)}


def _load_contexts(directory: Path, expected: dict[str, str]) -> list[QualitativeContext]:
    if directory.is_symlink() or not directory.is_dir():
        raise ProductionV2Error("contexts must be a regular directory, not a symlink")
    paths = sorted(directory.glob("*.json"))
    contexts: dict[str, QualitativeContext] = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ProductionV2Error(f"context path is not a regular file: {path.name}")
        if path.stat().st_size > MAX_CONTEXT_BYTES:
            raise ProductionV2Error(f"context exceeds {MAX_CONTEXT_BYTES} bytes: {path.name}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProductionV2Error(f"context is not valid JSON: {path.name}") from exc
        if not isinstance(raw, dict):
            raise ProductionV2Error(f"context must contain one object: {path.name}")
        validation = validate_context_dict(cast(dict[str, object], raw))
        if not validation.valid or validation.context is None:
            raise ProductionV2Error(f"invalid context {path.name}: {validation.rejection_reason}")
        context = validation.context
        if context.code in contexts:
            raise ProductionV2Error(f"duplicate context code: {context.code}")
        if expected.get(context.code) != context.name:
            raise ProductionV2Error(
                f"context identity is outside the exact {len(expected)}-stock scope: {context.code}"
            )
        contexts[context.code] = context
    if set(contexts) != set(expected):
        missing = sorted(set(expected) - set(contexts))
        extra = sorted(set(contexts) - set(expected))
        raise ProductionV2Error(f"contexts must match the exact scope; missing={missing}, extra={extra}")
    return [contexts[code] for code in sorted(contexts)]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or explicitly score the real-watchlist qualitative-v2 production scope."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preview", "score"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--contexts", required=True, type=Path)
        subparser.add_argument("--scope", required=True, choices=("canary", "all"))
        if command == "score":
            subparser.add_argument("--run-id", required=True)
            subparser.add_argument(
                "--execute",
                action="store_true",
                help="Read GEMINI_API_KEY, call the fixed model, and write validated rows to the separate v2 table",
            )
    return parser


def _preview(contexts: list[QualitativeContext], scope: str) -> dict[str, object]:
    missing = {context.code: list(context_missing_score_dimensions(context)) for context in contexts}
    return {
        "codes": [context.code for context in contexts],
        "execution_enabled": False,
        "fixed_model": DEFAULT_GEMINI_MODEL,
        "logical_call_limit": len(contexts),
        "maximum_http_attempts": len(contexts) * 3,
        "missing_score_dimensions": missing,
        "score_ready": not any(missing.values()),
        "scope": scope,
        "validated": True,
    }


def _create_run_root(run_id: str) -> Path:
    base = PROJECT_ROOT / "artifacts" / "qualitative-v2-production"
    cursor = PROJECT_ROOT
    for part in base.relative_to(PROJECT_ROOT).parts:
        cursor /= part
        if cursor.is_symlink():
            raise ProductionV2Error("production artifact ancestry contains a symlink")
        if cursor.exists() and not cursor.is_dir():
            raise ProductionV2Error("production artifact ancestry contains a non-directory")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    run_root = base / run_id
    if run_root.exists() or run_root.is_symlink():
        raise ProductionV2Error("run-id is create-only; use a new run-id")
    run_root.mkdir(mode=0o700)
    return run_root


def _execute(contexts: list[QualitativeContext], scope: str, run_id: str) -> tuple[dict[str, object], bool]:
    if RUN_ID_RE.fullmatch(run_id) is None or run_id in {".", ".."}:
        raise ProductionV2Error("run-id must be a bounded safe identifier")
    _load_env_file(PROJECT_ROOT / ".env")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ProductionV2Error("GEMINI_API_KEY is not set")

    run_root = _create_run_root(run_id)
    output = run_root / "gemini.jsonl"

    expected_codes = frozenset(context.code for context in contexts)
    outcomes: list[dict[str, object]] = []
    fully_scored = 0
    db = get_db()
    try:
        for context in contexts:
            api_called = False
            try:
                outcome = run_shadow_evaluation(
                    context,
                    api_key=api_key,
                    output_path=output,
                    model=DEFAULT_GEMINI_MODEL,
                )
                api_called = outcome.api_called
                status = str(outcome.record["validation_status"])
                promoted = False
                if status in {"VALID_SCORED", "VALID_INSUFFICIENT_DATA"}:
                    promoted = promote_shadow_record(db, outcome.record, expected_codes=expected_codes)
                    db.commit()
                if status == "VALID_SCORED":
                    fully_scored += 1
                outcomes.append(
                    {
                        "api_called": outcome.api_called,
                        "code": context.code,
                        "promoted": promoted,
                        "validation_status": status,
                    }
                )
            except Exception as exc:
                db.rollback()
                outcomes.append(
                    {
                        "api_called": api_called,
                        "code": context.code,
                        "error_class": type(exc).__name__,
                        "promoted": False,
                        "validation_status": "LOCAL_FAILURE",
                    }
                )
    finally:
        db.close()
    complete = fully_scored == len(contexts)
    return (
        {
            "fixed_model": DEFAULT_GEMINI_MODEL,
            "fully_scored": fully_scored,
            "outcomes": outcomes,
            "run_id": run_id,
            "scope": scope,
            "status": "READY" if complete else "DEGRADED",
            "total": len(contexts),
        },
        complete,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        expected = _target_companies(args.scope)
        contexts = _load_contexts(args.contexts, expected)
        if args.command == "preview":
            result = _preview(contexts, args.scope)
            success = True
        else:
            if not args.execute:
                raise ProductionV2Error(
                    "score requires --execute; no credential, artifact, database, or model was used"
                )
            missing = {context.code: context_missing_score_dimensions(context) for context in contexts}
            if any(missing.values()):
                raise ProductionV2Error(f"deterministic evidence gates cannot score the exact scope: {missing}")
            result, success = _execute(contexts, args.scope, args.run_id)
    except (OSError, ProductionV2Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True))
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
