from __future__ import annotations

import importlib.util
import os
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_market_data_readiness.py"
spec = importlib.util.spec_from_file_location("check_market_data_readiness", SCRIPT_PATH)
readiness = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(readiness)


def test_probe_passed_requires_pass_and_no_failed_rows(tmp_path) -> None:
    report = tmp_path / "probe.md"
    report.write_text("\nClose cross-check: PASS\n\n## Result\n\nPASS\n", encoding="utf-8")
    assert readiness.probe_passed(report)

    report.write_text("\nClose cross-check: PASS\n\n## Result\n\nPASS\n\n| x | failed | y |\n", encoding="utf-8")
    assert not readiness.probe_passed(report)

    report.write_text("\nClose cross-check: MANUAL_REQUIRED\n\n## Result\n\nPASS\n", encoding="utf-8")
    assert not readiness.probe_passed(report)


def test_readiness_status_requires_token_and_passing_probe(tmp_path, monkeypatch) -> None:
    reviews = tmp_path / "docs" / "reviews"
    reviews.mkdir(parents=True)
    report = reviews / "2026-06-09-tushare-capability-probe.md"
    report.write_text("\nClose cross-check: PASS\n\n## Result\n\nPASS\n", encoding="utf-8")
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(os.environ, "TUSHARE_TOKEN", "token")

    ready, reasons = readiness.readiness_status()

    assert ready
    assert reasons == []


def test_readiness_status_reports_missing_token_and_failed_probe(tmp_path, monkeypatch) -> None:
    reviews = tmp_path / "docs" / "reviews"
    reviews.mkdir(parents=True)
    report = reviews / "2026-06-09-tushare-capability-probe.md"
    report.write_text("\n## Result\n\nFAIL\n", encoding="utf-8")
    monkeypatch.setattr(readiness, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    ready, reasons = readiness.readiness_status()

    assert not ready
    assert "TUSHARE_TOKEN is not configured" in reasons
    assert any("not PASS" in reason for reason in reasons)
