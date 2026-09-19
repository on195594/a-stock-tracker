from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from a_stock_tracker import paths
from a_stock_tracker.paths import EXPERIMENT_MANIFEST_PATH
from a_stock_tracker.reporting.accuracy_report import _parse_as_of, build_accuracy_report, build_accuracy_summary
from a_stock_tracker.reporting.evaluation import (
    CalendarEvidence,
    EVALUATION_VERSION,
    ManifestError,
    QualifiedScore,
    ScoreSection,
    build_bucket_weights,
    evaluate_section,
    load_experiment_manifest,
)
from a_stock_tracker.scoring import score_stock
from a_stock_tracker.signals.l3_v2 import (
    DailyBar,
    DataContractState,
    PricePanel,
    compute_l3_v2_candidate,
)


FIXED_GIT_COMMIT = "69f11c99d1720f2dad07113b46f17f5d87a3975d"
FIXED_CONFIG_SHA256 = "87ac0e4972aa1562ed49b034dfd94172fa1f86b77ab181f9323aabd4b27e3a11"
FIXED_CODES = (
    "000786",
    "000963",
    "002050",
    "002119",
    "002201",
    "002475",
    "002594",
    "002648",
    "002736",
    "600019",
    "600023",
    "600036",
    "600089",
    "600362",
    "600785",
    "600900",
    "600938",
    "600941",
    "600999",
    "601088",
    "601138",
    "601179",
    "601225",
    "601288",
    "601398",
    "601600",
    "601668",
    "601728",
    "601857",
    "601899",
    "601933",
    "601939",
    "601988",
    "601991",
    "603606",
)
FIXED_UNIVERSE_HASH = "cdba80037cdefe10be6eeb4b4e9c4f603b1fd271fd295229ee796162c7d56cfb"
CALENDAR_DATES = ["2026-01-05", "2026-02-04", "2026-03-06", "2026-04-05", "2026-05-05"]
CALENDAR = CalendarEvidence.from_dates(
    CALENDAR_DATES,
    covered_from="2026-01-01",
    covered_to="2026-05-05",
    as_of="2026-05-05",
    source="synthetic-calendar",
)


def _manifest(codes: tuple[str, ...] = FIXED_CODES, *, status: str = "verified") -> dict:
    return {
        "schema_version": 1,
        "experiment_id": "synthetic-s2",
        "framework": "A",
        "universe_source": {
            "repository": "on195594/a-stock-tracker",
            "commit": FIXED_GIT_COMMIT,
            "config_path": "a_stock_tracker/config.py",
            "file_sha256": FIXED_CONFIG_SHA256,
        },
        "expected_codes": list(codes),
        "universe_hash": hashlib.sha256(json.dumps(sorted(codes), separators=(",", ":")).encode()).hexdigest(),
        "scoring_hashes": ["synthetic-scoring-hash"],
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
        "registration_status": status,
        "evidence_ref": "synthetic-fixture-only",
        "windows": [30, 60, 90],
        "minimum_sections": {"30": 3, "60": 2, "90": 1},
        "min_coverage": 0.9,
    }


def _snapshot(score: float, *, pb: float | None = 1.0) -> tuple[str, str, str, str]:
    quant_score = score - 10.0
    scoring = {
        "implementation": {
            "input_policy_version": "2026-09-15.v1",
            "a_stock_lib": "0.1.0",
            "source_sha256": {
                name: "a" * 64
                for name in (
                    "cli.py",
                    "scoring.py",
                    "data/tushare_primary_materialization.py",
                    "qualitative/production.py",
                )
            },
        },
        "scoring_inputs": {
            "pb_percentile_10y": pb,
            "roe_3y_avg": 10.0,
            "net_profit_growth": 10.0,
            "debt_ratio": 40.0,
            "gross_margin": 30.0,
            "moat_fixed": 5,
            "market_pos_fixed": 2,
            "sentiment_fixed": 3,
        },
        "qualitative_as_of": {"moat": None, "market_pos": None, "sentiment": None},
        "weights": json.loads(paths.WEIGHTS_PATH.read_text(encoding="utf-8"))["frameworks"]["A"],
        "result": {
            "quant_score": quant_score,
            "total_score": score,
            "component_scores": {
                "roe_3y_avg": quant_score,
                "net_profit_growth": 0.0,
                "debt_ratio": 0.0,
                "gross_margin": 0.0,
                "pb_percentile_10y": 0.0,
                "moat_fixed": 5.0,
                "market_pos_fixed": 2.0,
                "sentiment_fixed": 3.0,
            },
            "data_quality": 0.8 if pb is None else 1.0,
            "missing_fields": ["pb_percentile_10y"] if pb is None else [],
        },
    }
    qualitative = {"moat": 5, "market_pos": 2, "sentiment": 3}
    sources = {key: "v1" for key in qualitative}
    return (
        json.dumps(scoring, separators=(",", ":")),
        json.dumps(qualitative, separators=(",", ":")),
        json.dumps(sources, separators=(",", ":")),
        "v1",
    )


def _writer_rounding_snapshot(mode: str) -> tuple[str, str, str, str, dict[str, Any]]:
    quantitative = ("roe_3y_avg", "net_profit_growth", "debt_ratio", "gross_margin", "pb_percentile_10y")
    framework: dict[str, dict[str, dict[str, Any]]] = {"fundamental": {}, "valuation": {}}
    for index, field in enumerate(quantitative):
        endpoint = 0.01 if mode == "quant" and index < 3 else 0.03 if mode == "total" and index == 0 else 0.0
        framework["fundamental"][field] = {
            "interpolate": True,
            "breakpoints": [[0.0, 0.0], [1.0, endpoint]],
        }
    for field, value in (("moat_fixed", 5), ("market_pos_fixed", 2), ("sentiment_fixed", 3)):
        framework["valuation"][field] = {"phase1_fixed": value}
    inputs = {field: 0.5 for field in quantitative} | {
        "moat_fixed": 5,
        "market_pos_fixed": 2,
        "sentiment_fixed": 3,
    }
    result = score_stock("000786", "A", inputs, weights={"frameworks": {"A": framework}})
    scoring = {
        "implementation": {
            "input_policy_version": "2026-09-15.v1",
            "a_stock_lib": "0.1.0",
            "source_sha256": {
                name: "a" * 64
                for name in (
                    "cli.py",
                    "scoring.py",
                    "data/tushare_primary_materialization.py",
                    "qualitative/production.py",
                )
            },
        },
        "scoring_inputs": inputs,
        "qualitative_as_of": {"moat": None, "market_pos": None, "sentiment": None},
        "weights": framework,
        "result": result,
    }
    qualitative = {"moat": 5, "market_pos": 2, "sentiment": 3}
    sources = {key: "v1" for key in qualitative}
    return (
        json.dumps(scoring, separators=(",", ":")),
        json.dumps(qualitative, separators=(",", ":")),
        json.dumps(sources, separators=(",", ":")),
        "v1",
        result,
    )


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE predictions (
            code TEXT, framework TEXT, score_date TEXT, quant_score REAL, total_score REAL,
            l3_v2_signal INTEGER, weights_hash TEXT, scoring_snapshot_json TEXT,
            qualitative_snapshot_json TEXT, qualitative_sources_json TEXT,
            qualitative_mode TEXT
        );
        CREATE TABLE daily_bars (
            code TEXT, trade_date TEXT, close REAL, source TEXT, adjusted TEXT
        );
        CREATE TABLE index_prices (symbol TEXT, date TEXT, close REAL);
        """
    )
    return db


def _insert_scores(
    db: sqlite3.Connection,
    codes: tuple[str, ...] = FIXED_CODES,
    dates: tuple[str, ...] = ("2026-01-05", "2026-02-04", "2026-03-06"),
    *,
    tied: bool = False,
    missing_snapshot: set[tuple[str, str]] | None = None,
    missing_prices: set[tuple[str, str]] | None = None,
) -> None:
    missing_snapshot = missing_snapshot or set()
    missing_prices = missing_prices or set()
    for day_index, day in enumerate(CALENDAR_DATES):
        db.execute("INSERT INTO index_prices VALUES ('H00300', ?, ?)", (day, 100.0 + day_index))
    for code_index, code in enumerate(codes):
        score = 10.0 if tied else float(code_index + 1)
        for day_index, day in enumerate(CALENDAR_DATES):
            if (code, day) not in missing_prices:
                close = 100.0 + code_index * 2.0 + day_index * (10.0 + code_index)
                db.execute(
                    "INSERT INTO daily_bars VALUES (?, ?, ?, 'tushare.pro_bar.qfq', 'qfq')",
                    (code, day, close),
                )
            if day in dates:
                if (code, day) in missing_snapshot:
                    snapshot = qualitative = sources = mode = None
                else:
                    snapshot, qualitative, sources, mode = _snapshot(score)
                db.execute(
                    "INSERT INTO predictions VALUES (?, 'A', ?, ?, ?, 0, 'synthetic-scoring-hash', ?, ?, ?, ?)",
                    (code, day, score - 10.0, score, snapshot, qualitative, sources, mode),
                )
    db.commit()


def _summary(db: sqlite3.Connection, manifest: dict | None = None, *, as_of: str = "2026-05-05") -> dict:
    return build_accuracy_summary(
        db,
        manifest=manifest or _manifest(FIXED_CODES[:5]),
        evaluation_as_of=as_of,
        calendar=CALENDAR,
    )


def test_fixed_universe_is_extracted_from_registered_git_source() -> None:
    manifest = load_experiment_manifest(EXPERIMENT_MANIFEST_PATH)
    assert manifest.universe_source["commit"] == FIXED_GIT_COMMIT
    assert manifest.universe_source["file_sha256"] == FIXED_CONFIG_SHA256
    assert manifest.expected_codes == FIXED_CODES
    assert len(FIXED_CODES) == 35
    assert (
        hashlib.sha256(json.dumps(sorted(FIXED_CODES), separators=(",", ":")).encode()).hexdigest()
        == FIXED_UNIVERSE_HASH
    )


def test_t01_expected_universe_does_not_shrink_to_thirty_rows() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:30], dates=("2026-01-05",))
    summary = _summary(db, _manifest())
    window = summary["windows"]["90"]
    assert window["expected"] == 35
    assert window["actual_unique_predictions"] == 30
    assert window["selected_sections"] == 0
    assert "sections_below_minimum:0/1" in window["gaps"]


def test_t02_duplicate_pool_outside_and_conflicting_codes_are_not_filled() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    db.execute(
        "INSERT INTO predictions VALUES (?, 'A', ?, ?, 99, 0, 'synthetic-scoring-hash', ?, ?, ?, ?)",
        (FIXED_CODES[0], "2026-01-05", 89.0, *_snapshot(99)),
    )
    db.execute(
        "INSERT INTO predictions VALUES (?, 'A', ?, ?, 99, 0, 'synthetic-scoring-hash', ?, ?, ?, ?)",
        ("999999", "2026-01-05", 89.0, *_snapshot(99)),
    )
    summary = _summary(db)
    assert any(gap.startswith("conflicting_prediction") for gap in summary["gaps"])
    assert any(gap.startswith("pool_outside") for gap in summary["gaps"])
    assert summary["windows"]["90"]["selected_sections"] == 0


def test_t03_damaged_snapshot_is_a_visible_qualification_gap() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",), missing_snapshot={(FIXED_CODES[0], "2026-01-05")})
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0
    assert any("scoring_snapshot_missing" in gap for gap in summary["gaps"])


def test_t04_missing_pb_is_allowed_when_frozen_snapshot_is_honest() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    score, qualitative, sources, mode = _snapshot(1.0, pb=None)
    db.execute("UPDATE predictions SET scoring_snapshot_json=? WHERE code=?", (score, FIXED_CODES[0]))
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 1
    assert not any("snapshot" in gap for gap in summary["gaps"])


def test_t05_all_tied_is_manual_review_and_code_order_does_not_break_ties() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], tied=True)
    summary = _summary(db)
    assert summary["evidence_status"] == "READY_FOR_DIRECTION_REVIEW"
    assert summary["verdict"] == "MANUAL_REVIEW_REQUIRED"
    assert summary["windows"]["90"]["metrics"]["ic"] is None
    assert "ALL_SCORES_TIED" in summary["windows"]["90"]["gaps"]


def test_t06_fractional_boundary_weights_sum_to_one_and_are_permutation_invariant() -> None:
    scores = {f"6000{i:02d}": 10.0 if i < 2 else float(i) for i in range(10)}
    weights = build_bucket_weights(scores, "q5")
    permuted = build_bucket_weights(dict(reversed(list(scores.items()))), "q5")
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["600000"] == pytest.approx(0.5)
    assert weights["600001"] == pytest.approx(0.5)
    assert weights == permuted


def test_t07_no_tie_fixture_keeps_fixed_top_bucket_metrics() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    summary = _summary(db)
    metrics = summary["windows"]["90"]["metrics"]
    assert metrics["q5_return"] > 0
    assert metrics["q5_minus_equal"] != 0


def test_t08_endpoint_and_daily_path_use_the_same_frozen_weights() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:10], dates=("2026-01-05",))
    summary = _summary(db, _manifest(FIXED_CODES[:10]))
    metrics = summary["windows"]["90"]["metrics"]
    assert metrics["q5_return"] > 0
    assert metrics["q5_mdd"] == pytest.approx(0.0)


def test_t09_missing_benchmark_makes_overall_evidence_insufficient() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    db.execute("DELETE FROM index_prices WHERE date='2026-01-05'")
    summary = _summary(db)
    assert summary["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert "benchmark_path_missing" in summary["gaps"]


def test_t10_manifest_never_registers_an_unknown_hash_or_universe() -> None:
    bad = _manifest(FIXED_CODES[:5])
    bad["universe_hash"] = "not-registered"
    with pytest.raises(ManifestError):
        load_experiment_manifest(bad)
    pending = load_experiment_manifest(_manifest(status="pending"))
    assert pending.registration_status == "pending"


def test_t11_report_is_read_only_and_does_not_change_raw_predictions() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    before = db.execute("SELECT * FROM predictions ORDER BY code, score_date").fetchall()
    build_accuracy_report(db, manifest=_manifest(FIXED_CODES[:5]), evaluation_as_of="2026-05-05", calendar=CALENDAR)
    assert db.execute("SELECT * FROM predictions ORDER BY code, score_date").fetchall() == before


def test_t12_complete_all_tied_report_does_not_turn_na_into_zero() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], tied=True)
    report = build_accuracy_report(
        db, manifest=_manifest(FIXED_CODES[:5]), evaluation_as_of="2026-05-05", calendar=CALENDAR
    )
    assert "ALL_SCORES_TIED" in report
    assert "IC、spread、Q5排序优势=N/A" in report
    assert "Q5−Q1 平均 alpha spread：0.00%" not in report


def test_t13_missing_selected_member_is_not_replaced_or_renormalized() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",), missing_prices={(FIXED_CODES[4], "2026-04-05")})
    summary = _summary(db)
    window = summary["windows"]["90"]
    assert window["selected_dates"] == ["2026-01-05"]
    assert window["metrics"]["q5_return"] is None
    assert "stock_path_missing:002201" in window["gaps"]


def test_t14_earliest_incomplete_batch_is_not_shifted_to_the_next_day() -> None:
    db = _db()
    _insert_scores(
        db, FIXED_CODES[:5], dates=("2026-01-05", "2026-02-04"), missing_prices={(FIXED_CODES[4], "2026-02-04")}
    )
    summary = _summary(db)
    assert summary["windows"]["30"]["selected_dates"] == ["2026-01-05", "2026-02-04"]


def test_t15_incomplete_middle_batch_remains_a_gap_when_later_batch_recovers() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], missing_prices={(FIXED_CODES[4], "2026-03-06")})
    summary = _summary(db)
    assert summary["windows"]["30"]["selected_dates"] == ["2026-01-05", "2026-02-04", "2026-03-06"]
    assert summary["evidence_status"] == "INSUFFICIENT_EVIDENCE"


def test_t16_explicit_manifest_path_is_independent_of_cwd(tmp_path, monkeypatch) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest(FIXED_CODES[:5])), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    report = build_accuracy_report(db, manifest=path, evaluation_as_of="2026-05-05", calendar=CALENDAR)
    assert "experiment_id：synthetic-s2" in report


def test_t17_missing_invalid_and_pending_manifests_fail_closed() -> None:
    db = _db()
    missing = build_accuracy_report(db, manifest="/tmp/does-not-exist-s2-manifest.json")
    assert "INSUFFICIENT_EVIDENCE" in missing
    pending = build_accuracy_report(db, manifest=_manifest(status="pending"))
    assert "manifest_pending" in pending
    invalid = _manifest()
    invalid["min_coverage"] = 0.8
    assert "min_coverage" in build_accuracy_report(db, manifest=invalid)


def test_t18_snapshot_score_conflict_is_not_recomputed_or_smoothed() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    snapshot, *_ = _snapshot(999.0)
    db.execute("UPDATE predictions SET scoring_snapshot_json=? WHERE code=?", (snapshot, FIXED_CODES[0]))
    summary = _summary(db)
    assert any("snapshot_total_score_conflict" in gap for gap in summary["gaps"])
    assert summary["windows"]["90"]["selected_sections"] == 0


def test_t19_without_proven_calendar_report_does_not_self_prove_from_latest_prices(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "trading_calendar_path", lambda: tmp_path / "missing-calendar.json")
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    summary = build_accuracy_summary(db, manifest=_manifest(FIXED_CODES[:5]), evaluation_as_of="2026-05-05")
    assert summary["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert "calendar_unavailable" in summary["gaps"]


def test_t20_one_structured_summary_covers_all_windows_without_cohort_writes() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5])
    summary = _summary(db)
    assert set(summary["windows"]) == {"30", "60", "90"}
    assert summary["evaluation_version"] == EVALUATION_VERSION
    assert db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 15


def test_counterexample_truncated_calendar_cannot_mature_d90() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    truncated = CalendarEvidence.from_dates(
        CALENDAR_DATES[:3],
        covered_from="2026-01-01",
        covered_to="2026-05-05",
        as_of="2026-05-05",
        source="synthetic-truncated-calendar",
    )
    summary = build_accuracy_summary(
        db, manifest=_manifest(FIXED_CODES[:5]), evaluation_as_of="2026-05-05", calendar=truncated
    )
    window = summary["windows"]["90"]
    assert window["mature_sections"] == 0
    assert "calendar_endpoint_missing" in window["gaps"]


def test_counterexample_spearman_ranks_returns_not_raw_alpha() -> None:
    entry = date(2026, 1, 1)
    endpoint = date(2026, 1, 31)
    rows = tuple(QualifiedScore(f"60000{i}", entry, float(i), 0) for i in range(1, 4))
    stocks = {row.code: {entry.isoformat(): 100.0, endpoint.isoformat(): 100.0 + i**2} for i, row in enumerate(rows, 1)}
    result = evaluate_section(
        ScoreSection(entry, rows),
        30,
        calendar=[entry, endpoint],
        stocks=stocks,
        benchmark={entry.isoformat(): 100.0, endpoint.isoformat(): 100.0},
        evaluation_as_of=endpoint,
    )
    assert result.metrics["ic"] == pytest.approx(1.0)


def test_counterexample_overflowed_return_and_nav_are_controlled_gaps() -> None:
    entry = date(2026, 1, 1)
    endpoint = date(2026, 1, 31)
    row = QualifiedScore("600001", entry, 1.0, 0)
    result = evaluate_section(
        ScoreSection(entry, (row,)),
        30,
        calendar=[entry, endpoint],
        stocks={"600001": {entry.isoformat(): 1e-308, endpoint.isoformat(): 1e308}},
        benchmark={entry.isoformat(): 100.0, endpoint.isoformat(): 101.0},
        evaluation_as_of=endpoint,
    )
    assert result.metrics["q5_return"] is None
    assert result.metrics["q5_mdd"] is None
    assert "stock_return_non_finite:600001" in result.gaps


def test_counterexample_chained_mdd_is_not_minimum_batch_mdd() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05", "2026-02-04"))
    q5_code = FIXED_CODES[4]
    db.execute("UPDATE daily_bars SET close=? WHERE code=? AND trade_date=?", (100.0, q5_code, "2026-01-05"))
    db.execute("UPDATE daily_bars SET close=? WHERE code=? AND trade_date=?", (90.0, q5_code, "2026-02-04"))
    db.execute("UPDATE daily_bars SET close=? WHERE code=? AND trade_date=?", (81.0, q5_code, "2026-03-06"))
    summary = _summary(db)
    assert summary["windows"]["30"]["metrics"]["q5_mdd"] == pytest.approx(-19.0)


def test_counterexample_all_tied_requires_daily_paths() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], tied=True)
    db.execute("DELETE FROM daily_bars WHERE code=? AND trade_date=?", (FIXED_CODES[0], "2026-02-04"))
    summary = _summary(db)
    assert summary["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert "equal_daily_path_missing" in summary["gaps"]


def test_counterexample_registered_cohort_survives_unregistered_duplicate() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    db.execute(
        "INSERT INTO predictions VALUES (?, 'A', ?, ?, ?, 0, 'other-cohort', ?, ?, ?, ?)",
        (FIXED_CODES[0], "2026-01-05", 89.0, 99.0, *_snapshot(99)),
    )
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 1
    assert not any("conflicting_prediction" in gap for gap in summary["gaps"])


def test_counterexample_quant_score_and_qualitative_as_of_are_checked() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    db.execute("UPDATE predictions SET quant_score=? WHERE code=?", (123.0, FIXED_CODES[0]))
    summary = _summary(db)
    assert any("snapshot_quant_score_conflict" in gap for gap in summary["gaps"])
    assert summary["windows"]["90"]["selected_sections"] == 0


def test_counterexample_qualitative_snapshot_is_not_revalidated_against_mutable_cache() -> None:
    db = _db()
    db.execute(
        "CREATE TABLE qualitative_scores (code TEXT, moat INTEGER, market_pos INTEGER, sentiment INTEGER, scored_date TEXT)"
    )
    code = FIXED_CODES[0]
    db.execute("INSERT INTO qualitative_scores VALUES (?, 5, 2, 3, '2026-01-01')", (code,))
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    snapshot, *_ = _snapshot(1.0)
    parsed = json.loads(snapshot)
    parsed["qualitative_as_of"] = {"moat": "2026-01-01", "market_pos": "2026-01-01", "sentiment": "2026-01-01"}
    db.execute("UPDATE predictions SET scoring_snapshot_json=? WHERE code=?", (json.dumps(parsed), code))
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 1
    parsed["qualitative_as_of"]["moat"] = "2026-01-02"
    db.execute("UPDATE predictions SET scoring_snapshot_json=? WHERE code=?", (json.dumps(parsed), code))
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 1


def test_counterexample_hybrid_qualitative_vectors_are_valid_when_each_dimension_matches() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    snapshot, _, _, _ = _snapshot(1.0)
    parsed = json.loads(snapshot)
    parsed["scoring_inputs"].update({"moat_fixed": 8, "market_pos_fixed": 1, "sentiment_fixed": 4})
    parsed["result"]["component_scores"].update({"moat_fixed": 8, "market_pos_fixed": 1, "sentiment_fixed": 4})
    parsed["result"]["total_score"] = 4.0
    parsed["qualitative_as_of"] = {"moat": "2026-01-01", "market_pos": None, "sentiment": None}
    db.execute(
        "UPDATE predictions SET total_score=4, scoring_snapshot_json=?, qualitative_snapshot_json=?, "
        "qualitative_sources_json=?, qualitative_mode=? WHERE code=?",
        (
            json.dumps(parsed),
            json.dumps({"moat": 8, "market_pos": 1, "sentiment": 4}),
            json.dumps({"moat": "v2", "market_pos": "v1", "sentiment": "v1"}),
            "hybrid_v2",
            FIXED_CODES[0],
        ),
    )
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 1


def test_counterexample_malformed_source_and_numeric_inputs_are_controlled_gaps() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    snapshot, _, _, _ = _snapshot(1.0)
    parsed = json.loads(snapshot)
    parsed["scoring_inputs"]["roe_3y_avg"] = {"bad": "number"}
    db.execute("UPDATE predictions SET scoring_snapshot_json=? WHERE code=?", (json.dumps(parsed), FIXED_CODES[0]))
    db.execute(
        "UPDATE predictions SET qualitative_sources_json=? WHERE code=?",
        (json.dumps({"moat": [], "market_pos": "v1", "sentiment": "v1"}), FIXED_CODES[1]),
    )
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0
    assert any("scoring_input_invalid:roe_3y_avg" in gap for gap in summary["gaps"])
    assert any("qualitative_source_invalid" in gap for gap in summary["gaps"])


def test_counterexample_huge_numeric_snapshot_is_controlled_gap() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    snapshot, _, _, _ = _snapshot(1.0)
    parsed = json.loads(snapshot)
    parsed["scoring_inputs"]["roe_3y_avg"] = 10**4000
    db.execute("UPDATE predictions SET scoring_snapshot_json=? WHERE code=?", (json.dumps(parsed), FIXED_CODES[0]))
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0
    assert any("scoring_input_invalid:roe_3y_avg" in gap for gap in summary["gaps"])


def test_counterexample_manifest_types_and_duplicate_json_fail_closed(tmp_path) -> None:
    bad = _manifest()
    bad["schema_version"] = 1.0
    with pytest.raises(ManifestError):
        load_experiment_manifest(bad)
    bad = _manifest()
    bad["registration_status"] = []
    with pytest.raises(ManifestError):
        load_experiment_manifest(bad)
    path = tmp_path / "duplicate.json"
    raw = json.dumps(_manifest())[:-1] + ',"experiment_id":"duplicate"}'
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ManifestError, match="duplicate"):
        load_experiment_manifest(path)


def test_counterexample_as_of_requires_timezone_and_converts_to_shanghai() -> None:
    with pytest.raises(ValueError):
        _parse_as_of("2026-01-01T00:00:00")
    parsed = _parse_as_of("2025-12-31T16:30:00+00:00")
    assert parsed is not None
    assert parsed.isoformat() == "2026-01-01"


def test_default_manifest_and_calendar_are_project_relative_and_verified_across_cwd(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    summary = build_accuracy_summary(_db(), evaluation_as_of="2026-09-20")
    manifest = load_experiment_manifest(EXPERIMENT_MANIFEST_PATH)
    assert Path(EXPERIMENT_MANIFEST_PATH).is_file()
    assert paths.trading_calendar_path().is_file()
    assert manifest.registration_status == "verified"
    assert manifest.scoring_hashes == ("d312c8995522b563",)
    assert manifest.effective_from == date(2026, 9, 18)
    assert manifest.effective_to == date(2026, 11, 17)
    assert summary["protocol_status"] == "S2_EVALUATION"
    assert all(window["expected"] == 35 for window in summary["windows"].values())


def test_closed_cohort_keeps_later_evaluation_as_of_for_mature_outcomes() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    manifest = _manifest(FIXED_CODES[:5])
    manifest["effective_to"] = "2026-01-05"
    summary = _summary(db, manifest)
    assert summary["as_of"] == "2026-05-05"
    assert summary["windows"]["90"]["mature_sections"] == 1
    assert summary["windows"]["90"]["metrics"]["q5_return"] is not None


def test_counterexample_duplicate_and_infinite_prices_are_controlled_gaps() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    db.execute("INSERT INTO index_prices VALUES ('H00300', ?, ?)", ("2026-01-05", float("inf")))
    db.execute(
        "INSERT INTO daily_bars VALUES (?, ?, ?, 'tushare.pro_bar.qfq', 'qfq')", (FIXED_CODES[0], "2026-01-05", 101.0)
    )
    summary = _summary(db)
    assert summary["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert "benchmark_price_duplicate:2026-01-05" in summary["price_diagnostics"]
    assert any("stock_price_duplicate" in gap for gap in summary["price_diagnostics"])


def test_counterexample_calendar_alignment_is_backward_for_weekend_entry_and_endpoint() -> None:
    entry = date(2026, 1, 2)
    score_date = date(2026, 1, 3)
    endpoint = date(2026, 1, 30)
    rows = tuple(QualifiedScore(f"60000{i}", score_date, float(i), 0) for i in range(1, 6))
    stocks = {row.code: {entry.isoformat(): 100.0, endpoint.isoformat(): 101.0} for row in rows}
    result = evaluate_section(
        ScoreSection(score_date, rows),
        30,
        calendar=[entry, endpoint],
        stocks=stocks,
        benchmark={entry.isoformat(): 100.0, endpoint.isoformat(): 101.0},
        evaluation_as_of=date(2026, 2, 2),
    )
    assert result.entry_date == entry
    assert result.endpoint == endpoint
    assert "score_date_not_in_calendar" not in result.gaps
    assert result.metrics["q5_return"] is not None


def test_counterexample_result_components_are_required_and_match_frozen_qualitative_vector() -> None:
    for mutation, expected_gap in (
        ("missing", "snapshot_component_scores_missing"),
        ("malformed", "snapshot_component_scores_invalid"),
        ("mismatch", "qualitative_component_score_conflict"),
    ):
        db = _db()
        _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
        snapshot, *_ = _snapshot(1.0)
        parsed = json.loads(snapshot)
        if mutation == "missing":
            del parsed["result"]["component_scores"]
        elif mutation == "malformed":
            parsed["result"]["component_scores"]["moat_fixed"] = {"bad": "number"}
        else:
            parsed["result"]["component_scores"]["moat_fixed"] = 6
        db.execute(
            "UPDATE predictions SET scoring_snapshot_json=? WHERE code=?",
            (json.dumps(parsed), FIXED_CODES[0]),
        )
        summary = _summary(db)
        assert summary["windows"]["90"]["selected_sections"] == 0
        assert any(expected_gap in gap for gap in summary["gaps"])


@pytest.mark.parametrize("mode", ["quant", "total"])
def test_writer_valid_component_rounding_is_accepted(mode: str) -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    snapshot, qualitative, sources, qualitative_mode, result = _writer_rounding_snapshot(mode)
    db.execute(
        """UPDATE predictions
           SET quant_score=?, total_score=?, scoring_snapshot_json=?, qualitative_snapshot_json=?,
               qualitative_sources_json=?, qualitative_mode=?
           WHERE code=? AND score_date='2026-01-05'""",
        (
            result["quant_score"],
            result["total_score"],
            snapshot,
            qualitative,
            sources,
            qualitative_mode,
            FIXED_CODES[0],
        ),
    )

    summary = _summary(db)

    assert summary["windows"]["90"]["qualified_snapshots"] == 5
    assert not any("snapshot_quant_components_conflict" in gap for gap in summary["gaps"])
    assert not any("snapshot_total_components_conflict" in gap for gap in summary["gaps"])


@pytest.mark.parametrize(
    ("mode", "mutate", "expected_gap"),
    [
        (
            "quant",
            lambda snapshot, _result: snapshot["result"]["component_scores"].__setitem__("roe_3y_avg", 0.03),
            "snapshot_quant_components_conflict",
        ),
        (
            "total",
            lambda snapshot, result: (
                snapshot["result"].__setitem__("total_score", 10.06),
                result.__setitem__("total_score", 10.06),
            ),
            "snapshot_total_components_conflict",
        ),
    ],
)
def test_component_rounding_interval_rejects_out_of_range_tampering(mode, mutate, expected_gap: str) -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    snapshot_json, qualitative, sources, qualitative_mode, result = _writer_rounding_snapshot(mode)
    snapshot = json.loads(snapshot_json)
    mutate(snapshot, result)
    db.execute(
        """UPDATE predictions
           SET quant_score=?, total_score=?, scoring_snapshot_json=?, qualitative_snapshot_json=?,
               qualitative_sources_json=?, qualitative_mode=?
           WHERE code=? AND score_date='2026-01-05'""",
        (
            result["quant_score"],
            result["total_score"],
            json.dumps(snapshot, separators=(",", ":")),
            qualitative,
            sources,
            qualitative_mode,
            FIXED_CODES[0],
        ),
    )

    summary = _summary(db)

    assert summary["windows"]["90"]["qualified_snapshots"] == 4
    assert any(expected_gap in gap for gap in summary["gaps"])


def test_counterexample_public_report_controls_invalid_unicode_and_huge_manifest_json(tmp_path) -> None:
    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b"{\xff")
    report = build_accuracy_report(_db(), manifest=invalid_utf8)
    assert "INSUFFICIENT_EVIDENCE" in report
    assert "UnicodeDecodeError" not in report

    huge_integer = tmp_path / "huge-integer.json"
    huge_integer.write_text('{"schema_version":' + "9" * 5000 + "}", encoding="utf-8")
    report = build_accuracy_report(_db(), manifest=huge_integer)
    assert "INSUFFICIENT_EVIDENCE" in report
    assert "NaN" not in report and "Infinity" not in report


def test_explicit_pending_report_exposes_known_denominator_and_unknown_counts() -> None:
    db = _db()
    pending = _manifest(status="pending")
    summary = build_accuracy_summary(db, manifest=pending)
    report = build_accuracy_report(db, manifest=pending)
    for window in summary["windows"].values():
        assert window["expected"] == 35
        assert window["actual_unique_predictions"] is None
        assert window["qualified_snapshots"] is None
        assert window["outcome_computable"] is None
    assert "评分时资格：expected=35；实际唯一预测=N/A；有效快照=N/A" in report


def test_counterexample_unrelated_invalid_price_is_diagnostic_not_window_blocker() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    db.execute(
        "INSERT INTO daily_bars VALUES (?, ?, ?, 'tushare.pro_bar.qfq', 'qfq')",
        ("999999", "2026-02-20", "bad"),
    )
    summary = _summary(db)
    window = summary["windows"]["90"]
    assert window["selected_sections"] == 1
    assert window["metrics"]["q5_return"] is not None
    assert any("stock_price_invalid:999999:2026-02-20" == item for item in window["price_diagnostics"])
    assert not any("999999" in gap for gap in window["gaps"])


def test_counterexample_relevant_mid_path_corruption_keeps_endpoint_diagnostic_but_blocks_chain() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5])
    mid = "2026-02-20"
    for index, code in enumerate(FIXED_CODES[:5]):
        db.execute(
            "INSERT INTO daily_bars VALUES (?, ?, ?, 'tushare.pro_bar.qfq', 'qfq')",
            (code, mid, 120.0 + index),
        )
    db.execute("INSERT INTO index_prices VALUES ('H00300', ?, ?)", (mid, 110.0))
    db.execute(
        "INSERT INTO daily_bars VALUES (?, ?, ?, 'tushare.pro_bar.qfq', 'qfq')",
        (FIXED_CODES[4], mid, "bad"),
    )
    calendar = CalendarEvidence.from_dates(
        [*CALENDAR_DATES, mid],
        covered_from="2026-01-01",
        covered_to="2026-05-05",
        as_of="2026-05-05",
        source="synthetic-calendar-with-midpoint",
    )
    summary = build_accuracy_summary(
        db, manifest=_manifest(FIXED_CODES[:5]), evaluation_as_of="2026-05-05", calendar=calendar
    )
    window = summary["windows"]["30"]
    assert window["metrics"]["q5_return"] is None
    assert window["endpoint_diagnostics"][1]["metrics"]["q5_return"] is not None
    assert "q5_path_incomplete" in window["gaps"]


@pytest.mark.parametrize("tied", [False, True])
def test_young_batches_do_not_block_or_change_mature_fixed_results(tied: bool) -> None:
    before_db, after_db = _db(), _db()
    _insert_scores(before_db, FIXED_CODES[:5], dates=tuple(CALENDAR_DATES[:-1]), tied=tied)
    _insert_scores(after_db, FIXED_CODES[:5], dates=tuple(CALENDAR_DATES), tied=tied)
    before, after = _summary(before_db), _summary(after_db)
    assert after["evidence_status"] == "READY_FOR_DIRECTION_REVIEW"
    assert after["verdict"] == ("MANUAL_REVIEW_REQUIRED" if tied else None)
    for window, mature_count in [("30", 4), ("60", 2), ("90", 1)]:
        current = after["windows"][window]
        assert current["metrics"] == before["windows"][window]["metrics"]
        assert current["selected_sections"] == mature_count + 1
        assert current["mature_sections"] == mature_count
        assert current["immature_sections"] == 1
        assert current["endpoint_diagnostics"][-1]["maturity"] == "immature"
    assert after["windows"]["30"]["selected_dates"] == CALENDAR_DATES


def test_mature_bad_batch_cannot_be_skipped_when_young_batches_exist() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=tuple(CALENDAR_DATES))
    db.execute("DELETE FROM daily_bars WHERE code=? AND trade_date='2026-02-04'", (FIXED_CODES[4],))
    summary = _summary(db)
    window = summary["windows"]["30"]
    assert window["selected_dates"] == CALENDAR_DATES
    assert window["mature_sections"] == 4
    assert window["metrics"]["q5_return"] is None
    assert window["readiness_status"] == "INSUFFICIENT_EVIDENCE"
    assert summary["evidence_status"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("field", ["dates", "covered_from", "covered_to", "as_of"])
def test_calendar_factory_rejects_datetime_with_controlled_value_error(field: str) -> None:
    values: dict[str, Any] = dict(
        dates=[date(2026, 1, 5)],
        covered_from=date(2026, 1, 1),
        covered_to=date(2026, 5, 5),
        as_of=date(2026, 5, 5),
        source="fixture:calendar",
    )
    values[field] = [datetime(2026, 1, 5)] if field == "dates" else datetime(2026, 1, 5)
    with pytest.raises(ValueError, match="invalid calendar"):
        CalendarEvidence.from_dates(**values)


def test_direct_malformed_calendar_object_degrades_report() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5])
    calendar = CalendarEvidence(
        (datetime(2026, 1, 5),), date(2026, 1, 1), date(2026, 5, 5), date(2026, 5, 5), "fixture:bad-calendar"
    )
    summary = build_accuracy_summary(
        db, manifest=_manifest(FIXED_CODES[:5]), evaluation_as_of="2026-05-05", calendar=calendar
    )
    assert summary["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert any(gap.startswith("calendar_invalid:") for gap in summary["gaps"])


def test_unrepresentable_asof_is_a_controlled_report_gap() -> None:
    report = build_accuracy_report(
        _db(), manifest=_manifest(FIXED_CODES[:5]), evaluation_as_of="9999-12-31T23:59:59-12:00"
    )
    assert "evaluation_as_of_invalid" in report
    assert "INSUFFICIENT_EVIDENCE" in report


@pytest.mark.parametrize("tied", [False, True])
def test_non_overlapping_batches_treat_inter_batch_dates_as_cash(tied: bool) -> None:
    db = _db()
    dates = ("2026-01-05", "2026-02-06", "2026-03-10")
    _insert_scores(db, FIXED_CODES[:5], dates=(dates[0],), tied=tied)
    for day in dates[1:]:
        for code_index, code in enumerate(FIXED_CODES[:5]):
            score = 10.0 if tied else float(code_index + 1)
            snapshot, qualitative, sources, mode = _snapshot(score)
            db.execute(
                "INSERT INTO predictions VALUES (?, 'A', ?, ?, ?, 0, 'synthetic-scoring-hash', ?, ?, ?, ?)",
                (code, day, score - 10.0, score, snapshot, qualitative, sources, mode),
            )
    cash_dates = ["2026-02-05", "2026-03-09"]
    extra_dates = ["2026-02-06", "2026-03-10", "2026-04-09"]
    for offset, day in enumerate(extra_dates, start=10):
        db.execute("INSERT INTO index_prices VALUES ('H00300', ?, ?)", (day, 100.0 + offset))
        for code_index, code in enumerate(FIXED_CODES[:5]):
            db.execute(
                "INSERT INTO daily_bars VALUES (?, ?, ?, 'tushare.pro_bar.qfq', 'qfq')",
                (code, day, 120.0 + code_index + offset),
            )
    calendar = CalendarEvidence.from_dates(
        [*CALENDAR_DATES, *cash_dates, *extra_dates],
        covered_from="2026-01-01",
        covered_to="2026-05-05",
        as_of="2026-05-05",
        source="synthetic-discontinuous-calendar",
    )
    summary = build_accuracy_summary(
        db, manifest=_manifest(FIXED_CODES[:5]), evaluation_as_of="2026-05-05", calendar=calendar
    )
    window = summary["windows"]["30"]
    assert window["all_scores_tied"] is tied
    assert (window["metrics"]["q5_return"] is None) is tied
    assert window["metrics"]["equal_return"] is not None
    assert window["metrics"]["benchmark_return"] is not None
    assert not any("nav_path_gap" in gap for gap in window["gaps"])
    assert window["endpoint_diagnostics"][0]["metrics"]["equal_return"] is not None
    assert window["readiness_status"] == "READY_FOR_DIRECTION_REVIEW"


def _replace_first_snapshot(db: sqlite3.Connection, mutate) -> str:
    raw = db.execute(
        "SELECT scoring_snapshot_json FROM predictions WHERE code=? ORDER BY score_date LIMIT 1", (FIXED_CODES[0],)
    ).fetchone()[0]
    value = json.loads(raw)
    mutate(value)
    changed = json.dumps(value, separators=(",", ":"), allow_nan=False)
    db.execute(
        "UPDATE predictions SET scoring_snapshot_json=? WHERE code=? AND score_date='2026-01-05'",
        (changed, FIXED_CODES[0]),
    )
    return changed


@pytest.mark.parametrize("section", ["implementation", "weights", "scoring_inputs", "result"])
def test_empty_required_snapshot_sections_fail_closed(section: str) -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    _replace_first_snapshot(db, lambda value: value.__setitem__(section, {}))
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0
    assert any("scoring_snapshot" in gap for gap in summary["gaps"])


@pytest.mark.parametrize(
    ("container", "field"),
    [
        ("scoring_inputs", "roe_3y_avg"),
        ("component_scores", "roe_3y_avg"),
        ("result", "data_quality"),
        ("result", "missing_fields"),
    ],
)
def test_missing_frozen_score_contract_fields_fail_closed(container: str, field: str) -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))

    def mutate(value: dict[str, Any]) -> None:
        target = value["result"]["component_scores"] if container == "component_scores" else value[container]
        target.pop(field)

    _replace_first_snapshot(db, mutate)
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0
    assert any(FIXED_CODES[0] in gap for gap in summary["gaps"])


def test_v2_qualitative_source_without_source_date_fails_closed() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    db.execute(
        "UPDATE predictions SET qualitative_sources_json=?, qualitative_mode='hybrid_v2' "
        "WHERE code=? AND score_date='2026-01-05'",
        (json.dumps({"moat": "v2", "market_pos": "v1", "sentiment": "v1"}), FIXED_CODES[0]),
    )
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0
    assert any("qualitative_as_of" in gap for gap in summary["gaps"])


def test_duplicate_snapshot_keys_fail_closed_even_when_values_agree() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    raw = db.execute("SELECT scoring_snapshot_json FROM predictions WHERE code=?", (FIXED_CODES[0],)).fetchone()[0]
    duplicate = raw.replace('"total_score":1.0', '"total_score":1.0,"total_score":1.0')
    assert duplicate != raw
    db.execute(
        "UPDATE predictions SET scoring_snapshot_json=? WHERE code=? AND score_date='2026-01-05'",
        (duplicate, FIXED_CODES[0]),
    )
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0
    assert any("duplicate" in gap for gap in summary["gaps"])


def test_boolean_snapshot_number_fails_closed() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    _replace_first_snapshot(db, lambda value: value["result"].__setitem__("total_score", True))
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0


def test_runtime_weights_metadata_qualifies_but_non_finite_numeric_weight_does_not() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    assert _summary(db)["windows"]["90"]["selected_sections"] == 1

    raw = db.execute(
        "SELECT scoring_snapshot_json FROM predictions WHERE code=? AND score_date='2026-01-05'",
        (FIXED_CODES[0],),
    ).fetchone()[0]
    snapshot = json.loads(raw)
    snapshot["weights"]["fundamental"]["roe_3y_avg"]["max_score"] = float("nan")
    db.execute(
        "UPDATE predictions SET scoring_snapshot_json=? WHERE code=? AND score_date='2026-01-05'",
        (json.dumps(snapshot), FIXED_CODES[0]),
    )
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0
    assert any("snapshot_non_finite" in gap for gap in summary["gaps"])


def test_score_comparison_uses_frozen_two_decimal_contract_not_json_lexeme_precision() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    db.execute(
        "UPDATE predictions SET total_score=50.04, quant_score=40.04 WHERE code=? AND score_date='2026-01-05'",
        (FIXED_CODES[0],),
    )
    _replace_first_snapshot(
        db,
        lambda value: (
            value["result"].__setitem__("total_score", 50.0),
            value["result"].__setitem__("quant_score", 40.0),
            value["result"]["component_scores"].__setitem__("roe_3y_avg", 40.0),
        ),
    )
    summary = _summary(db)
    assert summary["windows"]["90"]["selected_sections"] == 0
    assert any("score_conflict" in gap for gap in summary["gaps"])


def test_default_report_loads_local_calendar_from_configured_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "configured-project"
    config_dir = project_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "experiment_manifest.json").write_text(json.dumps(_manifest(FIXED_CODES[:5])), encoding="utf-8")
    (config_dir / "trading_calendar.json").write_text(
        json.dumps(
            {
                "source": "fixture:local-calendar",
                "covered_from": "2026-01-01",
                "covered_to": "2026-05-05",
                "as_of": "2026-05-05",
                "dates": CALENDAR_DATES,
            }
        ),
        encoding="utf-8",
    )
    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    monkeypatch.setattr(paths, "PROJECT_ROOT", project_root)
    monkeypatch.chdir(unrelated)
    db = _db()
    _insert_scores(db, FIXED_CODES[:5])
    summary = build_accuracy_summary(db, evaluation_as_of="2026-05-05")
    assert summary["evidence_status"] == "READY_FOR_DIRECTION_REVIEW"
    assert not any("calendar" in gap for gap in summary["gaps"])


@pytest.mark.parametrize("content", [None, "not-json", "{}"])
def test_default_local_calendar_missing_or_malformed_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | None
) -> None:
    project_root = tmp_path / "configured-project"
    config_dir = project_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "experiment_manifest.json").write_text(json.dumps(_manifest(FIXED_CODES[:5])), encoding="utf-8")
    if content is not None:
        (config_dir / "trading_calendar.json").write_text(content, encoding="utf-8")
    monkeypatch.setattr(paths, "PROJECT_ROOT", project_root)
    db = _db()
    _insert_scores(db, FIXED_CODES[:5])
    summary = build_accuracy_summary(db, evaluation_as_of="2026-05-05")
    assert summary["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert any("calendar" in gap for gap in summary["gaps"])


def _l3_result(latest_close: float | None):
    contract = DataContractState("qfq", "fixture", "shares", False, None, None, None)
    if latest_close is None:
        return compute_l3_v2_candidate(None, contract)
    bars = tuple(
        DailyBar(
            date(2025, 1, 1),
            None,
            None,
            None,
            latest_close if index == 119 else 100.0,
            None,
            "fixture",
            "qfq",
            "shares",
            None,
            None,
        )
        for index in range(120)
    )
    panel = PricePanel("000001", "qfq", bars, "fixture", "shares", date(2025, 1, 1), None, None)
    return compute_l3_v2_candidate(panel, contract)


def test_l3_owner_semantics_survive_aggregation_and_text_rendering() -> None:
    db = _db()
    _insert_scores(db, FIXED_CODES[:5], dates=("2026-01-05",))
    db.execute("UPDATE predictions SET l3_v2_signal=1")
    owner_results = (_l3_result(60.0), _l3_result(100.0), _l3_result(None))
    assert [(item.signal, item.status) for item in owner_results] == [
        (0, "reject"),
        (1, "pass_strong"),
        (None, "unavailable"),
    ]
    for code, result in zip(FIXED_CODES[:3], owner_results, strict=True):
        db.execute(
            "UPDATE predictions SET l3_v2_signal=? WHERE code=? AND score_date='2026-01-05'",
            (result.signal, code),
        )
    summary = _summary(db)
    counts = summary["windows"]["90"]["l3_recorded"]
    assert counts == {"triggered": 1, "normal": 3, "unknown": 1}
    report = build_accuracy_report(
        db, manifest=_manifest(FIXED_CODES[:5]), evaluation_as_of="2026-05-05", calendar=CALENDAR
    )
    assert "正常 n=3；触发 n=1；未知 n=1" in report
