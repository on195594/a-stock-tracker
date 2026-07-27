#!/usr/bin/env python3
"""Fail-closed weekly automation for Framework B report-only cohort freezing."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT_S = 180

CommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


class AutomationError(RuntimeError):
    """Raised when the automatic freeze cannot prove its safety contract."""


def _run_command(args: list[str], project_root: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise AutomationError(f"command timeout: {args[1]}") from exc


def _require_success(result: subprocess.CompletedProcess[str], stage: str, marker: str | None = None) -> None:
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())[:300]
        raise AutomationError(f"{stage} failed: {detail or f'exit={result.returncode}'}")
    if marker is not None and marker not in result.stdout:
        raise AutomationError(f"{stage} missing marker: {marker}")


def _parse_freeze_json(result: subprocess.CompletedProcess[str], stage: str) -> dict[str, Any]:
    _require_success(result, stage)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AutomationError(f"{stage} JSON output missing")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise AutomationError(f"{stage} JSON output invalid") from exc
    if not isinstance(payload, dict):
        raise AutomationError(f"{stage} JSON contract invalid")
    required = {
        "cohort_week": str,
        "label_date": str,
        "candidates": int,
        "inserted": int,
        "skipped_existing": int,
        "dry_run": bool,
    }
    for key, expected_type in required.items():
        value = payload.get(key)
        if not isinstance(value, expected_type) or (expected_type is int and isinstance(value, bool)):
            raise AutomationError(f"{stage} JSON contract invalid: {key}")
    return payload


def _validate_dry_run(payload: dict[str, Any], label_date: str) -> None:
    if payload["dry_run"] is not True:
        raise AutomationError("dry-run contract mismatch: dry_run")
    if payload["label_date"] != label_date:
        raise AutomationError("dry-run contract mismatch: label_date")
    if payload["candidates"] <= 0:
        raise AutomationError("dry-run candidates must be greater than zero")
    if payload["inserted"] != 0 or payload["skipped_existing"] != 0:
        raise AutomationError("dry-run contract mismatch: write counters")


def _validate_execute(dry: dict[str, Any], executed: dict[str, Any], label_date: str) -> None:
    valid = (
        executed["dry_run"] is False
        and executed["label_date"] == label_date
        and executed["cohort_week"] == dry["cohort_week"]
        and executed["candidates"] == dry["candidates"]
        and executed["inserted"] + executed["skipped_existing"] == executed["candidates"]
    )
    if not valid:
        raise AutomationError("execute contract mismatch")


def run_automation(
    *,
    label_date: str,
    project_root: Path = PROJECT_ROOT,
    runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    date.fromisoformat(label_date)
    python = sys.executable

    readiness = runner(
        [
            python,
            "scripts/check_market_data_readiness.py",
            "--scope",
            "cron",
            "--allow-stale-days",
            "9",
        ],
        project_root,
    )
    _require_success(readiness, "readiness", "READY_CRON")

    dry_result = runner(
        [
            python,
            "pipeline.py",
            "framework-b-cohort-freeze",
            "--dry-run",
            "--label-date",
            label_date,
        ],
        project_root,
    )
    dry = _parse_freeze_json(dry_result, "dry-run")
    _validate_dry_run(dry, label_date)

    execute_result = runner(
        [python, "pipeline.py", "framework-b-cohort-freeze", "--label-date", label_date],
        project_root,
    )
    executed = _parse_freeze_json(execute_result, "execute")
    _validate_execute(dry, executed, label_date)

    inserted = int(executed["inserted"])
    skipped = int(executed["skipped_existing"])
    return {
        "status": "completed",
        "cohort_week": executed["cohort_week"],
        "label_date": label_date,
        "candidates": int(executed["candidates"]),
        "inserted": inserted,
        "skipped_existing": skipped,
        "idempotent_replay": inserted == 0 and skipped == int(executed["candidates"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Automatically freeze one weekly Framework B report-only cohort.")
    parser.add_argument("--label-date", default=date.today().isoformat())
    args = parser.parse_args(argv)
    try:
        summary = run_automation(label_date=args.label_date)
    except (AutomationError, ValueError) as exc:
        print(f"framework_b_cohort_auto_freeze_failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
