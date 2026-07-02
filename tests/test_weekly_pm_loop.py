from __future__ import annotations

import importlib.util
import os
import subprocess
from datetime import datetime
from pathlib import Path
import sys

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "weekly_pm_loop.py"
spec = importlib.util.spec_from_file_location("weekly_pm_loop", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
weekly_pm_loop = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = weekly_pm_loop
spec.loader.exec_module(weekly_pm_loop)


REPORT_OK = """
── Phase 6 readiness ──
Phase 6 生产化阻塞项：无
Phase 6 下一步：继续 report-only
结论：可以进入 Phase 6 report-only 深化；仍不要启用生产写入。
"""

REPORT_WARN = """
── Phase 6 readiness ──
Phase 6 生产化阻塞项：B label 已结案样本不足
Phase 6 下一步：等待自然结案
结论：暂不进入 Phase 6 生产化；继续 report-only。
"""


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def block_unmocked_subprocess(monkeypatch):
    def fail_run(*args, **kwargs):
        raise AssertionError(f"unmocked subprocess.run: {args!r} {kwargs!r}")

    monkeypatch.setattr(weekly_pm_loop.subprocess, "run", fail_run)


def _write_log(project_root: Path, name: str, text: str, *, mtime: datetime) -> None:
    path = project_root / "logs" / name
    path.write_text(text, encoding="utf-8")
    timestamp = mtime.timestamp()
    os.utime(path, (timestamp, timestamp))


def _fresh_logs(project_root: Path, now: datetime) -> None:
    for name in ("weekly.log", "daily.log", "outcome.log"):
        _write_log(
            project_root,
            name,
            f"{now:%Y-%m-%d} 09:00:00 INFO {name} ok\n",
            mtime=now,
        )


def _mock_commands(monkeypatch, *, readiness: str = "READY_CRON", report: str = REPORT_OK) -> None:
    def fake_run(args, **kwargs):
        command = " ".join(args)
        if "check_market_data_readiness.py" in command:
            assert "--allow-stale-days" in args
            assert "9" in args
            return subprocess.CompletedProcess(args, 0, readiness, "")
        if args[-2:] == ["pipeline.py", "accuracy-report"]:
            return subprocess.CompletedProcess(args, 0, report, "")
        raise AssertionError(f"unexpected subprocess args: {args!r}")

    monkeypatch.setattr(weekly_pm_loop.subprocess, "run", fake_run)


def _freeze_now(monkeypatch, now: datetime) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(weekly_pm_loop, "datetime", FrozenDateTime)


def test_run_loop_ok_with_mocked_subprocess(project_root: Path, monkeypatch) -> None:
    now = datetime(2026, 7, 6, 9, 30)
    _fresh_logs(project_root, now)
    _mock_commands(monkeypatch, report=REPORT_OK)

    status, summary = weekly_pm_loop.run_loop(project_root=project_root, now=now)

    assert status == "OK"
    assert "readiness: OK - READY_CRON" in summary
    assert "accuracy-report: OK" in summary


def test_log_check_only_fails_on_latest_dated_lines(project_root: Path) -> None:
    now = datetime(2026, 7, 6, 9, 30)
    _write_log(
        project_root,
        "daily.log",
        "\n".join(
            [
                "2026-07-01 16:30:00 ERROR old failure",
                "2026-07-04 16:30:00 INFO recovered",
            ]
        ),
        mtime=now,
    )

    result = weekly_pm_loop.check_log(
        "daily.log",
        max_age=weekly_pm_loop.timedelta(days=4),
        now=now,
        project_root=project_root,
    )

    assert result.status == "OK"


def test_log_check_does_not_treat_failed_zero_metric_as_failure(project_root: Path) -> None:
    now = datetime(2026, 7, 6, 9, 30)
    _write_log(
        project_root,
        "daily.log",
        "2026-07-06 16:30:00 INFO L3 行情刷新：ok=35 degraded=0 failed=0 total=35\n",
        mtime=now,
    )

    result = weekly_pm_loop.check_log(
        "daily.log",
        max_age=weekly_pm_loop.timedelta(days=4),
        now=now,
        project_root=project_root,
    )

    assert result.status == "OK"


def test_accuracy_report_warns_when_conclusion_lacks_restrictive_marker() -> None:
    report = """
── Phase 6 readiness ──
Phase 6 生产化阻塞项：无
Phase 6 下一步：人工复核
结论：可以进入 Phase 6 生产化。
"""

    result = weekly_pm_loop.extract_phase6_summary(report)

    assert result.status == "WARN"
    assert "production_guard=missing_restrictive_marker" in result.detail


def test_main_returns_two_for_business_warning_after_telegram_sent(
    project_root: Path,
    monkeypatch,
    capsys,
) -> None:
    now = datetime(2026, 7, 6, 9, 30)
    _fresh_logs(project_root, now)
    _mock_commands(monkeypatch, readiness="HOLD_CRON", report=REPORT_WARN)
    monkeypatch.setattr(weekly_pm_loop, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(weekly_pm_loop, "send_telegram", lambda text, *, project_root: "telegram_sent")
    _freeze_now(monkeypatch, now)

    assert weekly_pm_loop.main([]) == 2

    out = capsys.readouterr().out
    assert "telegram_sent" in out
    assert (project_root / "logs" / "weekly-pm-loop-summary.txt").exists()


def test_dry_run_no_telegram_does_not_require_credentials(
    project_root: Path,
    monkeypatch,
    capsys,
) -> None:
    now = datetime(2026, 7, 6, 9, 30)
    _fresh_logs(project_root, now)
    _mock_commands(monkeypatch, report=REPORT_OK)
    monkeypatch.setattr(weekly_pm_loop, "PROJECT_ROOT", project_root)
    _freeze_now(monkeypatch, now)

    assert weekly_pm_loop.main(["--dry-run", "--no-telegram"]) == 0

    out = capsys.readouterr().out
    assert "Phase 6 weekly PM loop" in out
    assert "telegram_disabled" in out
