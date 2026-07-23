"""Contract tests for additive outcome-shadow production migration."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from a_stock_tracker import cli
import a_stock_tracker.data.outcome_shadow_migration as migration
from a_stock_tracker.data.outcome_shadow import build_shadow_candidate
from a_stock_tracker.data.outcome_shadow_migration import (
    MigrationContractError,
    MigrationEvidenceError,
    apply_shadow_import,
    inspect_shadow_import,
    revert_shadow_import,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_production(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT,
                framework TEXT NOT NULL, score_date TEXT NOT NULL,
                price_at_score REAL, quant_score REAL, total_score REAL,
                weights_hash TEXT, report_period TEXT, outcome_30d REAL,
                outcome_60d REAL, outcome_90d REAL, benchmark_30d REAL,
                benchmark_60d REAL, benchmark_90d REAL,
                alpha_30d REAL GENERATED ALWAYS AS (outcome_30d - benchmark_30d) VIRTUAL,
                alpha_60d REAL GENERATED ALWAYS AS (outcome_60d - benchmark_60d) VIRTUAL,
                alpha_90d REAL GENERATED ALWAYS AS (outcome_90d - benchmark_90d) VIRTUAL,
                created_at TEXT
            );
            CREATE TABLE daily_bars (
                code TEXT NOT NULL, trade_date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL,
                source TEXT NOT NULL, adjusted TEXT NOT NULL,
                volume_unit TEXT NOT NULL, fetched_at TEXT NOT NULL,
                quality_status TEXT NOT NULL, error_code TEXT,
                PRIMARY KEY(code, trade_date, adjusted)
            );
            """
        )
        conn.execute(
            """INSERT INTO predictions
               (id,code,name,framework,score_date,price_at_score,quant_score,total_score,
                weights_hash,report_period,outcome_30d,benchmark_30d,created_at)
               VALUES(1,'600036','测试','A','2026-01-05',100,50,60,'hash','2025Q3',20,10,
                      '2026-01-05T17:30:00')"""
        )
        for trade_date, close in (("2026-01-05", 110.0), ("2026-02-04", 121.0)):
            conn.execute(
                """INSERT INTO daily_bars
                   VALUES('600036',?,?,?,?,?,?, 'tushare.daily','none','share',
                          '2026-07-23T10:00:00Z','ok',NULL)""",
                (trade_date, close, close, close, close, 1000.0),
            )


def _make_candidate(tmp_path: Path) -> tuple[Path, Path, str, str]:
    production = tmp_path / "production.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    _seed_production(production)
    benchmark.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "tushare.index_daily",
                "symbol": "000300",
                "fetched_at": "2026-07-23T10:00:00Z",
                "rows": [
                    {"trade_date": "2026-01-05", "close": 100.0},
                    {"trade_date": "2026-02-04", "close": 105.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    built = build_shadow_candidate(production, candidate, benchmark, as_of_date="2026-02-10")
    return production, candidate, built.run_id, built.manifest_hash


def _shadow_objects(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'outcome_shadow_%' ORDER BY name")
        ]


def test_inspect_absent_is_read_only(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    before = _sha256(production)

    result = inspect_shadow_import(production, candidate, run_id, manifest)

    assert result.status == "absent"
    assert result.result_count == 1
    assert result.observation_count == 4
    assert _sha256(production) == before
    assert _shadow_objects(production) == []


def test_apply_creates_backup_and_is_idempotent(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    backup = tmp_path / "backup.db"
    evidence = tmp_path / "import.json"

    result = apply_shadow_import(production, candidate, run_id, manifest, backup, evidence)
    replay = apply_shadow_import(
        production,
        candidate,
        run_id,
        manifest,
        tmp_path / "unused-backup.db",
        tmp_path / "replay.json",
    )

    assert result.status == "imported"
    assert replay.status == "already_present"
    assert backup.exists()
    assert evidence.exists()
    assert len(_shadow_objects(production)) == 9
    with sqlite3.connect(production) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM outcome_shadow_results").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM outcome_shadow_observations").fetchone()[0] == 4


def test_candidate_identity_mismatch_fails_before_writing(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    before = _sha256(production)

    with pytest.raises(MigrationContractError, match="CANDIDATE_MANIFEST_MISMATCH"):
        apply_shadow_import(
            production,
            candidate,
            run_id,
            "wrong-manifest",
            tmp_path / "backup.db",
            tmp_path / "evidence.json",
        )

    assert _sha256(production) == before
    assert _shadow_objects(production) == []


def test_partial_shadow_schema_fails_closed(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    with sqlite3.connect(production) as conn:
        conn.execute("CREATE TABLE outcome_shadow_runs(run_id TEXT PRIMARY KEY)")

    with pytest.raises(MigrationContractError, match="PARTIAL_PRESENT"):
        inspect_shadow_import(production, candidate, run_id, manifest)


def test_lock_conflict_does_not_create_shadow_objects(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    locker = sqlite3.connect(production)
    locker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(MigrationContractError, match="PRODUCTION_LOCKED"):
            apply_shadow_import(
                production,
                candidate,
                run_id,
                manifest,
                tmp_path / "backup.db",
                tmp_path / "evidence.json",
            )
    finally:
        locker.rollback()
        locker.close()
    assert _shadow_objects(production) == []


def test_revert_removes_only_shadow_objects(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    apply_shadow_import(
        production,
        candidate,
        run_id,
        manifest,
        tmp_path / "backup.db",
        tmp_path / "import.json",
    )
    with sqlite3.connect(production) as conn:
        prediction_before = conn.execute("SELECT * FROM predictions").fetchall()
        bars_before = conn.execute("SELECT * FROM daily_bars ORDER BY trade_date").fetchall()

    result = revert_shadow_import(production, run_id, manifest, tmp_path / "revert.json", apply=True)

    assert result.status == "reverted"
    assert _shadow_objects(production) == []
    with sqlite3.connect(production) as conn:
        assert conn.execute("SELECT * FROM predictions").fetchall() == prediction_before
        assert conn.execute("SELECT * FROM daily_bars ORDER BY trade_date").fetchall() == bars_before
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_revert_inspect_is_read_only(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    apply_shadow_import(
        production,
        candidate,
        run_id,
        manifest,
        tmp_path / "backup.db",
        tmp_path / "import.json",
    )
    before = _sha256(production)

    result = revert_shadow_import(production, run_id, manifest, tmp_path / "revert.json", apply=False)

    assert result.status == "revert_ready"
    assert _sha256(production) == before


def test_candidate_row_hash_tamper_fails_before_writing(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    with sqlite3.connect(candidate) as conn:
        trigger_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='outcome_shadow_results_update_immutable'"
        ).fetchone()[0]
        conn.execute("DROP TRIGGER outcome_shadow_results_update_immutable")
        conn.execute("UPDATE outcome_shadow_results SET row_hash='tampered'")
        conn.execute(trigger_sql)
    before = _sha256(production)

    with pytest.raises(MigrationContractError, match="RESULT_HASH_MISMATCH"):
        inspect_shadow_import(production, candidate, run_id, manifest)

    assert _sha256(production) == before


def test_transaction_failure_rolls_back_all_shadow_objects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    original_insert = migration._insert_rows

    def fail_on_observations(
        conn: sqlite3.Connection,
        table: str,
        columns: tuple[str, ...],
        rows: tuple[tuple[object, ...], ...],
    ) -> None:
        if table == "outcome_shadow_observations":
            raise RuntimeError("injected failure")
        original_insert(conn, table, columns, rows)

    monkeypatch.setattr(migration, "_insert_rows", fail_on_observations)
    with pytest.raises(RuntimeError, match="injected failure"):
        apply_shadow_import(
            production,
            candidate,
            run_id,
            manifest,
            tmp_path / "backup.db",
            tmp_path / "evidence.json",
        )
    assert _shadow_objects(production) == []


def test_production_change_after_backup_stops_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    original_backup = migration._create_verified_backup

    def backup_then_change(source: Path, backup: Path) -> object:
        snapshot = original_backup(source, backup)
        with sqlite3.connect(source) as conn:
            conn.execute("UPDATE predictions SET name='changed-after-backup'")
        return snapshot

    monkeypatch.setattr(migration, "_create_verified_backup", backup_then_change)
    with pytest.raises(MigrationContractError, match="PRODUCTION_CHANGED_AFTER_BACKUP"):
        apply_shadow_import(
            production,
            candidate,
            run_id,
            manifest,
            tmp_path / "backup.db",
            tmp_path / "evidence.json",
        )
    assert _shadow_objects(production) == []


def test_additional_run_is_diverged(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    apply_shadow_import(
        production,
        candidate,
        run_id,
        manifest,
        tmp_path / "backup.db",
        tmp_path / "import.json",
    )
    with sqlite3.connect(production) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(outcome_shadow_runs)")]
        values = list(conn.execute("SELECT * FROM outcome_shadow_runs").fetchone())
        values[columns.index("run_id")] = "unexpected-run"
        values[columns.index("manifest_hash")] = "unexpected-manifest"
        conn.execute(
            f"INSERT INTO outcome_shadow_runs VALUES ({','.join('?' for _ in values)})",
            values,
        )

    with pytest.raises(MigrationContractError, match="ALREADY_EXISTS_DIVERGED"):
        inspect_shadow_import(production, candidate, run_id, manifest)


def test_revert_rejects_identity_mismatch(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    apply_shadow_import(
        production,
        candidate,
        run_id,
        manifest,
        tmp_path / "backup.db",
        tmp_path / "import.json",
    )

    with pytest.raises(MigrationContractError, match="CANDIDATE_MANIFEST_MISMATCH"):
        revert_shadow_import(
            production,
            run_id,
            "wrong-manifest",
            tmp_path / "revert.json",
            apply=True,
        )


def test_cli_import_inspect_does_not_accept_write_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "inspect_shadow_import",
        lambda *args: (
            called.update(args=args) or migration.MigrationResult("absent", "run", "manifest", 1, 4, "/production")
        ),
        raising=False,
    )

    cli.cmd_outcome_shadow_import(
        production_db="production.db",
        candidate_db="candidate.db",
        expected_run="run",
        expected_manifest="manifest",
        mode="inspect",
    )

    assert called["args"] == ("production.db", "candidate.db", "run", "manifest")


def test_cli_import_apply_requires_backup_and_evidence() -> None:
    with pytest.raises(ValueError, match="backup_db and evidence_json are required"):
        cli.cmd_outcome_shadow_import(
            production_db="production.db",
            candidate_db="candidate.db",
            expected_run="run",
            expected_manifest="manifest",
            mode="apply",
        )


def test_cli_revert_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unsupported revert mode"):
        cli.cmd_outcome_shadow_revert(
            production_db="production.db",
            expected_run="run",
            expected_manifest="manifest",
            evidence_json="evidence.json",
            mode="unknown",
        )


def test_revert_inspect_does_not_require_evidence_path(tmp_path: Path) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    apply_shadow_import(
        production,
        candidate,
        run_id,
        manifest,
        tmp_path / "backup.db",
        tmp_path / "import.json",
    )

    result = revert_shadow_import(production, run_id, manifest, evidence_json=None, apply=False)

    assert result.status == "revert_ready"


def test_evidence_failure_reports_committed_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    production, candidate, run_id, manifest = _make_candidate(tmp_path)
    monkeypatch.setattr(
        migration,
        "_write_evidence",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(MigrationEvidenceError) as caught:
        apply_shadow_import(
            production,
            candidate,
            run_id,
            manifest,
            tmp_path / "backup.db",
            tmp_path / "evidence.json",
        )

    assert caught.value.write_performed is True
    assert caught.value.result.status == "imported"
    assert inspect_shadow_import(production, candidate, run_id, manifest).status == "already_present"
