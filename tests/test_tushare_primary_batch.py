from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a_stock_tracker.data import tushare_primary_batch as batch


@dataclass(frozen=True)
class FakeSummary:
    status: str
    endpoint: str
    source_as_of: str
    row_count: int = 1
    record_count: int = 1
    event_count: int = 1
    error_code: str | None = None


def test_batch_preview_is_bounded_and_does_not_call_execute(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    artifact_root = tmp_path / "artifacts"
    calls: list[Any] = []

    def fake_execute(_: Any) -> FakeSummary:
        calls.append("execute")
        return FakeSummary(status="completed", endpoint="daily_basic", source_as_of="2026-07-21")

    report = batch.run_tushare_primary_batch(
        scope="valuation",
        db_path=db_path,
        artifact_root=artifact_root,
        as_of="2026-07-21",
        watchlist_codes=["600036", "601288"],
        execute=fake_execute,
        execute_mode=False,
        request_payload_start_date="2026-07-01",
    )

    assert report["executed"] is False
    assert report["success"] is True
    assert not calls
    assert report["request_count"] == 3


def test_batch_execute_calls_expected_scope_endpoints_once_and_returns_success_for_all_success(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    artifact_root = tmp_path / "artifacts"
    calls: list[str] = []

    def fake_execute(request: batch.IngestionRequest) -> FakeSummary:
        calls.append(request.endpoint)
        return FakeSummary(status="completed", endpoint=request.endpoint, source_as_of="2026-07-21")

    report = batch.run_tushare_primary_batch(
        scope="all",
        db_path=db_path,
        artifact_root=artifact_root,
        as_of="2026-07-21",
        watchlist_codes=["600036", "601288"],
        financial_endpoints=("fina_indicator",),
        execute=fake_execute,
        execute_mode=True,
        request_payload_start_date="2010-01-01",
    )

    assert report["executed"] is True
    assert report["success"] is True
    assert report["request_count"] == 7
    assert sorted((r["endpoint"] for r in report["request_results"])) == sorted(
        ["daily_basic"] + ["valuation-history"] * 2 + ["fina_indicator"] * 2 + ["dividend"] * 2
    )


def test_batch_execute_returns_failure_if_any_request_fails(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    artifact_root = tmp_path / "artifacts"

    def fake_execute(request: batch.IngestionRequest) -> FakeSummary:
        if request.endpoint == "fina_indicator" and request.code == "601288":
            return FakeSummary(
                status="failed",
                endpoint="fina_indicator",
                source_as_of="2026-07-21",
                error_code="NETWORK_ERROR",
            )
        return FakeSummary(status="completed", endpoint=request.endpoint, source_as_of="2026-07-21")

    report = batch.run_tushare_primary_batch(
        scope="financial",
        db_path=db_path,
        artifact_root=artifact_root,
        as_of="2026-07-21",
        watchlist_codes=["600036", "601288"],
        financial_endpoints=("fina_indicator",),
        execute=fake_execute,
        execute_mode=True,
        request_payload_start_date="2010-01-01",
    )

    assert report["success"] is False
    assert report["failed_count"] == 1
    assert any(item["status"] == "failed" for item in report["request_results"]) is True


def test_batch_rejects_tracker_db_path_even_in_preview(tmp_path: Path) -> None:
    db_path = tmp_path / "tracker.db"
    artifact_root = tmp_path / "artifacts"

    def fake_execute(_: batch.IngestionRequest) -> FakeSummary:
        return FakeSummary(status="completed", endpoint="daily_basic", source_as_of="2026-07-21")

    result = batch.run_tushare_primary_batch(
        scope="valuation",
        db_path=db_path,
        artifact_root=artifact_root,
        as_of="2026-07-21",
        watchlist_codes=["600036"],
        execute=fake_execute,
        execute_mode=False,
    )

    assert result["success"] is False
    assert result["errors"] == ["refuse-production-db"]


def test_batch_parser_outputs_structured_json_with_main(tmp_path: Path, monkeypatch, capsys) -> None:
    db_path = tmp_path / "shadow.db"
    artifact_root = tmp_path / "artifacts"

    def fake_run(
        *,
        scope: str,
        db_path: Path,
        artifact_root: Path,
        as_of: str,
        watchlist_codes: list[str],
        execute: Any,
        execute_mode: bool,
        financial_endpoints: tuple[str, ...],
        valuation_history_days: int,
        request_payload_start_date: str | None,
    ) -> dict[str, Any]:
        return {
            "executed": False,
            "success": True,
            "request_count": 0,
            "request_results": [],
            "failed_count": 0,
            "errors": [],
        }

    monkeypatch.setattr(batch, "run_tushare_primary_batch", fake_run)
    code = batch.main(
        [
            "--scope",
            "valuation",
            "--db-path",
            str(db_path),
            "--artifact-root",
            str(artifact_root),
            "--as-of-date",
            "2026-07-21",
            "--watchlist-codes",
            "600036",
            "--request-payload-start-date",
            "2026-01-01",
        ]
    )

    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out.startswith("{") and out.endswith("}")
