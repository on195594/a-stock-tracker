from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    monkeypatch.setattr(cycle, "run_materialization", lambda **kwargs: SimpleNamespace(changed_count=1, kwargs=kwargs))

    report = cycle.run_weekly_cycle("2026-07-21")

    assert report == {
        "mode": "weekly",
        "financial_requests": 1,
        "dividend_requests": 1,
        "changed_count": 1,
    }
    assert calls == ["financial", "dividend"]


def test_main_serializes_path_values(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(cycle, "run_daily_cycle", lambda as_of: {"artifact": Path("1/daily_basic.jsonl.gz")})

    exit_code = cycle.main(["daily", "--as-of-date", "2026-07-21"])

    assert exit_code == 0
    assert '"artifact": "1/daily_basic.jsonl.gz"' in capsys.readouterr().out
