from __future__ import annotations

import importlib.util
import os
from datetime import date
from pathlib import Path
import sys


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_market_data_readiness.py"
spec = importlib.util.spec_from_file_location("check_market_data_readiness", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
readiness = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = readiness
spec.loader.exec_module(readiness)


def _write_report(root: Path, name: str, body: str) -> Path:
    reviews = root / "docs" / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    report = reviews / name
    report.write_text(body, encoding="utf-8")
    return report


def _new_report(
    *,
    write_gate: str = "PASS",
    capability_checks: str = "PASS",
    production_decision: str = "DAILY_WRITES_ALLOWED",
    dependent_jobs: str = "ALLOWED",
    close_cross_check: str = "PASS",
) -> str:
    return f"""
# Tushare Capability Probe

## Decision

Write Gate: {write_gate}
Capability Checks: {capability_checks}
Production Decision: {production_decision}
Index/Calendar Dependent Jobs: {dependent_jobs}

| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |
|---|---|---|---|---:|---:|---|---|
| index_daily 000300 (000300.SH) | no | failed | tushare.index_daily | 0 | 1.0 | RATE_LIMITED | |

## Close Cross-Check

Close cross-check: {close_cross_check}
"""


def test_latest_probe_report_filters_non_standard_names(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    older = _write_report(tmp_path, "2026-06-25-tushare-capability-probe.md", _new_report())
    latest = _write_report(tmp_path, "2026-06-26-tushare-capability-probe.md", _new_report())
    _write_report(tmp_path, "zz-2026-06-27-tushare-capability-probe.md", _new_report())
    _write_report(tmp_path, "2026-6-27-tushare-capability-probe.md", _new_report())
    _write_report(tmp_path, "2026-06-31-tushare-capability-probe.md", _new_report())

    assert readiness.latest_probe_report() == latest
    assert readiness.latest_probe_report() != older


def test_cron_readiness_rejects_stale_report(tmp_path, monkeypatch, capsys) -> None:
    _write_report(tmp_path, "2026-06-25-tushare-capability-probe.md", _new_report())
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(readiness, "_today", lambda: date(2026, 6, 26))
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    assert readiness.main(["--scope", "cron"]) == 1

    out = capsys.readouterr().out
    assert "HOLD_CRON" in out
    assert "latest Tushare capability probe is stale" in out


def test_readiness_rejects_duplicate_decision_fields(tmp_path, monkeypatch) -> None:
    _write_report(
        tmp_path,
        "2026-06-26-tushare-capability-probe.md",
        _new_report() + "\nWrite Gate: FAIL\n",
    )
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    status = readiness.readiness_status()

    assert not status.daily_ready
    assert not status.capability_ready
    assert "Write Gate appears multiple times in latest Tushare capability probe" in status.reasons


def test_readiness_allows_cron_when_daily_and_capabilities_pass(tmp_path, monkeypatch) -> None:
    _write_report(tmp_path, "2026-06-26-tushare-capability-probe.md", _new_report())
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    status = readiness.readiness_status()

    assert status.daily_ready
    assert status.capability_ready
    assert status.cron_ready
    assert status.reasons == []


def test_readiness_allows_daily_but_holds_cron_when_capabilities_degraded(tmp_path, monkeypatch) -> None:
    _write_report(
        tmp_path,
        "2026-06-26-tushare-capability-probe.md",
        _new_report(capability_checks="DEGRADED", dependent_jobs="HOLD"),
    )
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    status = readiness.readiness_status()

    assert status.daily_ready
    assert not status.capability_ready
    assert not status.cron_ready
    assert "Capability Checks is DEGRADED" in status.reasons
    assert "Index/Calendar Dependent Jobs is HOLD" in status.reasons


def test_readiness_rejects_legacy_report_even_when_old_result_was_pass(tmp_path, monkeypatch) -> None:
    _write_report(
        tmp_path,
        "2026-06-26-tushare-capability-probe.md",
        "\nClose cross-check: PASS\n\n## Result\n\nPASS\n",
    )
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    status = readiness.readiness_status()

    assert not status.daily_ready
    assert not status.capability_ready
    assert not status.cron_ready
    assert "latest Tushare capability probe uses legacy readiness format; rerun probe" in status.reasons


def test_readiness_rejects_partial_latest_report_instead_of_falling_back_to_older_pass(
    tmp_path,
    monkeypatch,
) -> None:
    _write_report(tmp_path, "2026-06-25-tushare-capability-probe.md", _new_report())
    _write_report(
        tmp_path,
        "2026-06-26-tushare-capability-probe.md",
        """
# Tushare Capability Probe

## Decision

Write Gate: PASS
Capability Checks: PASS
""",
    )
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    status = readiness.readiness_status()

    assert status.report_path == tmp_path / "docs" / "reviews" / "2026-06-26-tushare-capability-probe.md"
    assert not status.daily_ready
    assert not status.capability_ready
    assert "latest Tushare capability probe uses legacy readiness format; rerun probe" in status.reasons


def test_readiness_rejects_unknown_decision_values(tmp_path, monkeypatch) -> None:
    _write_report(
        tmp_path,
        "2026-06-26-tushare-capability-probe.md",
        _new_report(
            write_gate="READY",
            capability_checks="UNKNOWN",
            production_decision="ALLOW",
            dependent_jobs="READY",
        ),
    )
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    status = readiness.readiness_status()

    assert not status.daily_ready
    assert not status.capability_ready
    assert "Write Gate is READY" in status.reasons
    assert "Production Decision is ALLOW" in status.reasons
    assert "Capability Checks is UNKNOWN" in status.reasons
    assert "Index/Calendar Dependent Jobs is READY" in status.reasons


def test_readiness_loads_token_from_dotenv(tmp_path, monkeypatch) -> None:
    _write_report(tmp_path, "2026-06-26-tushare-capability-probe.md", _new_report())
    (tmp_path / ".env").write_text("TUSHARE_TOKEN=token-from-env-file\n", encoding="utf-8")
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    status = readiness.readiness_status()

    assert status.daily_ready
    assert status.capability_ready
    assert os.environ["TUSHARE_TOKEN"] == "token-from-env-file"


def test_readiness_dotenv_does_not_override_existing_token(tmp_path, monkeypatch) -> None:
    _write_report(tmp_path, "2026-06-26-tushare-capability-probe.md", _new_report())
    (tmp_path / ".env").write_text("TUSHARE_TOKEN=token-from-env-file\n", encoding="utf-8")
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token-from-shell")

    status = readiness.readiness_status()

    assert status.daily_ready
    assert status.capability_ready
    assert os.environ["TUSHARE_TOKEN"] == "token-from-shell"


def test_readiness_blocks_daily_when_write_gate_fails(tmp_path, monkeypatch) -> None:
    _write_report(
        tmp_path,
        "2026-06-26-tushare-capability-probe.md",
        _new_report(write_gate="FAIL", production_decision="DAILY_WRITES_BLOCKED"),
    )
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    status = readiness.readiness_status()

    assert not status.daily_ready
    assert not status.capability_ready
    assert "Write Gate is FAIL" in status.reasons
    assert "Production Decision is DAILY_WRITES_BLOCKED" in status.reasons


def test_readiness_blocks_daily_when_close_cross_check_fails_or_needs_manual_review(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    for close_cross_check in ("FAIL", "MANUAL_REQUIRED"):
        _write_report(
            tmp_path,
            "2026-06-26-tushare-capability-probe.md",
            _new_report(close_cross_check=close_cross_check),
        )

        status = readiness.readiness_status()

        assert not status.daily_ready
        assert not status.capability_ready
        assert f"Close cross-check is {close_cross_check}" in status.reasons


def test_readiness_holds_capability_when_checks_blocked_or_dependent_jobs_hold(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    for capability_checks, dependent_jobs in (("BLOCKED", "HOLD"), ("PASS", "HOLD")):
        _write_report(
            tmp_path,
            "2026-06-26-tushare-capability-probe.md",
            _new_report(capability_checks=capability_checks, dependent_jobs=dependent_jobs),
        )

        status = readiness.readiness_status()

        assert status.daily_ready
        assert not status.capability_ready


def test_readiness_blocks_without_token_or_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    status = readiness.readiness_status()

    assert not status.daily_ready
    assert not status.capability_ready
    assert "TUSHARE_TOKEN is not configured" in status.reasons
    assert "no Tushare capability probe report found" in status.reasons


def test_main_scope_exit_codes_for_degraded_capabilities(tmp_path, monkeypatch, capsys) -> None:
    _write_report(
        tmp_path,
        "2026-06-26-tushare-capability-probe.md",
        _new_report(capability_checks="DEGRADED", dependent_jobs="HOLD"),
    )
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(readiness, "_today", lambda: date(2026, 6, 26))
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    assert readiness.main(["--scope", "cron"]) == 1
    cron_out = capsys.readouterr().out
    assert "HOLD_CRON" in cron_out
    assert "- daily writes: READY" in cron_out
    assert "- index/calendar-dependent jobs: HOLD" in cron_out

    assert readiness.main(["--scope", "daily"]) == 0
    daily_out = capsys.readouterr().out
    assert "READY_DAILY" in daily_out
    assert "- latest report: docs/reviews/2026-06-26-tushare-capability-probe.md" in daily_out


def test_main_scope_exit_codes_when_daily_fails(tmp_path, monkeypatch, capsys) -> None:
    _write_report(
        tmp_path,
        "2026-06-26-tushare-capability-probe.md",
        _new_report(write_gate="FAIL", production_decision="DAILY_WRITES_BLOCKED"),
    )
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(readiness, "_today", lambda: date(2026, 6, 26))
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    assert readiness.main(["--scope", "cron"]) == 1
    cron_out = capsys.readouterr().out
    assert "HOLD_CRON" in cron_out

    assert readiness.main(["--scope", "daily"]) == 1
    daily_out = capsys.readouterr().out
    assert "HOLD_DAILY" in daily_out
