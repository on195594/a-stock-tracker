"""Temporary-database tests for the authorization-bound fundamentals snapshot."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import stat
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

import a_stock_tracker.qualitative.m5.fundamentals_snapshot as snapshot
from a_stock_tracker.qualitative.m5.data_auth import seal_data_authorization
from scripts.build_qualitative_v2_m5_fundamentals_snapshot import main as cli_main

SAMPLE = Path(__file__).parent / "fixtures" / "milestone005" / "sample.csv"
AUTHORIZATION_ID = "m5-data-readiness-test-01"
DATA_RUN_ID = "m5-data-test-01"
NOT_BEFORE = "2026-07-19T00:00:00+08:00"
NOT_AFTER = "2026-08-01T23:59:59+08:00"
ACTIVE_NOW = datetime.fromisoformat("2026-07-20T12:00:00+08:00")


def _authorization(tmp_path: Path) -> tuple[Path, Path]:
    authorization = tmp_path / "authorization.json"
    checksum = tmp_path / "authorization.sha256"
    seal_data_authorization(
        SAMPLE,
        authorization,
        checksum,
        authorization_id=AUTHORIZATION_ID,
        data_run_id=DATA_RUN_ID,
        not_before=NOT_BEFORE,
        not_after=NOT_AFTER,
    )
    return authorization, checksum


def _database(project_root: Path, *, complete_schema: bool = True) -> Path:
    project_root.mkdir()
    database = project_root / "tracker.db"
    connection = sqlite3.connect(database)
    if complete_schema:
        connection.execute(
            """CREATE TABLE stock_fundamentals (
                code TEXT PRIMARY KEY,
                name TEXT,
                industry TEXT,
                data JSON,
                updated_at TEXT,
                ttl_hours INTEGER
            )"""
        )
    else:
        connection.execute("CREATE TABLE stock_fundamentals (code TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()
    return database


def _sample_rows() -> list[dict[str, str]]:
    return list(csv.DictReader(SAMPLE.read_text(encoding="utf-8").splitlines()))


def _insert(connection: sqlite3.Connection, row: dict[str, str], *, name: str | None = None, data: object) -> None:
    raw_data = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    connection.execute(
        "INSERT INTO stock_fundamentals(code,name,industry,data,updated_at,ttl_hours) VALUES(?,?,?,?,?,?)",
        (
            row["ts_code"].split(".", 1)[0],
            name if name is not None else row["name"],
            row["industry_name"],
            raw_data,
            "2026-07-18T08:00:00+08:00",
            168,
        ),
    )


def _output(project_root: Path) -> Path:
    return project_root / "artifacts" / "milestone-005" / "data" / DATA_RUN_ID / snapshot.SNAPSHOT_FILENAME


def test_snapshot_reads_one_private_transaction_without_database_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    database = _database(project_root)
    rows = _sample_rows()
    connection = sqlite3.connect(database)
    common = {
        "roe_3y_avg": 12.3,
        "net_profit_growth": 8.1,
        "debt_ratio": 42.0,
        "gross_margin": 31.0,
        "report_period": "2025-12-31",
    }
    _insert(connection, rows[0], data=common)
    _insert(connection, rows[1], name="wrong company", data=common)
    _insert(connection, rows[2], data={**common, "report_period": "2026-12-31"})
    _insert(connection, rows[3], data="{not-json")
    connection.commit()
    connection.close()
    before = database.read_bytes()
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", project_root)
    output = _output(project_root)

    result = snapshot.build_fundamentals_snapshot(
        SAMPLE,
        authorization,
        checksum,
        output,
        now=ACTIVE_NOW,
    )

    assert result["validated"] is True
    assert result["database_transactions"] == 1
    assert result["database_writes"] == 0
    assert result["network_calls"] == 0
    assert result["status_counts"] == {
        "future_report_period": 1,
        "identity_mismatch": 1,
        "invalid_data": 1,
        "missing": 32,
        "usable": 1,
    }
    assert database.read_bytes() == before
    assert not (project_root / "tracker.db-journal").exists()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    payload = cast(dict[str, object], json.loads(output.read_text(encoding="utf-8")))
    companies = cast(list[dict[str, object]], payload["companies"])
    assert len(companies) == 36
    assert companies[0]["status"] == "usable"
    assert cast(dict[str, object], companies[0]["values"])["roe_3y_avg"] == 12.3
    assert companies[1]["values"] == {}
    assert cast(dict[str, object], payload["database"])["mode"] == "ro"
    assert cast(dict[str, object], payload["database"])["query_only"] is True


def test_snapshot_rejects_output_outside_authorized_root_before_database_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    _database(project_root)
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(snapshot.sqlite3, "connect", lambda *args, **kwargs: pytest.fail("database opened"))

    with pytest.raises(snapshot.FundamentalsSnapshotError, match="authorization-bound artifact path"):
        snapshot.build_fundamentals_snapshot(
            SAMPLE,
            authorization,
            checksum,
            tmp_path / "outside.json",
            now=ACTIVE_NOW,
        )


def test_snapshot_rejects_existing_output_before_database_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    _database(project_root)
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", project_root)
    output = _output(project_root)
    output.parent.mkdir(parents=True)
    output.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(snapshot.sqlite3, "connect", lambda *args, **kwargs: pytest.fail("database opened"))

    with pytest.raises(snapshot.FundamentalsSnapshotError, match="already exists"):
        snapshot.build_fundamentals_snapshot(SAMPLE, authorization, checksum, output, now=ACTIVE_NOW)

    assert output.read_text(encoding="utf-8") == "existing"


def test_snapshot_rejects_symlinked_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    target = tmp_path / "elsewhere.db"
    sqlite3.connect(target).close()
    (project_root / "tracker.db").symlink_to(target)
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", project_root)

    with pytest.raises(snapshot.FundamentalsSnapshotError, match="non-symlink regular file"):
        snapshot.build_fundamentals_snapshot(
            SAMPLE,
            authorization,
            checksum,
            _output(project_root),
            now=ACTIVE_NOW,
        )


def test_snapshot_rejects_incomplete_schema_without_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    _database(project_root, complete_schema=False)
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", project_root)
    output = _output(project_root)

    with pytest.raises(snapshot.FundamentalsSnapshotError, match="schema is incomplete"):
        snapshot.build_fundamentals_snapshot(SAMPLE, authorization, checksum, output, now=ACTIVE_NOW)

    assert not output.exists()


def test_snapshot_detects_database_identity_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    authorization, checksum = _authorization(tmp_path)
    project_root = tmp_path / "project"
    database = _database(project_root)
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", project_root)
    original_read = snapshot._read_rows

    def drifting_read(uri: str, codes: list[str]) -> tuple[list[tuple[object, ...]], int, int]:
        result = original_read(uri, codes)
        metadata = database.stat()
        os.utime(database, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1))
        return result

    monkeypatch.setattr(snapshot, "_read_rows", drifting_read)
    output = _output(project_root)
    with pytest.raises(snapshot.FundamentalsSnapshotError, match="changed during"):
        snapshot.build_fundamentals_snapshot(SAMPLE, authorization, checksum, output, now=ACTIVE_NOW)
    assert not output.exists()


def test_snapshot_cli_requires_explicit_flag_without_opening_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    authorization, checksum = _authorization(tmp_path)
    monkeypatch.setattr(snapshot.sqlite3, "connect", lambda *args, **kwargs: pytest.fail("database opened"))

    assert (
        cli_main(
            [
                "--sample",
                str(SAMPLE),
                "--authorization",
                str(authorization),
                "--checksum",
                str(checksum),
                "--output",
                str(tmp_path / "unused.json"),
            ]
        )
        == 2
    )
    assert "requires --execute-read-only" in capsys.readouterr().err
