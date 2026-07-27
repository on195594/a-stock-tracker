from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_framework_b_cohort_freeze.py"
spec = importlib.util.spec_from_file_location("run_framework_b_cohort_freeze", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
auto_freeze = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = auto_freeze
spec.loader.exec_module(auto_freeze)


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], project_root: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        assert project_root.name
        return self.results.pop(0)


def _freeze_json(*, dry_run: bool, candidates: int, inserted: int, skipped: int, week: str = "2026-W31") -> str:
    return json.dumps(
        {
            "cohort_week": week,
            "label_date": "2026-07-27",
            "candidates": candidates,
            "inserted": inserted,
            "skipped_existing": skipped,
            "dry_run": dry_run,
        }
    )


def test_readiness_hold_stops_before_dry_run(tmp_path: Path) -> None:
    runner = FakeRunner([_completed(1, "HOLD_CRON")])

    with pytest.raises(auto_freeze.AutomationError, match="readiness"):
        auto_freeze.run_automation(label_date="2026-07-27", project_root=tmp_path, runner=runner)

    assert len(runner.calls) == 1
    assert runner.calls[0][1] == "scripts/check_market_data_readiness.py"


def test_zero_candidates_stops_before_execute(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            _completed(0, "READY_CRON"),
            _completed(0, _freeze_json(dry_run=True, candidates=0, inserted=0, skipped=0)),
        ]
    )

    with pytest.raises(auto_freeze.AutomationError, match="candidates"):
        auto_freeze.run_automation(label_date="2026-07-27", project_root=tmp_path, runner=runner)

    assert len(runner.calls) == 2


def test_invalid_dry_run_json_fails_closed(tmp_path: Path) -> None:
    runner = FakeRunner([_completed(0, "READY_CRON"), _completed(0, "not-json")])

    with pytest.raises(auto_freeze.AutomationError, match="JSON"):
        auto_freeze.run_automation(label_date="2026-07-27", project_root=tmp_path, runner=runner)

    assert len(runner.calls) == 2


def test_first_freeze_executes_after_matching_dry_run(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            _completed(0, "READY_CRON"),
            _completed(0, _freeze_json(dry_run=True, candidates=8, inserted=0, skipped=0)),
            _completed(0, _freeze_json(dry_run=False, candidates=8, inserted=8, skipped=0)),
        ]
    )

    result = auto_freeze.run_automation(label_date="2026-07-27", project_root=tmp_path, runner=runner)

    assert result == {
        "status": "completed",
        "cohort_week": "2026-W31",
        "label_date": "2026-07-27",
        "candidates": 8,
        "inserted": 8,
        "skipped_existing": 0,
        "idempotent_replay": False,
    }
    assert runner.calls[1][-3:] == ["--dry-run", "--label-date", "2026-07-27"]
    assert runner.calls[2][-2:] == ["--label-date", "2026-07-27"]


def test_same_week_idempotent_replay_is_success(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            _completed(0, "READY_CRON"),
            _completed(0, _freeze_json(dry_run=True, candidates=8, inserted=0, skipped=0)),
            _completed(0, _freeze_json(dry_run=False, candidates=8, inserted=0, skipped=8)),
        ]
    )

    result = auto_freeze.run_automation(label_date="2026-07-27", project_root=tmp_path, runner=runner)

    assert result["idempotent_replay"] is True
    assert result["inserted"] == 0
    assert result["skipped_existing"] == 8


@pytest.mark.parametrize(
    "execute_payload",
    [
        _freeze_json(dry_run=False, candidates=7, inserted=7, skipped=0),
        _freeze_json(dry_run=False, candidates=8, inserted=8, skipped=0, week="2026-W32"),
        _freeze_json(dry_run=False, candidates=8, inserted=3, skipped=3),
    ],
    ids=["candidate-mismatch", "week-mismatch", "partial-count"],
)
def test_execute_contract_mismatch_fails_closed(tmp_path: Path, execute_payload: str) -> None:
    runner = FakeRunner(
        [
            _completed(0, "READY_CRON"),
            _completed(0, _freeze_json(dry_run=True, candidates=8, inserted=0, skipped=0)),
            _completed(0, execute_payload),
        ]
    )

    with pytest.raises(auto_freeze.AutomationError, match="contract"):
        auto_freeze.run_automation(label_date="2026-07-27", project_root=tmp_path, runner=runner)
