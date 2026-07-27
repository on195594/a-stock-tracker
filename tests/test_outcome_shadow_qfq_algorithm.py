"""qfq_total_return_v1 algorithm contract tests.

All writes use tmp_path databases. Tests never access the production database or network.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import a_stock_tracker.data.outcome_shadow as outcome_shadow
from a_stock_tracker.data.outcome_shadow import (
    EXTENDED_PROTECTION_COLUMNS,
    PROTECTION_COLUMNS,
    QFQ_TOTAL_RETURN_V1,
    RAW_PRICE_RETURN_V1,
    ShadowContractError,
    build_shadow_candidate,
    extended_predictions_protection,
    inspect_frozen_cohort,
)

QFQ_SOURCE = "tushare.pro_bar.qfq"


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
    conn: sqlite3.Connection,
    code: str,
    trade_date: str,
    close: float,
    *,
    source: str = QFQ_SOURCE,
    adjusted: str = "qfq",
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
            adjusted,
            "share",
            "2026-07-23T10:00:00Z",
            "ok",
            None,
        ),
    )


def _write_benchmark(path: Path, rows: list[tuple[str, float]], *, symbol: str = "H00300.CSI") -> None:
    payload = {
        "schema_version": 1,
        "source": "tushare.index_daily",
        "symbol": symbol,
        "fetched_at": "2026-07-23T10:00:00Z",
        "rows": [{"trade_date": trade_date, "close": close} for trade_date, close in rows],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed(path: Path) -> None:
    _create_source(path)
    conn = sqlite3.connect(path)
    _insert_prediction(conn)
    for trade_date, close in [("2026-01-05", 100.0), ("2026-02-04", 110.0)]:
        _insert_bar(conn, "600036", trade_date, close)
    conn.commit()
    conn.close()


# --- guard rails on the protection column sets ---------------------------------


def test_protection_columns_constant_is_not_polluted() -> None:
    """Phase 1/2 runs hash over exactly these columns; adding to them breaks replay."""
    assert PROTECTION_COLUMNS == (
        "id",
        "code",
        "framework",
        "score_date",
        "price_at_score",
        "quant_score",
        "total_score",
        "weights_hash",
        "report_period",
        "created_at",
    )
    assert not any(column.startswith(("outcome_", "benchmark_")) for column in PROTECTION_COLUMNS)


def test_extended_protection_is_a_strict_superset() -> None:
    assert set(PROTECTION_COLUMNS) < set(EXTENDED_PROTECTION_COLUMNS)
    assert EXTENDED_PROTECTION_COLUMNS[: len(PROTECTION_COLUMNS)] == PROTECTION_COLUMNS
    for window in (30, 60, 90):
        assert f"outcome_{window}d" in EXTENDED_PROTECTION_COLUMNS
        assert f"benchmark_{window}d" in EXTENDED_PROTECTION_COLUMNS


def test_extended_protection_detects_outcome_rewrite_that_ordinary_hash_misses(tmp_path: Path) -> None:
    """The whole point of the extended hash: outcome columns must be pinned."""
    source = tmp_path / "source.db"
    _seed(source)

    conn = sqlite3.connect(source)
    conn.row_factory = sqlite3.Row
    ordinary_before = outcome_shadow._predictions_protection(conn)
    extended_before = extended_predictions_protection(conn)

    conn.execute("UPDATE predictions SET outcome_30d = 999.0 WHERE id = 1")
    conn.commit()

    ordinary_after = outcome_shadow._predictions_protection(conn)
    extended_after = extended_predictions_protection(conn)
    conn.close()

    assert ordinary_after == ordinary_before, "ordinary hash is blind to outcome rewrites"
    assert extended_after != extended_before, "extended hash must catch outcome rewrites"


# --- algorithm configuration ---------------------------------------------------


def test_qfq_algorithm_contract_values() -> None:
    assert QFQ_TOTAL_RETURN_V1.version == "qfq_total_return_v1"
    assert QFQ_TOTAL_RETURN_V1.stock_adjusted == "qfq"
    assert QFQ_TOTAL_RETURN_V1.stock_source == QFQ_SOURCE
    assert QFQ_TOTAL_RETURN_V1.benchmark_symbol == "H00300.CSI"
    assert QFQ_TOTAL_RETURN_V1.windows == (30, 60)
    assert QFQ_TOTAL_RETURN_V1.compute_stored_entry is False
    assert QFQ_TOTAL_RETURN_V1.use_extended_protection is True


def test_default_algorithm_preserves_phase1_behaviour() -> None:
    assert RAW_PRICE_RETURN_V1.version == outcome_shadow.ALGORITHM_VERSION
    assert RAW_PRICE_RETURN_V1.stock_adjusted == "none"
    assert RAW_PRICE_RETURN_V1.stock_source == outcome_shadow.STOCK_SOURCE
    assert RAW_PRICE_RETURN_V1.benchmark_symbol == "000300"
    assert RAW_PRICE_RETURN_V1.windows == outcome_shadow.WINDOW_DAYS
    assert RAW_PRICE_RETURN_V1.compute_stored_entry is True
    assert RAW_PRICE_RETURN_V1.use_extended_protection is False


def test_qfq_algorithm_excludes_90d_window(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed(source)
    inspection = inspect_frozen_cohort(source, "2026-06-30", QFQ_TOTAL_RETURN_V1)
    assert set(inspection.window_counts) == {30, 60}


def test_inspection_hash_matches_the_variant_the_build_will_use(tmp_path: Path) -> None:
    """An inspection whose hash cannot be compared against the run it previews is useless."""
    source = tmp_path / "source.db"
    _seed(source)

    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        ordinary_hash = outcome_shadow._predictions_protection(conn)[1]
        extended_hash = extended_predictions_protection(conn)[1]
    assert ordinary_hash != extended_hash, "fixture must distinguish the two variants"

    qfq = inspect_frozen_cohort(source, "2026-06-30", QFQ_TOTAL_RETURN_V1)
    assert qfq.predictions_protection_hash == extended_hash

    default = inspect_frozen_cohort(source, "2026-06-30")
    assert default.predictions_protection_hash == ordinary_hash


def test_unsupported_window_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed(source)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(ShadowContractError, match="UNSUPPORTED_WINDOW:45"):
            outcome_shadow._load_frozen_events(conn, "2026-06-30", (45,))


# --- source purity -------------------------------------------------------------


def test_qfq_load_rejects_shadow_rows_sharing_the_same_source(tmp_path: Path) -> None:
    """qfq and qfq_tushare_shadow share a source value; adjusted must also be pinned."""
    source = tmp_path / "source.db"
    _seed(source)
    conn = sqlite3.connect(source)
    _insert_bar(conn, "600036", "2026-01-06", 105.0, adjusted="qfq_tushare_shadow")
    conn.commit()
    conn.close()

    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        events = outcome_shadow._load_frozen_events(conn, "2026-06-30", QFQ_TOTAL_RETURN_V1.windows)
        observations = outcome_shadow._load_stock_observations(conn, events, "2026-06-30", QFQ_TOTAL_RETURN_V1)

    assert observations, "expected the production qfq rows to load"
    assert {row.adjusted for row in observations} == {"qfq"}


def test_qfq_load_rejects_foreign_source(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed(source)
    conn = sqlite3.connect(source)
    conn.execute("UPDATE daily_bars SET source='retired.source' WHERE trade_date='2026-02-04'")
    conn.commit()
    conn.close()

    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        events = outcome_shadow._load_frozen_events(conn, "2026-06-30", QFQ_TOTAL_RETURN_V1.windows)
        with pytest.raises(ShadowContractError, match="SOURCE_IMPURE"):
            outcome_shadow._load_stock_observations(conn, events, "2026-06-30", QFQ_TOTAL_RETURN_V1)


# --- benchmark symbol ----------------------------------------------------------


def test_qfq_requires_total_return_symbol(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed(source)
    snapshot = tmp_path / "benchmark.json"
    _write_benchmark(snapshot, [("2026-01-05", 1.0)], symbol="000300")

    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        events = outcome_shadow._load_frozen_events(conn, "2026-06-30", QFQ_TOTAL_RETURN_V1.windows)

    with pytest.raises(ShadowContractError, match="BENCHMARK_SOURCE_INVALID"):
        outcome_shadow._load_benchmark_snapshot(snapshot, events, "2026-06-30", QFQ_TOTAL_RETURN_V1)


def test_default_algorithm_still_requires_price_symbol(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed(source)
    snapshot = tmp_path / "benchmark.json"
    _write_benchmark(snapshot, [("2026-01-05", 1.0)], symbol="H00300.CSI")

    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        events = outcome_shadow._load_frozen_events(conn, "2026-06-30")

    with pytest.raises(ShadowContractError, match="BENCHMARK_SOURCE_INVALID"):
        outcome_shadow._load_benchmark_snapshot(snapshot, events, "2026-06-30")


# --- end to end ----------------------------------------------------------------


def test_qfq_build_writes_null_stored_entry_and_new_algorithm_version(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed(source)
    snapshot = tmp_path / "benchmark.json"
    _write_benchmark(snapshot, [("2026-01-05", 1000.0), ("2026-02-04", 1010.0)])
    candidate = tmp_path / "candidate.db"

    result = build_shadow_candidate(source, candidate, snapshot, as_of_date="2026-06-30", algorithm=QFQ_TOTAL_RETURN_V1)

    assert result.event_count > 0
    with sqlite3.connect(f"file:{candidate}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM outcome_shadow_runs").fetchone()
        rows = conn.execute("SELECT * FROM outcome_shadow_results").fetchall()

    assert run["algorithm_version"] == "qfq_total_return_v1"
    assert rows, "expected at least one shadow result"
    for row in rows:
        assert row["stored_entry_shadow_outcome"] is None
        assert row["stored_entry_shadow_alpha"] is None
    assert {row["adjusted"] for row in rows} <= {"qfq"}
