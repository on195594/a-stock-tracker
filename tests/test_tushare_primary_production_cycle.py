from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from a_stock_tracker.data import tushare_primary_production_cycle as cycle


@dataclass(frozen=True)
class FakeSummary:
    status: str = "completed"
    endpoint: str = "daily_basic"
    db_path: Path = Path("shadow.db")
    run_id: int = 1
    source_as_of: str = "2026-07-21"
    row_count: int = 35
    record_count: int = 35
    event_count: int = 35
    artifact_path: str = "1/daily_basic.jsonl.gz"
    error_code: str | None = None
    error_message: str | None = None


def test_daily_cycle_ingests_checks_and_materializes_only_valuation(monkeypatch) -> None:
    calls: dict[str, Any] = {}
    monkeypatch.setattr(cycle, "_codes", lambda: ["600036"])
    monkeypatch.setattr(cycle, "assess_readiness", lambda **kwargs: {"status": "READY"})

    def fake_materialize(**kwargs: Any) -> SimpleNamespace:
        calls["materialize"] = kwargs
        return SimpleNamespace(changed_count=1)

    monkeypatch.setattr(cycle, "run_materialization", fake_materialize)

    def fake_execute(request: Any) -> FakeSummary:
        calls["request"] = request
        return FakeSummary()

    monkeypatch.setattr(cycle, "execute_ingestion", fake_execute)

    report = cycle.run_daily_cycle("2026-07-21")

    assert report["changed_count"] == 1
    assert calls["request"].trade_date == "2026-07-21"
    assert calls["materialize"]["valuation_enabled"] is True
    assert calls["materialize"]["financial_enabled"] is False
    assert calls["materialize"]["dividend_enabled"] is False
    assert calls["materialize"]["execute"] is True


def test_weekly_cycle_materializes_financial_and_dividend(monkeypatch) -> None:
    calls: list[str] = []
    materialization_calls: dict[str, Any] = {}
    monkeypatch.setattr(cycle, "_codes", lambda: ["600036"])
    monkeypatch.setattr(
        cycle,
        "_run_batch",
        lambda scope, as_of, codes: {"success": True, "request_count": 1, "scope": scope},
    )

    def fake_readiness(**kwargs: Any) -> dict[str, str]:
        calls.append(kwargs["scope"])
        return {"status": "READY"}

    monkeypatch.setattr(cycle, "assess_readiness", fake_readiness)

    def fake_materialize(**kwargs: Any) -> SimpleNamespace:
        materialization_calls.update(kwargs)
        return SimpleNamespace(changed_count=1)

    monkeypatch.setattr(cycle, "run_materialization", fake_materialize)

    report = cycle.run_weekly_cycle("2026-07-21")

    assert report == {
        "mode": "weekly",
        "financial_requests": 1,
        "dividend_requests": 1,
        "changed_count": 1,
    }
    assert calls == ["financial", "dividend"]
    assert materialization_calls["watchlist_industries"] == {
        str(item["code"]): str(item.get("industry", "")) for item in cycle.WATCHLIST
    }


def test_main_serializes_path_values(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(cycle, "run_daily_cycle", lambda as_of: {"artifact": Path("1/daily_basic.jsonl.gz")})

    exit_code = cycle.main(["daily", "--as-of-date", "2026-07-21"])

    assert exit_code == 0
    assert '"artifact": "1/daily_basic.jsonl.gz"' in capsys.readouterr().out


def test_main_success_record_includes_completion_timestamp(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(cycle, "run_daily_cycle", lambda as_of: {"changed_count": 35})

    exit_code = cycle.main(["daily", "--as-of-date", "2026-07-21"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed_at"].startswith("2026-")


def test_run_batch_failure_preserves_report_details(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        cycle,
        "run_tushare_primary_batch",
        lambda **kwargs: {
            "success": False,
            "failed_count": 2,
            "errors": ["600036: timeout", "601288: permission denied"],
            "request_results": [],
        },
    )

    with pytest.raises(RuntimeError) as raised:
        cycle._run_batch("financial", "2026-07-21", ["600036", "601288"])

    message = str(raised.value)
    assert "FINANCIAL_INGESTION_FAILED" in message
    assert "failed_count=2" in message
    assert "600036: timeout" in message
    assert "601288: permission denied" in message


def test_main_failure_includes_traceback(monkeypatch: Any, capsys: Any) -> None:
    def fail(_as_of: str) -> dict[str, Any]:
        raise RuntimeError("specific ingestion failure")

    monkeypatch.setattr(cycle, "run_daily_cycle", fail)

    exit_code = cycle.main(["daily", "--as-of-date", "2026-07-21"])

    assert exit_code == 1
    failure = json.loads(capsys.readouterr().err)
    assert failure["status"] == "failed"
    assert failure["error"] == "specific ingestion failure"
    assert failure["traceback"]
    assert "RuntimeError: specific ingestion failure" in failure["traceback"]
