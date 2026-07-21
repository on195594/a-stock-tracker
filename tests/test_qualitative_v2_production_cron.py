"""Managed cron and alert integration tests for qualitative-v2 acceptance."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

from scripts.check_qualitative_v2_production import resolve_required_score_date

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_require_today_resolves_without_shell_date_expansion() -> None:
    assert resolve_required_score_date(None, True, today=date(2026, 7, 20)) == "2026-07-20"
    assert resolve_required_score_date("2026-07-19", False, today=date(2026, 7, 20)) == "2026-07-19"
    assert resolve_required_score_date(None, False, today=date(2026, 7, 20)) is None


def test_managed_cron_places_acceptance_between_daily_and_outcome() -> None:
    setup = (PROJECT_ROOT / "cron-setup.sh").read_text(encoding="utf-8")

    assert 'PRIMARY_DAILY_RULE="15 17 * * 1-5 ' in setup
    assert 'DAILY_RULE="30 17 * * 1-5 ' in setup
    assert 'ACCEPTANCE_RULE="45 17 * * 1-5 ' in setup
    assert 'OUTCOME_RULE="00 18 * * 1-5 ' in setup
    assert "check_qualitative_v2_production.py --require-today" in setup
    assert "qualitative-v2-production-acceptance --alert-exit-2" in setup
    assert (
        setup.index("$PRIMARY_DAILY_RULE\n")
        < setup.index("$DAILY_RULE\n")
        < setup.index("$ACCEPTANCE_RULE\n")
        < setup.index("$OUTCOME_RULE\n")
    )
    assert "run_tushare_primary_production_cycle.py daily" in setup
    assert "run_tushare_primary_production_cycle.py weekly" in setup
    assert "PRESERVE_EXISTING_DAILY=0" in setup
    assert max(len(line) for line in setup.splitlines()) < 1000
    assert 'PROJECT_DIR="${A_STOCK_PROJECT_DIR:-$SCRIPT_DIR}"' in setup
    assert 'if [ "${1:-}" = "--rollback" ]' in setup
    assert 'crontab "$CRON_BACKUP_PATH"' in setup
    assert "/check_qualitative_v2_production\\.py/ { next }" in setup


def _run_alert_wrapper(tmp_path: Path, *, alert_exit_2: bool) -> tuple[subprocess.CompletedProcess[str], Path]:
    wrapper = tmp_path / "cron-alert-wrap.sh"
    shutil.copy2(PROJECT_ROOT / "cron-alert-wrap.sh", wrapper)
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=fake-token\nTELEGRAM_CHAT_ID=fake-chat\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "curl-args.txt"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE_FILE"\n', encoding="utf-8")
    fake_curl.chmod(0o700)
    command = ["bash", str(wrapper), "exit 2", "qualitative-v2-production-acceptance"]
    if alert_exit_2:
        command.append("--alert-exit-2")
    environment = dict(os.environ)
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["CAPTURE_FILE"] = str(capture)
    result = subprocess.run(command, capture_output=True, check=False, env=environment, text=True)
    return result, capture


def test_alert_wrapper_sends_rollback_alert_when_explicitly_enabled(tmp_path: Path) -> None:
    result, capture = _run_alert_wrapper(tmp_path, alert_exit_2=True)

    assert result.returncode == 2
    alert = capture.read_text(encoding="utf-8")
    assert "ROLLBACK" in alert
    assert "qualitative-v2-production-acceptance" in alert
    assert "fake-token" not in result.stdout + result.stderr


def test_alert_wrapper_keeps_existing_exit_two_suppression_by_default(tmp_path: Path) -> None:
    result, capture = _run_alert_wrapper(tmp_path, alert_exit_2=False)

    assert result.returncode == 2
    assert not capture.exists()
