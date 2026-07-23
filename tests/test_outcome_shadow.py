"""Historical outcome shadow Phase 1 contract tests.

All writes use tmp_path databases. Tests never access the production database or network.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from a_stock_tracker import cli
import a_stock_tracker.data.outcome_shadow as outcome_shadow
from a_stock_tracker.data.outcome_shadow import (
    ShadowContractError,
    build_shadow_candidate,
    inspect_frozen_cohort,
    resolve_observation,
    write_shadow_report,
)


def _create_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL,
            name TEXT,
            framework TEXT NOT NULL,
            score_date TEXT NOT NULL,
            price_at_score REAL,
            quant_score REAL,
            total_score REAL,
            weights_hash TEXT,
            report_period TEXT,
            outcome_30d REAL,
            outcome_60d REAL,
            outcome_90d REAL,
            benchmark_30d REAL,
            benchmark_60d REAL,
            benchmark_90d REAL,
            alpha_30d REAL GENERATED ALWAYS AS (outcome_30d - benchmark_30d) VIRTUAL,
            alpha_60d REAL GENERATED ALWAYS AS (outcome_60d - benchmark_60d) VIRTUAL,
            alpha_90d REAL GENERATED ALWAYS AS (outcome_90d - benchmark_90d) VIRTUAL,
            created_at TEXT
        );
        CREATE TABLE daily_bars (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL NOT NULL,
            volume REAL,
            source TEXT NOT NULL,
            adjusted TEXT NOT NULL,
            volume_unit TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            quality_status TEXT NOT NULL,
            error_code TEXT,
            PRIMARY KEY(code, trade_date, adjusted)
        );
        """
    )
    conn.commit()
    conn.close()


def _insert_prediction(conn: sqlite3.Connection, *, row_id: int = 1, code: str = "600036") -> None:
    conn.execute(
        """INSERT INTO predictions
           (id,code,name,framework,score_date,price_at_score,quant_score,total_score,
            weights_hash,report_period,outcome_30d,benchmark_30d,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row_id,
            code,
            "测试",
            "A",
            "2026-01-05",
            100.0,
            50.0,
            60.0,
            "hash",
            "2025Q3",
            20.0,
            10.0,
            "2026-01-05T17:30:00",
        ),
    )


def _insert_bar(
    conn: sqlite3.Connection, code: str, trade_date: str, close: float, *, source: str = "tushare.daily"
) -> None:
    conn.execute(
        """INSERT INTO daily_bars
           (code,trade_date,open,high,low,close,volume,source,adjusted,volume_unit,
            fetched_at,quality_status,error_code)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            code,
            trade_date,
            close,
            close,
            close,
            close,
            1000.0,
            source,
            "none",
            "share",
            "2026-07-23T10:00:00Z",
            "ok",
            None,
        ),
    )


def _write_benchmark(path: Path, rows: list[tuple[str, float]]) -> None:
    payload = _benchmark_payload(rows)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _benchmark_payload(rows: list[tuple[str, float]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": "tushare.index_daily",
        "symbol": "000300",
        "fetched_at": "2026-07-23T10:00:00Z",
        "rows": [{"trade_date": trade_date, "close": close} for trade_date, close in rows],
    }


def _seed_complete_source(path: Path, *, stock_entry_date: str = "2026-01-05") -> None:
    _create_source(path)
    conn = sqlite3.connect(path)
    _insert_prediction(conn)
    _insert_bar(conn, "600036", stock_entry_date, 110.0)
    _insert_bar(conn, "600036", "2026-02-04", 121.0)
    conn.commit()
    conn.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inspect_frozen_cohort_is_read_only_and_does_not_create_candidate(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    _seed_complete_source(source)
    before = _sha256(source)

    inspection = inspect_frozen_cohort(source, "2026-02-10")

    assert inspection.event_count == 1
    assert inspection.window_counts == {30: 1, 60: 0, 90: 0}
    assert _sha256(source) == before
    assert candidate.exists() is False
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'outcome_shadow_%'").fetchone()[0] == 0


def test_resolve_observation_ignores_future_and_enforces_ten_calendar_days() -> None:
    observations = {
        "2026-01-01": 10.0,
        "2026-01-11": 11.0,
        "2026-01-12": 12.0,
    }

    assert resolve_observation(observations, "2026-01-11") == ("2026-01-11", 11.0, 0)
    assert resolve_observation({"2026-01-01": 10.0, "2026-01-12": 12.0}, "2026-01-11") == (
        "2026-01-01",
        10.0,
        10,
    )
    assert resolve_observation({"2025-12-31": 9.0, "2026-01-12": 12.0}, "2026-01-11") is None


def test_candidate_separates_stored_entry_and_reconstructed_outcomes(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    _seed_complete_source(source)
    _write_benchmark(benchmark, [("2026-01-05", 100.0), ("2026-02-04", 105.0)])

    result = build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")

    assert result.event_count == 1
    assert result.computed_count == 1
    conn = sqlite3.connect(candidate)
    row = conn.execute(
        """SELECT status, stored_entry_shadow_outcome, reconstructed_shadow_outcome,
                  shadow_benchmark, stored_entry_shadow_alpha, reconstructed_shadow_alpha,
                  entry_reconstruction
           FROM outcome_shadow_results"""
    ).fetchone()
    conn.close()
    assert row[0] == "computed_aligned"
    assert row[1] == pytest.approx(21.0)
    assert row[2] == pytest.approx(10.0)
    assert row[3] == pytest.approx(5.0)
    assert row[4] == pytest.approx(16.0)
    assert row[5] == pytest.approx(5.0)
    assert row[6] == "ex_post_reconstructed"


def test_missing_stock_target_is_preserved_as_result_row(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    _create_source(source)
    conn = sqlite3.connect(source)
    _insert_prediction(conn)
    _insert_bar(conn, "600036", "2026-01-05", 110.0)
    conn.commit()
    conn.close()
    _write_benchmark(benchmark, [("2026-01-05", 100.0), ("2026-02-04", 105.0)])

    result = build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")

    assert result.event_count == 1
    assert result.missing_count == 1
    with sqlite3.connect(candidate) as conn:
        row = conn.execute("SELECT status,target_actual_trade_date,target_price FROM outcome_shadow_results").fetchone()
    assert row == ("missing_stock_target", None, None)


def test_calendar_mismatch_is_not_counted_as_aligned_alpha(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    _seed_complete_source(source, stock_entry_date="2026-01-04")
    _write_benchmark(benchmark, [("2026-01-05", 100.0), ("2026-02-04", 105.0)])

    build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")

    with sqlite3.connect(candidate) as conn:
        row = conn.execute(
            "SELECT status,entry_actual_trade_date,benchmark_entry_actual_trade_date,aligned_alpha_eligible FROM outcome_shadow_results"
        ).fetchone()
    assert row == ("computed_calendar_mismatch", "2026-01-04", "2026-01-05", 0)


def test_identical_inputs_produce_identical_run_and_row_hashes(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    benchmark = tmp_path / "benchmark.json"
    _seed_complete_source(source)
    _write_benchmark(benchmark, [("2026-01-05", 100.0), ("2026-02-04", 105.0)])

    first = build_shadow_candidate(source, tmp_path / "candidate-a.db", benchmark, as_of_date="2026-02-10")
    second = build_shadow_candidate(source, tmp_path / "candidate-b.db", benchmark, as_of_date="2026-02-10")

    assert first.run_id == second.run_id
    assert first.manifest_hash == second.manifest_hash
    with sqlite3.connect(tmp_path / "candidate-a.db") as a, sqlite3.connect(tmp_path / "candidate-b.db") as b:
        assert (
            a.execute("SELECT row_hash FROM outcome_shadow_results").fetchall()
            == b.execute("SELECT row_hash FROM outcome_shadow_results").fetchall()
        )


def test_descriptive_and_legacy_changes_do_not_mutate_cohort_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed_complete_source(source)
    before = inspect_frozen_cohort(source, "2026-02-10")
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE predictions SET name='新名称', outcome_30d=99.0")
    descriptive_change = inspect_frozen_cohort(source, "2026-02-10")
    assert descriptive_change.cohort_hash == before.cohort_hash
    assert descriptive_change.predictions_protection_hash == before.predictions_protection_hash

    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE predictions SET score_date='2026-01-06'")
    identity_change = inspect_frozen_cohort(source, "2026-02-10")
    assert identity_change.cohort_hash != before.cohort_hash
    assert identity_change.predictions_protection_hash != before.predictions_protection_hash


def test_candidate_path_must_not_exist_and_source_impurity_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    _seed_complete_source(source)
    _write_benchmark(benchmark, [("2026-01-05", 100.0), ("2026-02-04", 105.0)])
    candidate.touch()

    with pytest.raises(ShadowContractError, match="CANDIDATE_ALREADY_EXISTS"):
        build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")

    candidate.unlink()
    conn = sqlite3.connect(source)
    conn.execute("UPDATE daily_bars SET source='retired.source' WHERE trade_date='2026-02-04'")
    conn.commit()
    conn.close()
    with pytest.raises(ShadowContractError, match="SOURCE_IMPURE"):
        build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")


@pytest.mark.parametrize(
    ("override", "error_code"),
    [
        ({"schema_version": 2}, "BENCHMARK_SCHEMA_VERSION_INVALID"),
        ({"source": "legacy.index"}, "BENCHMARK_SOURCE_INVALID"),
        ({"symbol": "000905"}, "BENCHMARK_SOURCE_INVALID"),
        ({"fetched_at": ""}, "BENCHMARK_FETCHED_AT_MISSING"),
        ({"fetched_at": "not-a-time"}, "BENCHMARK_FETCHED_AT_INVALID"),
        ({"rows": "not-a-list"}, "BENCHMARK_ROWS_INVALID"),
        ({"rows": ["not-an-object"]}, "BENCHMARK_ROW_INVALID"),
        ({"rows": [{"trade_date": "2026-02-30", "close": 100.0}]}, "BENCHMARK_DATE_INVALID"),
        ({"rows": [{"trade_date": "2026-01-05", "close": True}]}, "BENCHMARK_CLOSE_INVALID"),
        (
            {
                "rows": [
                    {"trade_date": "2026-01-05", "close": 100.0},
                    {"trade_date": "2026-01-05", "close": 101.0},
                ]
            },
            "BENCHMARK_DUPLICATE_DATE",
        ),
        ({"rows": [{"trade_date": "2020-01-01", "close": 100.0}]}, "BENCHMARK_COVERAGE_EMPTY"),
    ],
)
def test_benchmark_snapshot_validation_fails_closed(
    tmp_path: Path,
    override: dict[str, object],
    error_code: str,
) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    _seed_complete_source(source)
    payload = _benchmark_payload([("2026-01-05", 100.0), ("2026-02-04", 105.0)])
    payload.update(override)
    benchmark.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ShadowContractError, match=error_code):
        build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")
    assert candidate.exists() is False


def test_build_reads_from_consistent_candidate_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    _seed_complete_source(source)
    _write_benchmark(benchmark, [("2026-01-05", 100.0), ("2026-02-04", 105.0)])
    original_copy = outcome_shadow._copy_source_database

    def copy_then_mutate_source(source_path: Path, candidate_path: Path) -> None:
        original_copy(source_path, candidate_path)
        with sqlite3.connect(source_path) as conn:
            conn.execute("UPDATE predictions SET score_date='2026-01-06'")

    monkeypatch.setattr(outcome_shadow, "_copy_source_database", copy_then_mutate_source)
    build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")

    with sqlite3.connect(source) as source_conn, sqlite3.connect(candidate) as candidate_conn:
        source_date = source_conn.execute("SELECT score_date FROM predictions").fetchone()[0]
        prediction_date = candidate_conn.execute("SELECT score_date FROM predictions").fetchone()[0]
        shadow_date = candidate_conn.execute("SELECT score_date FROM outcome_shadow_results").fetchone()[0]
    assert source_date == "2026-01-06"
    assert prediction_date == shadow_date == "2026-01-05"


def test_report_is_read_only_and_separates_difference_layers(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    output = tmp_path / "report.json"
    _seed_complete_source(source)
    _write_benchmark(benchmark, [("2026-01-05", 100.0), ("2026-02-04", 105.0)])
    build = build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")
    candidate_before = _sha256(candidate)

    report = write_shadow_report(candidate, build.run_id, output)

    payload = json.loads(report.json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["old_vs_stored_entry_shadow_changed"] == 1
    assert payload["summary"]["stored_vs_reconstructed_changed"] == 1
    assert payload["summary"]["old_vs_shadow_benchmark_changed"] == 1
    assert len(payload["details"]) == 1
    assert report.markdown_path.exists()
    assert _sha256(candidate) == candidate_before


def test_report_requires_json_output_extension(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    _seed_complete_source(source)
    _write_benchmark(benchmark, [("2026-01-05", 100.0), ("2026-02-04", 105.0)])
    build = build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")

    with pytest.raises(ShadowContractError, match="REPORT_OUTPUT_MUST_BE_JSON"):
        write_shadow_report(candidate, build.run_id, tmp_path / "report.txt")


def test_shadow_tables_reject_update_and_delete(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    candidate = tmp_path / "candidate.db"
    benchmark = tmp_path / "benchmark.json"
    _seed_complete_source(source)
    _write_benchmark(benchmark, [("2026-01-05", 100.0), ("2026-02-04", 105.0)])
    build_shadow_candidate(source, candidate, benchmark, as_of_date="2026-02-10")

    statements = (
        "UPDATE outcome_shadow_runs SET created_at=created_at",
        "DELETE FROM outcome_shadow_runs",
        "UPDATE outcome_shadow_observations SET close=close",
        "DELETE FROM outcome_shadow_observations",
        "UPDATE outcome_shadow_results SET status=status",
        "DELETE FROM outcome_shadow_results",
    )
    with sqlite3.connect(candidate) as conn:
        for statement in statements:
            with pytest.raises(sqlite3.IntegrityError, match="OUTCOME_SHADOW_IMMUTABLE"):
                conn.execute(statement)
        assert conn.execute("SELECT COUNT(*) FROM outcome_shadow_results").fetchone()[0] == 1


def test_cli_dry_run_does_not_require_candidate_or_benchmark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.db"
    _seed_complete_source(source)
    before = _sha256(source)

    cli.cmd_outcome_shadow_build(source_db=str(source), as_of_date="2026-02-10", dry_run=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["event_count"] == 1
    assert _sha256(source) == before


def test_cli_dry_run_rejects_ignored_artifact_arguments(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed_complete_source(source)

    with pytest.raises(ValueError, match="not accepted with --dry-run"):
        cli.cmd_outcome_shadow_build(
            source_db=str(source),
            as_of_date="2026-02-10",
            dry_run=True,
            candidate_db=str(tmp_path / "candidate.db"),
        )


def test_main_dispatches_outcome_shadow_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}
    monkeypatch.setattr(cli, "cmd_outcome_shadow_build", lambda **kwargs: called.update(kwargs))
    monkeypatch.setattr(
        "sys.argv",
        [
            "pipeline.py",
            "outcome-shadow-build",
            "--source-db",
            "/tmp/source.db",
            "--as-of-date",
            "2026-07-23",
            "--dry-run",
        ],
    )

    cli.main()

    assert called == {
        "source_db": "/tmp/source.db",
        "as_of_date": "2026-07-23",
        "dry_run": True,
        "candidate_db": None,
        "benchmark_snapshot": None,
    }
