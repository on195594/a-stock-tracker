from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from a_stock_tracker.data.tushare_primary_ingestion import IngestionRequest, execute_ingestion, main


@dataclass(frozen=True)
class FakeResult:
    value: pd.DataFrame | None
    status: str
    source: str
    fetched_at: str
    source_as_of: str | None
    request_fingerprint: str | None
    row_count: int | None
    error_code: str | None = None
    error_message: str | None = None


class FakeValuationProvider:
    def __init__(self, result: FakeResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    def fetch_valuation_history(self, code: str, start_date: str, end_date: str) -> FakeResult:
        self.calls.append((code, start_date, end_date))
        return self.result

    def fetch_daily_basic_by_trade_date(self, trade_date: str, code: str | None = None) -> FakeResult:
        self.calls.append((code or "*", trade_date, trade_date))
        return self.result


def _success_result() -> FakeResult:
    frame = pd.DataFrame(
        [
            {
                "ts_code": "603606.SH",
                "trade_date": "2026-07-18",
                "close": 58.2,
                "pe": 18.4,
                "pe_ttm": 17.9,
                "pb": 2.1,
                "ps": 3.0,
                "ps_ttm": 2.9,
                "dv_ratio": 1.2,
                "dv_ttm": 1.1,
                "total_mv": 4000000.0,
                "circ_mv": 3900000.0,
            }
        ]
    )
    return FakeResult(
        value=frame,
        status="ok",
        source="tushare.daily_basic",
        fetched_at="2026-07-21T01:00:00+00:00",
        source_as_of="2026-07-18",
        request_fingerprint="fingerprint",
        row_count=1,
    )


def test_execute_ingestion_writes_only_explicit_shadow_db_and_artifact(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow" / "primary.db"
    artifact_root = tmp_path / "artifacts"
    provider = FakeValuationProvider(_success_result())
    request = IngestionRequest(
        endpoint="daily_basic",
        db_path=db_path,
        artifact_root=artifact_root,
        code="603606",
        start_date="2026-07-01",
        end_date="2026-07-18",
        trade_date=None,
        pit_status="backfilled_latest",
    )

    summary = execute_ingestion(request, provider=provider)

    assert summary.status == "completed"
    assert summary.row_count == 1
    assert summary.db_path == db_path.resolve()
    assert provider.calls == [("603606", "2026-07-01", "2026-07-18")]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM valuation_observations").fetchone()[0] == 1
        run = conn.execute("SELECT status, row_count, raw_artifact_path, payload_sha256 FROM ingestion_runs").fetchone()
    assert run[0:2] == ("completed", 1)
    assert run[2].endswith("/daily_basic.jsonl.gz")
    assert run[3]


def test_execute_ingestion_records_provider_failure_without_artifact(tmp_path: Path) -> None:
    result = FakeResult(
        value=None,
        status="failed",
        source="tushare.daily_basic",
        fetched_at="2026-07-21T01:00:00+00:00",
        source_as_of=None,
        request_fingerprint="fingerprint",
        row_count=0,
        error_code="PERMISSION_DENIED",
        error_message="permission denied",
    )
    request = IngestionRequest(
        endpoint="daily_basic",
        db_path=tmp_path / "shadow.db",
        artifact_root=tmp_path / "artifacts",
        code="603606",
        start_date="2026-07-01",
        end_date="2026-07-18",
        trade_date=None,
        pit_status="backfilled_latest",
    )

    summary = execute_ingestion(request, provider=FakeValuationProvider(result))

    assert summary.status == "failed"
    assert not list((tmp_path / "artifacts").glob("**/*.gz"))
    with sqlite3.connect(request.db_path) as conn:
        assert conn.execute("SELECT status FROM ingestion_runs").fetchone()[0] == "failed"
        assert conn.execute("SELECT COUNT(*) FROM valuation_observations").fetchone()[0] == 0


def test_daily_market_artifact_keeps_full_response_but_db_filters_allowed_codes(tmp_path: Path) -> None:
    result = _success_result()
    assert result.value is not None
    second = {**result.value.iloc[0].to_dict(), "ts_code": "600036.SH"}
    market_result = FakeResult(
        value=pd.concat([result.value, pd.DataFrame([second])], ignore_index=True),
        status=result.status,
        source=result.source,
        fetched_at=result.fetched_at,
        source_as_of=result.source_as_of,
        request_fingerprint=result.request_fingerprint,
        row_count=2,
    )
    request = IngestionRequest(
        endpoint="daily_basic",
        db_path=tmp_path / "shadow.db",
        artifact_root=tmp_path / "artifacts",
        code=None,
        start_date=None,
        end_date=None,
        trade_date="2026-07-18",
        pit_status="prospective_observed",
        allowed_codes=frozenset({"603606"}),
    )

    summary = execute_ingestion(request, provider=FakeValuationProvider(market_result))

    assert summary.row_count == 2
    assert summary.record_count == 1
    assert summary.artifact_path is not None
    import gzip

    with gzip.open(summary.artifact_path, "rt", encoding="utf-8") as handle:
        assert len(handle.readlines()) == 2
    with sqlite3.connect(request.db_path) as conn:
        assert conn.execute("SELECT code FROM valuation_observations").fetchall() == [("603606",)]
        assert conn.execute("SELECT row_count FROM ingestion_runs").fetchone()[0] == 2


def test_artifact_failure_closes_checkpoint_as_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from a_stock_tracker.data import tushare_primary_ingestion as ingestion

    monkeypatch.setattr(
        ingestion, "write_raw_artifact", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full"))
    )
    request = IngestionRequest(
        endpoint="daily_basic",
        db_path=tmp_path / "shadow.db",
        artifact_root=tmp_path / "artifacts",
        code="603606",
        start_date="2026-07-01",
        end_date="2026-07-18",
        trade_date=None,
        pit_status="backfilled_latest",
    )

    summary = execute_ingestion(request, provider=FakeValuationProvider(_success_result()))

    assert summary.status == "failed"
    with sqlite3.connect(request.db_path) as conn:
        assert conn.execute("SELECT status FROM ingestion_runs").fetchone()[0] == "failed"


def test_partial_record_failure_rolls_back_all_observations(tmp_path: Path) -> None:
    result = _success_result()
    assert result.value is not None
    invalid = {**result.value.iloc[0].to_dict(), "ts_code": "INVALID"}
    mixed_result = FakeResult(
        value=pd.concat([result.value, pd.DataFrame([invalid])], ignore_index=True),
        status=result.status,
        source=result.source,
        fetched_at=result.fetched_at,
        source_as_of=result.source_as_of,
        request_fingerprint=result.request_fingerprint,
        row_count=2,
    )
    request = IngestionRequest(
        endpoint="daily_basic",
        db_path=tmp_path / "shadow.db",
        artifact_root=tmp_path / "artifacts",
        code="603606",
        start_date="2026-07-01",
        end_date="2026-07-18",
        trade_date=None,
        pit_status="backfilled_latest",
    )

    summary = execute_ingestion(request, provider=FakeValuationProvider(mixed_result))

    assert summary.status == "failed"
    assert not list(request.artifact_root.glob("**/*.gz"))
    with sqlite3.connect(request.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM valuation_observations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM observation_events").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM ingestion_runs").fetchone()[0] == "failed"


def test_main_requires_explicit_db_path() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["valuation-history", "--code", "603606", "--start-date", "2026-07-01", "--end-date", "2026-07-18"])
    assert exc_info.value.code == 2


def test_main_logs_resolved_db_path_and_returns_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    provider = FakeValuationProvider(_success_result())
    exit_code = main(
        [
            "valuation-history",
            "--db-path",
            str(tmp_path / "nested" / "shadow.db"),
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--code",
            "603606",
            "--start-date",
            "2026-07-01",
            "--end-date",
            "2026-07-18",
        ],
        provider_factory=lambda endpoint: provider,
    )
    captured = capsys.readouterr()
    body: dict[str, Any] = json.loads(captured.out)
    assert exit_code == 0
    assert body["status"] == "completed"
    assert str((tmp_path / "nested" / "shadow.db").resolve()) in caplog.text
