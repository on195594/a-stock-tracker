from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "probe_tushare_market_data.py"
spec = importlib.util.spec_from_file_location("probe_tushare_market_data", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def _check(
    *,
    kind: str,
    blocking: bool,
    status: str = "ok",
    error_code: str = "",
    code: str = "",
    latest_trade_date: str = "",
    latest_close: float | None = None,
):
    return probe.ProbeCheck(
        label=kind,
        kind=kind,
        blocking=blocking,
        code=code,
        status=status,
        source=f"tushare.{kind}",
        error_code=error_code,
        error_message="",
        rows=1 if status != "failed" else 0,
        elapsed_ms=1.0,
        latest_trade_date=latest_trade_date,
        latest_close=latest_close,
    )


def test_probe_samples_match_current_tracked_reference_universe() -> None:
    assert probe.SAMPLES == ["600036", "000786", "002594"]


def test_close_cross_check_requires_same_reference_trade_date(monkeypatch) -> None:
    monkeypatch.setattr(
        probe,
        "_load_reference_close",
        lambda code, trade_date: probe.ReferenceClose(36.76, "tushare.daily", "2026-06-24"),
    )

    status, rows = probe._close_cross_checks(
        [{"code": "600036", "latest_trade_date": "2026-06-25", "latest_close": 36.23}]
    )

    assert status == "FAIL"
    assert "DATE_MISMATCH" in rows[0]
    assert "2026-06-24" in rows[0]


def test_close_cross_check_compares_same_day_reference(monkeypatch) -> None:
    monkeypatch.setattr(
        probe,
        "_load_reference_close",
        lambda code, trade_date: probe.ReferenceClose(36.22, "tushare.daily", trade_date),
    )

    status, rows = probe._close_cross_checks(
        [{"code": "600036", "latest_trade_date": "2026-06-25", "latest_close": 36.23}]
    )

    assert status == "PASS"
    assert "PASS" in rows[0]


def test_load_reference_close_uses_qfq_production_row(monkeypatch, tmp_path) -> None:
    """Legacy tushare.daily/none rows stopped being written after the QFQ cutover
    and must not be picked up even if they have a newer fetched_at."""
    db_path = tmp_path / "market.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE daily_bars (
               code TEXT,
               trade_date TEXT,
               close REAL,
               source TEXT,
               adjusted TEXT,
               fetched_at TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?)",
            ("600036", "2026-06-25", 36.23, "tushare.daily", "none", "2026-06-25T15:01:00"),
        )
        conn.execute(
            "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?)",
            ("600036", "2026-06-25", 37.5, "tushare.pro_bar.qfq", "qfq", "2026-06-25T15:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(probe, "DB_PATH", str(db_path))

    reference = probe._load_reference_close("600036", "2026-06-25")

    assert reference == probe.ReferenceClose(37.5, "tushare.pro_bar.qfq", "2026-06-25")


def test_decision_allows_daily_writes_when_only_capabilities_are_rate_limited() -> None:
    checks = [
        _check(kind="daily", blocking=True),
        _check(kind="index_daily", blocking=False, status="failed", error_code="RATE_LIMITED"),
        _check(kind="trade_cal", blocking=False, status="failed", error_code="RATE_LIMITED"),
    ]

    decision = probe._decide_probe("token", checks, "PASS")

    assert decision.exit_code == 0
    assert decision.write_gate_status == "PASS"
    assert decision.capability_status == "DEGRADED"
    assert decision.production_decision == "DAILY_WRITES_ALLOWED"
    assert decision.dependent_jobs == "HOLD"


def test_decision_blocks_daily_writes_when_daily_check_fails() -> None:
    checks = [
        _check(kind="daily", blocking=True, status="failed", error_code="UNKNOWN_ERROR"),
        _check(kind="index_daily", blocking=False),
        _check(kind="trade_cal", blocking=False),
    ]

    decision = probe._decide_probe("token", checks, "PASS")

    assert decision.exit_code == 1
    assert decision.write_gate_status == "FAIL"
    assert decision.production_decision == "DAILY_WRITES_BLOCKED"


def test_decision_blocks_daily_writes_when_close_cross_check_fails() -> None:
    checks = [
        _check(kind="daily", blocking=True),
        _check(kind="index_daily", blocking=False),
        _check(kind="trade_cal", blocking=False),
    ]

    decision = probe._decide_probe("token", checks, "FAIL")

    assert decision.exit_code == 1
    assert decision.write_gate_status == "FAIL"
    assert decision.production_decision == "DAILY_WRITES_BLOCKED"


def test_decision_blocks_daily_writes_when_token_is_missing() -> None:
    checks = [
        _check(kind="daily", blocking=True),
        _check(kind="index_daily", blocking=False),
        _check(kind="trade_cal", blocking=False),
    ]

    decision = probe._decide_probe(None, checks, "PASS")

    assert decision.exit_code == 1
    assert decision.write_gate_status == "FAIL"
    assert decision.production_decision == "DAILY_WRITES_BLOCKED"
    assert decision.dependent_jobs == "HOLD"


def test_capability_timeout_or_unknown_error_blocks_capabilities() -> None:
    for error_code in ("TIMEOUT", "UNKNOWN_ERROR"):
        checks = [
            _check(kind="daily", blocking=True),
            _check(kind="index_daily", blocking=False, status="failed", error_code=error_code),
            _check(kind="trade_cal", blocking=False),
        ]

        decision = probe._decide_probe("token", checks, "PASS")

        assert decision.exit_code == 0
        assert decision.write_gate_status == "PASS"
        assert decision.capability_status == "BLOCKED"
        assert decision.dependent_jobs == "HOLD"


def test_mixed_capability_rate_limit_and_timeout_blocks_capabilities() -> None:
    checks = [
        _check(kind="daily", blocking=True),
        _check(kind="index_daily", blocking=False, status="failed", error_code="RATE_LIMITED"),
        _check(kind="trade_cal", blocking=False, status="failed", error_code="TIMEOUT"),
    ]

    decision = probe._decide_probe("token", checks, "PASS")

    assert decision.exit_code == 0
    assert decision.write_gate_status == "PASS"
    assert decision.capability_status == "BLOCKED"
    assert decision.dependent_jobs == "HOLD"


def test_dependent_jobs_allowed_only_when_write_gate_and_capabilities_pass() -> None:
    checks = [
        _check(kind="daily", blocking=True),
        _check(kind="index_daily", blocking=False),
        _check(kind="trade_cal", blocking=False),
    ]

    decision = probe._decide_probe("token", checks, "PASS")

    assert decision.exit_code == 0
    assert decision.write_gate_status == "PASS"
    assert decision.capability_status == "PASS"
    assert decision.dependent_jobs == "ALLOWED"


def test_render_report_includes_decision_fields_and_blocking_column() -> None:
    checks = [
        _check(kind="daily", blocking=True),
        _check(kind="index_daily", blocking=False, status="failed", error_code="RATE_LIMITED"),
        _check(kind="trade_cal", blocking=False),
    ]
    decision = probe._decide_probe("token", checks, "PASS")

    report = "\n".join(
        probe._render_report(
            report_date="2026-06-26",
            token="token",
            start="2025-06-26",
            end="2026-06-26",
            checks=checks,
            close_status="PASS",
            close_rows=["| 600036 | 2026-06-25 | 36.2300 | 2026-06-25 | 36.2300 | tushare.daily | 0.000% | PASS |"],
            decision=decision,
        )
    )

    assert "Write Gate: PASS" in report
    assert "Capability Checks: DEGRADED" in report
    assert "Production Decision: DAILY_WRITES_ALLOWED" in report
    assert "Index/Calendar Dependent Jobs: HOLD" in report
    assert "## Write Gate Result" in report
    assert "| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |" in report
    assert "| daily | yes | ok | tushare.daily | 1 | 1.0 |  |  |" in report
    assert "| index_daily | no | failed | tushare.index_daily | 0 | 1.0 | RATE_LIMITED |  |" in report


def test_load_reference_close_returns_none_when_cache_query_fails(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "market.db"
    db_path.write_text("not sqlite", encoding="utf-8")

    monkeypatch.setattr(probe, "DB_PATH", str(db_path))

    reference = probe._load_reference_close("600036", "2026-06-25")

    assert reference is None
