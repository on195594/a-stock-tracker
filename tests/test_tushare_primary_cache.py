from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from a_stock_tracker.data.tushare_primary_cache import (
    ShadowStoreError,
    complete_ingestion_run,
    create_ingestion_run,
    open_shadow_store,
    persist_observations,
    sanitize_error_message,
    write_raw_artifact,
)


VALUATION_ROW = {
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


def _counts(conn: sqlite3.Connection) -> tuple[int, int]:
    records = conn.execute("SELECT COUNT(*) FROM valuation_observations").fetchone()[0]
    events = conn.execute("SELECT COUNT(*) FROM observation_events").fetchone()[0]
    return records, events


def test_open_shadow_store_creates_only_isolated_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with open_shadow_store(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {
        "ingestion_runs",
        "observation_events",
        "valuation_observations",
        "financial_observations",
        "dividend_observations",
    }.issubset(tables)
    assert "predictions" not in tables
    assert "stock_fundamentals" not in tables


def test_open_shadow_store_rejects_directory_path(tmp_path: Path) -> None:
    with pytest.raises(ShadowStoreError, match="DB_PATH_IS_DIRECTORY"):
        open_shadow_store(tmp_path)


def test_open_shadow_store_rejects_tracker_db_name(tmp_path: Path) -> None:
    with pytest.raises(ShadowStoreError, match="PRODUCTION_DB_PATH_REJECTED"):
        open_shadow_store(tmp_path / "tracker.db")


def test_open_shadow_store_rejects_existing_production_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "looks-like-shadow.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE predictions (id INTEGER PRIMARY KEY)")
    with pytest.raises(ShadowStoreError, match="NON_SHADOW_SCHEMA_DETECTED"):
        open_shadow_store(db_path)


def test_same_payload_is_idempotent_within_run_and_observed_again_across_runs(tmp_path: Path) -> None:
    with open_shadow_store(tmp_path / "shadow.db") as conn:
        first_run = create_ingestion_run(conn, "daily_basic", "fingerprint-1", "2026-07-21T01:00:00+00:00")
        first = persist_observations(
            conn,
            endpoint="daily_basic",
            rows=[VALUATION_ROW],
            run_id=first_run,
            observed_at="2026-07-21T01:01:00+00:00",
            pit_status="backfilled_latest",
            source="tushare.daily_basic",
            source_as_of="2026-07-18",
        )
        repeated = persist_observations(
            conn,
            endpoint="daily_basic",
            rows=[dict(VALUATION_ROW)],
            run_id=first_run,
            observed_at="2026-07-21T01:02:00+00:00",
            pit_status="backfilled_latest",
            source="tushare.daily_basic",
            source_as_of="2026-07-18",
        )
        second_run = create_ingestion_run(conn, "daily_basic", "fingerprint-2", "2026-07-21T02:00:00+00:00")
        observed_again = persist_observations(
            conn,
            endpoint="daily_basic",
            rows=[dict(reversed(list(VALUATION_ROW.items())))],
            run_id=second_run,
            observed_at="2026-07-21T02:01:00+00:00",
            pit_status="prospective_observed",
            source="tushare.daily_basic",
            source_as_of="2026-07-18",
        )
        assert _counts(conn) == (1, 2)
    assert first == (1, 1)
    assert repeated == (0, 0)
    assert observed_again == (0, 1)


def test_changed_payload_creates_new_immutable_record(tmp_path: Path) -> None:
    changed = {**VALUATION_ROW, "pb": 2.2}
    with open_shadow_store(tmp_path / "shadow.db") as conn:
        first_run = create_ingestion_run(conn, "daily_basic", "one", "2026-07-21T01:00:00+00:00")
        second_run = create_ingestion_run(conn, "daily_basic", "two", "2026-07-21T02:00:00+00:00")
        persist_observations(
            conn,
            "daily_basic",
            [VALUATION_ROW],
            first_run,
            "2026-07-21T01:01:00+00:00",
            "backfilled_latest",
            "tushare.daily_basic",
            "2026-07-18",
        )
        persist_observations(
            conn,
            "daily_basic",
            [changed],
            second_run,
            "2026-07-21T02:01:00+00:00",
            "backfilled_latest",
            "tushare.daily_basic",
            "2026-07-18",
        )
        assert _counts(conn) == (2, 2)


def test_financial_empty_dates_are_normalized_to_null(tmp_path: Path) -> None:
    row = {
        "ts_code": "603606.SH",
        "endpoint": "income",
        "ann_date": "2026-04-01",
        "f_ann_date": "",
        "end_date": "2025-12-31",
        "report_type": "1",
        "comp_type": "1",
        "end_type": "4",
        "update_flag": "1",
        "revenue": 123.0,
    }
    with open_shadow_store(tmp_path / "shadow.db") as conn:
        run_id = create_ingestion_run(conn, "income", "fp", "2026-07-21T01:00:00+00:00")
        persist_observations(
            conn,
            "income",
            [row],
            run_id,
            "2026-07-21T01:01:00+00:00",
            "prospective_observed",
            "tushare.income",
            "2026-04-01",
        )
        f_ann_date, payload_json = conn.execute(
            "SELECT f_ann_date, payload_json FROM financial_observations"
        ).fetchone()
    assert f_ann_date is None
    assert json.loads(payload_json)["f_ann_date"] is None


def test_artifact_rejects_endpoint_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ShadowStoreError, match="UNSUPPORTED_ENDPOINT"):
        write_raw_artifact(tmp_path, 7, "../../escape", [VALUATION_ROW])
    assert not (tmp_path.parent / "escape.jsonl.gz").exists()


def test_artifact_is_gzip_atomic_and_checksum_matches(tmp_path: Path) -> None:
    artifact = write_raw_artifact(tmp_path, 7, "daily_basic", [VALUATION_ROW])
    assert artifact.relative_path == "7/daily_basic.jsonl.gz"
    payload = artifact.path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == artifact.sha256
    with gzip.open(artifact.path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    assert rows == [VALUATION_ROW]
    assert not list(artifact.path.parent.glob("*.tmp"))


def test_complete_run_persists_sanitized_failure(tmp_path: Path) -> None:
    with open_shadow_store(tmp_path / "shadow.db") as conn:
        run_id = create_ingestion_run(conn, "income", "fp", "2026-07-21T01:00:00+00:00")
        complete_ingestion_run(
            conn,
            run_id,
            status="failed",
            completed_at="2026-07-21T01:01:00+00:00",
            error_code="PERMISSION_DENIED",
            error_message="TUSHARE_TOKEN=secret-token Authorization: Bearer abc",
        )
        status, message = conn.execute(
            "SELECT status, error_message FROM ingestion_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    assert status == "failed"
    assert "secret-token" not in message
    assert "Bearer abc" not in message
    assert sanitize_error_message(message) == message
