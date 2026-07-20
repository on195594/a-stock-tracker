"""Framework B frozen-cohort and legacy-report tests.

All database state is isolated in memory; no production database or network access.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from a_stock_tracker.data.cache import ensure_framework_b_cohort_schema
from a_stock_tracker.reporting import framework_b_cohort
from a_stock_tracker.reporting.framework_b_cohort import FreezePayload


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            framework TEXT NOT NULL,
            score_date TEXT NOT NULL,
            total_score REAL,
            weights_hash TEXT,
            outcome_30d REAL,
            benchmark_30d REAL,
            alpha_30d REAL
        )"""
    )
    return conn


def _payload(source_id: int = 1, code: str = "000858") -> FreezePayload:
    return FreezePayload(
        code=code,
        name="测试股票",
        industry="制造",
        source_a_prediction_id=source_id,
        source_a_score_date="2026-07-17",
        source_a_total_score=58.0,
        score_a=59.0,
        score_b=63.0,
        delta=4.0,
        label="strong",
        rule_name="loose",
        rule_snapshot={"roe_3y_avg_min": 10.0},
        threshold_snapshot={"strong": 63.0, "moderate": 58.0, "light": 50.0},
        candidate_input_snapshot={"roe_3y_avg": 16.0, "gross_margin": 30.0},
        score_snapshot={"score_a": {"total_score": 59.0}, "score_b": {"total_score": 63.0}},
    )


def test_ensure_cohort_schema_is_additive() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO predictions(code,name,framework,score_date,total_score) VALUES('000858','五粮液','A','2026-07-17',59)"
    )

    ensure_framework_b_cohort_schema(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(framework_b_label_cohorts)")}
    assert {
        "cohort_week",
        "label_date",
        "source_a_prediction_id",
        "rule_snapshot_json",
        "threshold_snapshot_json",
        "candidate_input_json",
        "score_snapshot_json",
        "status",
    } <= columns
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 1


def test_freeze_dry_run_does_not_create_table(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _db()
    monkeypatch.setattr(framework_b_cohort, "_build_freeze_payloads", lambda *args, **kwargs: [_payload()])

    result = framework_b_cohort.freeze_weekly_cohort(
        conn,
        {"frameworks": {"A": {}, "B": {}}},
        label_date="2026-07-20",
        dry_run=True,
    )

    assert result.candidates == 1
    assert result.inserted == 0
    assert result.dry_run is True
    assert framework_b_cohort.cohort_table_exists(conn) is False


def test_freeze_is_idempotent_and_preserves_source_prediction(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _db()
    source_id = conn.execute(
        "INSERT INTO predictions(code,name,framework,score_date,total_score) VALUES('000858','五粮液','A','2026-07-17',58)"
    ).lastrowid
    assert source_id is not None
    monkeypatch.setattr(
        framework_b_cohort,
        "_build_freeze_payloads",
        lambda *args, **kwargs: [_payload(source_id=int(source_id))],
    )

    first = framework_b_cohort.freeze_weekly_cohort(conn, {"frameworks": {"A": {}, "B": {}}}, label_date="2026-07-20")
    conn.execute(
        "INSERT INTO predictions(code,name,framework,score_date,total_score) VALUES('000858','五粮液','A','2026-07-21',60)"
    )
    second = framework_b_cohort.freeze_weekly_cohort(conn, {"frameworks": {"A": {}, "B": {}}}, label_date="2026-07-20")

    row = conn.execute(
        """SELECT source_a_prediction_id, source_a_score_date, rule_snapshot_json,
                  threshold_snapshot_json, candidate_input_json
           FROM framework_b_label_cohorts"""
    ).fetchone()
    assert first.inserted == 1
    assert second.inserted == 0
    assert second.skipped_existing == 1
    assert row[0:2] == (source_id, "2026-07-17")
    assert json.loads(row[2]) == {"roe_3y_avg_min": 10.0}
    assert json.loads(row[3])["strong"] == 63.0
    assert json.loads(row[4])["roe_3y_avg"] == 16.0


def test_cohort_gate_counts_distinct_sources_and_requires_three_weeks() -> None:
    conn = _db()
    ensure_framework_b_cohort_schema(conn)
    for index in range(20):
        source_id = conn.execute(
            """INSERT INTO predictions
               (code,name,framework,score_date,total_score,outcome_30d,benchmark_30d)
               VALUES(?,?,?,?,?,?,?)""",
            (f"{index:06d}", "测试", "A", "2026-05-01", 50.0, 3.0, 1.0),
        ).lastrowid
        assert source_id is not None
        payload = _payload(source_id=int(source_id), code=f"{index:06d}")
        framework_b_cohort.insert_freeze_payloads(
            conn,
            [payload],
            cohort_week="2026-W18" if index < 10 else "2026-W19",
            label_date="2026-05-01",
            weights_hash="hash",
        )
    duplicate = _payload(source_id=1, code="999999")
    framework_b_cohort.insert_freeze_payloads(
        conn,
        [duplicate],
        cohort_week="2026-W20",
        label_date="2026-05-15",
        weights_hash="hash",
    )

    summary = framework_b_cohort.summarize_cohort_outcomes(conn, as_of_date="2026-07-20")

    assert summary.closed_count == 20
    assert summary.closed_weeks == 2
    assert summary.ready_for_manual_review is False


def test_cohort_gate_allows_manual_review_only_after_twenty_closed_across_three_weeks() -> None:
    conn = _db()
    ensure_framework_b_cohort_schema(conn)
    weeks = ["2026-W18"] * 7 + ["2026-W19"] * 7 + ["2026-W20"] * 6
    for index, week in enumerate(weeks):
        source_id = conn.execute(
            """INSERT INTO predictions
               (code,name,framework,score_date,total_score,outcome_30d,benchmark_30d)
               VALUES(?,?,?,?,?,?,?)""",
            (f"{index:06d}", "测试", "A", "2026-05-01", 50.0, 3.0, 1.0),
        ).lastrowid
        assert source_id is not None
        framework_b_cohort.insert_freeze_payloads(
            conn,
            [_payload(source_id=int(source_id), code=f"{index:06d}")],
            cohort_week=week,
            label_date="2026-05-01",
            weights_hash="hash",
        )

    summary = framework_b_cohort.summarize_cohort_outcomes(conn, as_of_date="2026-07-20")

    assert summary.closed_count == 20
    assert summary.closed_weeks == 3
    assert summary.overdue_count == 0
    assert summary.ready_for_manual_review is True


def test_legacy_report_uses_all_rows_and_discloses_correlation() -> None:
    conn = _db()
    for index in range(3):
        conn.execute(
            """INSERT INTO predictions
               (code,name,framework,score_date,total_score,weights_hash,outcome_30d,benchmark_30d,alpha_30d)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (f"{index:06d}", "测试", "B", "2026-05-11", 40 + index, "hash", 2.0, 1.0, 1.0),
        )
    lines: list[str] = []

    summary = framework_b_cohort.append_framework_b_legacy_report(lines, conn, "hash")
    text = "\n".join(lines)

    assert summary["sample_count"] == 3
    assert "legacy-only" in text
    assert "不能视为独立样本" in text
    assert "不计入 prospective 门禁" in text


def test_insert_freeze_payloads_rolls_back_on_mid_batch_failure() -> None:
    conn = _db()
    first_id = conn.execute(
        "INSERT INTO predictions(code,framework,score_date) VALUES('000001','A','2026-07-17')"
    ).lastrowid
    second_id = conn.execute(
        "INSERT INTO predictions(code,framework,score_date) VALUES('000002','A','2026-07-17')"
    ).lastrowid
    assert first_id is not None and second_id is not None
    invalid = replace(
        _payload(source_id=int(second_id), code="000002"),
        score_snapshot={"not_json_serializable": {1, 2}},
    )

    with pytest.raises(TypeError):
        framework_b_cohort.insert_freeze_payloads(
            conn,
            [_payload(source_id=int(first_id), code="000001"), invalid],
            cohort_week="2026-W30",
            label_date="2026-07-20",
            weights_hash="hash",
        )

    assert conn.execute("SELECT COUNT(*) FROM framework_b_label_cohorts").fetchone()[0] == 0


def test_freeze_rejects_dangling_source_prediction_id() -> None:
    conn = _db()

    with pytest.raises(sqlite3.IntegrityError):
        framework_b_cohort.insert_freeze_payloads(
            conn,
            [_payload(source_id=999999)],
            cohort_week="2026-W30",
            label_date="2026-07-20",
            weights_hash="hash",
        )


def test_summarize_cohort_outcomes_marks_missing_source() -> None:
    conn = _db()
    source_id = conn.execute(
        "INSERT INTO predictions(code,framework,score_date) VALUES('000858','A','2026-07-17')"
    ).lastrowid
    assert source_id is not None
    framework_b_cohort.insert_freeze_payloads(
        conn,
        [_payload(source_id=int(source_id))],
        cohort_week="2026-W30",
        label_date="2026-07-20",
        weights_hash="hash",
    )
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DELETE FROM predictions WHERE id=?", (source_id,))
    conn.commit()

    summary = framework_b_cohort.summarize_cohort_outcomes(conn, as_of_date="2026-08-20")

    assert summary.missing_source_count == 1
    assert summary.ready_for_manual_review is False


def test_summarize_cohort_outcomes_marks_overdue() -> None:
    conn = _db()
    source_id = conn.execute(
        "INSERT INTO predictions(code,framework,score_date,outcome_30d) VALUES('000858','A','2026-05-01',NULL)"
    ).lastrowid
    assert source_id is not None
    old_payload = replace(
        _payload(source_id=int(source_id)),
        source_a_score_date="2026-05-01",
    )
    framework_b_cohort.insert_freeze_payloads(
        conn,
        [old_payload],
        cohort_week="2026-W18",
        label_date="2026-05-01",
        weights_hash="hash",
    )

    summary = framework_b_cohort.summarize_cohort_outcomes(conn, as_of_date="2026-07-20")

    assert summary.overdue_count == 1
    assert summary.future_count == 0
    assert summary.ready_for_manual_review is False


def test_cohort_report_handles_missing_table() -> None:
    conn = _db()
    lines: list[str] = []

    result = framework_b_cohort.append_framework_b_cohort_report(lines, conn)

    assert result["b_label_sample_count"] == 0
    assert "尚未创建 cohort 表" in "\n".join(lines)


def test_freeze_weekly_cohort_empty_candidates_returns_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    monkeypatch.setattr(framework_b_cohort, "_build_freeze_payloads", lambda *args, **kwargs: [])

    result = framework_b_cohort.freeze_weekly_cohort(
        conn,
        {"frameworks": {"A": {}, "B": {}}},
        label_date="2026-07-20",
    )

    assert result.candidates == 0
    assert result.inserted == 0
    assert result.skipped_existing == 0
    assert framework_b_cohort.cohort_table_exists(conn) is False


def test_summarize_cohort_outcomes_dedupes_across_weights_hash_change() -> None:
    conn = _db()
    source_id = conn.execute(
        """INSERT INTO predictions(code,framework,score_date,outcome_30d)
           VALUES('000858','A','2026-05-01',3.0)"""
    ).lastrowid
    assert source_id is not None
    payload = _payload(source_id=int(source_id))
    for week, weights_hash in (("2026-W18", "hash-a"), ("2026-W19", "hash-b")):
        framework_b_cohort.insert_freeze_payloads(
            conn,
            [payload],
            cohort_week=week,
            label_date="2026-05-01",
            weights_hash=weights_hash,
        )

    summary = framework_b_cohort.summarize_cohort_outcomes(conn, as_of_date="2026-07-20")

    assert conn.execute("SELECT COUNT(*) FROM framework_b_label_cohorts").fetchone()[0] == 2
    assert summary.sample_count == 1
    assert summary.closed_count == 1
