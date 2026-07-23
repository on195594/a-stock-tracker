"""Managed cron and alert integration tests for qualitative-v2 acceptance."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

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
    assert "-m scripts.run_tushare_primary_production_cycle daily" in setup
    assert "-m scripts.run_tushare_primary_production_cycle weekly" in setup
    assert "PRESERVE_EXISTING_DAILY=0" in setup
    assert max(len(line) for line in setup.splitlines()) < 1000
    assert 'PROJECT_DIR="${A_STOCK_PROJECT_DIR:-$SCRIPT_DIR}"' in setup
    assert 'if [ "${1:-}" = "--rollback" ]' in setup
    assert 'crontab "$CRON_BACKUP_PATH"' in setup
    assert "/check_qualitative_v2_production\\.py/ { next }" in setup


def _run_alert_wrapper(
    tmp_path: Path, *, alert_exit_2: bool, option_before_label: bool = False
) -> tuple[subprocess.CompletedProcess[str], Path]:
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
    command = ["bash", str(wrapper), "exit 2"]
    if alert_exit_2:
        if option_before_label:
            command.extend(["--alert-exit-2", "qualitative-v2-production-acceptance"])
        else:
            command.extend(["qualitative-v2-production-acceptance", "--alert-exit-2"])
    else:
        command.append("qualitative-v2-production-acceptance")
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


def test_alert_wrapper_accepts_option_before_label(tmp_path: Path) -> None:
    result, capture = _run_alert_wrapper(tmp_path, alert_exit_2=True, option_before_label=True)

    assert result.returncode == 2
    alert = capture.read_text(encoding="utf-8")
    assert "ROLLBACK" in alert
    assert "qualitative-v2-production-acceptance" in alert


@pytest.mark.parametrize(
    "extra_args",
    [
        ["label", "--alert-exit-2", "--alert-exit-2"],
        ["first-label", "second-label"],
        ["label", "--unsupported"],
    ],
    ids=["duplicate-option", "duplicate-label", "unknown-option"],
)
def test_alert_wrapper_rejects_invalid_arguments_before_running_command(tmp_path: Path, extra_args: list[str]) -> None:
    wrapper = tmp_path / "cron-alert-wrap.sh"
    shutil.copy2(PROJECT_ROOT / "cron-alert-wrap.sh", wrapper)
    marker = tmp_path / "executed"

    result = subprocess.run(
        [
            "bash",
            str(wrapper),
            f"touch {marker}",
            *extra_args,
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 64
    assert not marker.exists()


def _write_fake_crontab_command(fake_bin: Path) -> None:
    command = fake_bin / "crontab"
    command.write_text(
        """#!/bin/sh
case "${1:-}" in
  -l) [ ! -f "$CRONTAB_STATE" ] || cat "$CRONTAB_STATE" ;;
  -) cat > "$CRONTAB_STATE" ;;
  *) cp "$1" "$CRONTAB_STATE" ;;
esac
""",
        encoding="utf-8",
    )
    command.chmod(0o700)


def _run_cron_setup(tmp_path: Path, initial_crontab: str) -> tuple[subprocess.CompletedProcess[str], str]:
    project = tmp_path / "project"
    project.mkdir()
    setup = project / "cron-setup.sh"
    wrapper = project / "cron-alert-wrap.sh"
    shutil.copy2(PROJECT_ROOT / "cron-setup.sh", setup)
    shutil.copy2(PROJECT_ROOT / "cron-alert-wrap.sh", wrapper)
    fake_python = project / ".venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_python.chmod(0o700)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_crontab_command(fake_bin)
    state = tmp_path / "crontab.txt"
    state.write_text(initial_crontab, encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        {
            "A_STOCK_PROJECT_DIR": str(project),
            "A_STOCK_CRON_BACKUP_DIR": str(tmp_path / "backups"),
            "CRONTAB_STATE": str(state),
            "PATH": f"{fake_bin}:{environment['PATH']}",
        }
    )
    result = subprocess.run(["bash", str(setup)], capture_output=True, check=False, env=environment, text=True)
    return result, state.read_text(encoding="utf-8")


def test_cron_setup_canonicalizes_project_daily_variant_and_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    unrelated = f"7 8 * * * /other/project/pipeline.py daily # references {project}\n"
    protected_comment = "# a-stock-tracker personal note outside managed block\n"
    legacy = (
        f'30 17 * * 1-5 {project}/cron-alert-wrap.sh "cd {project} && /usr/bin/python3 -m pipeline daily" old-daily\n'
    )
    legacy_file = f"31 17 * * 1-5 /usr/bin/python3 {project}/pipeline.py\t daily # old-direct\n"

    first_result, first_crontab = _run_cron_setup(tmp_path, unrelated + protected_comment + legacy + legacy_file)

    assert first_result.returncode == 0
    assert "门禁未通过，但检测到既有 daily；保留现有评分链" in first_result.stdout
    assert "移除 daily" not in first_result.stdout
    assert unrelated.strip() in first_crontab
    assert protected_comment.strip() in first_crontab
    assert "-m pipeline daily" not in first_crontab
    assert "old-direct" not in first_crontab
    assert first_crontab.count(".venv/bin/python pipeline.py daily") == 1

    state = tmp_path / "crontab.txt"
    state.write_text(first_crontab, encoding="utf-8")
    second_result = subprocess.run(
        ["bash", str(tmp_path / "project" / "cron-setup.sh")],
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "A_STOCK_PROJECT_DIR": str(tmp_path / "project"),
            "A_STOCK_CRON_BACKUP_DIR": str(tmp_path / "backups"),
            "CRONTAB_STATE": str(state),
            "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        },
        text=True,
    )

    assert second_result.returncode == 0
    assert state.read_text(encoding="utf-8") == first_crontab


def test_cron_setup_does_not_preserve_daily_from_comment_or_other_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initial = (
        f"# {project}/pipeline.py daily is documentation only\n7 8 * * * /other/project/pipeline.py daily # unrelated\n"
    )

    result, installed = _run_cron_setup(tmp_path, initial)

    assert result.returncode == 0
    assert "未发现既有 daily，且行情恢复门禁未通过；不新增评分写任务" in result.stdout
    assert initial.strip() in installed
    assert ".venv/bin/python pipeline.py daily" not in installed


def test_cron_setup_rollback_restores_different_snapshot_byte_for_byte(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    setup = project / "cron-setup.sh"
    shutil.copy2(PROJECT_ROOT / "cron-setup.sh", setup)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_crontab_command(fake_bin)
    state = tmp_path / "crontab.txt"
    snapshot = tmp_path / "snapshot.txt"
    original = b"# snapshot A\n1 2 * * * /safe/task\n\n"
    changed = b"# live state B\n9 9 * * * /different/task\n"
    snapshot.write_bytes(original)
    state.write_bytes(changed)
    assert state.read_bytes() != snapshot.read_bytes()
    environment = {
        **os.environ,
        "CRONTAB_STATE": str(state),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["bash", str(setup), "--rollback", str(snapshot)],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode == 0
    assert str(snapshot) in result.stdout
    assert state.read_bytes() == original
