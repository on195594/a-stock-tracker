"""Read-only production acceptance and rollback verification tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from a_stock_tracker.data import cache as cache_mod
from a_stock_tracker.qualitative.production_acceptance import (
    BASELINE_SCHEMA_VERSION,
    AcceptanceError,
    ScoreLoader,
    open_readonly_database,
    prediction_snapshot,
    run_acceptance,
    verify_off_rollback,
)

WATCHLIST = [
    {"code": "600036", "name": "招商银行"},
    {"code": "601288", "name": "农业银行"},
]
EXPECTED_SCORES = {"600036": {"moat": 7, "market_pos": 4, "sentiment": None}}
CUTOFF = "2026-07-17"


def _database(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "tracker.db"
    original = cache_mod.DB_PATH
    cache_mod.DB_PATH = str(path)
    try:
        connection = cache_mod.get_db()
        for code, name in (("600036", "招商银行"), ("601288", "农业银行")):
            connection.execute(
                """INSERT INTO predictions
                   (code, name, framework, score_date, price_at_score, quant_score,
                    total_score, weights_hash, report_period, estimate_flag,
                    threshold_adjusted, created_at)
                   VALUES (?, ?, 'A', ?, 40.0, 30.0, 44.0, 'fixture', '2025', 0, 0, ?)""",
                (code, name, CUTOFF, f"{CUTOFF}T16:30:00+08:00"),
            )
        connection.commit()
        connection.close()
    finally:
        cache_mod.DB_PATH = original
    return path


def _baseline(database_path: Path, output: Path) -> None:
    with open_readonly_database(database_path) as connection:
        snapshot = prediction_snapshot(connection, CUTOFF)
    output.write_text(
        json.dumps(
            {
                "schema_version": BASELINE_SCHEMA_VERSION,
                "expected_mode": "on",
                "expected_scores": EXPECTED_SCORES,
                "prediction_snapshot": {
                    "through_date": snapshot.through_date,
                    "row_count": snapshot.row_count,
                    "sha256": snapshot.sha256,
                },
                "watchlist_count": len(WATCHLIST),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _loader(
    connection: sqlite3.Connection,
    code: str,
    name: str,
    *,
    today: date | None = None,
) -> dict[str, int | None] | None:
    del connection, name
    assert today == date(2026, 7, 19)
    score = EXPECTED_SCORES.get(code)
    return None if score is None else dict(score)


def _run(
    tmp_path: Path,
    *,
    environment: dict[str, str] | None = None,
    required_score_date: str | None = None,
    score_loader: ScoreLoader = _loader,
) -> tuple[dict[str, object], Path, Path]:
    database_path = _database(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    _baseline(database_path, baseline_path)
    env_path = tmp_path / ".env"
    env_path.write_text("QUALITATIVE_V2_MODE=on\nGEMINI_API_KEY=must-not-appear\n", encoding="utf-8")
    daily_log = tmp_path / "daily.log"
    report = run_acceptance(
        database_path=database_path,
        env_path=env_path,
        baseline_path=baseline_path,
        daily_log_path=daily_log,
        watchlist=WATCHLIST,
        canary_codes=frozenset({"600036"}),
        environment=environment,
        today=date(2026, 7, 19),
        required_score_date=required_score_date,
        score_loader=score_loader,
    )
    return report, database_path, daily_log


def test_acceptance_passes_exact_hybrid_fallback_and_off_rollback(tmp_path: Path) -> None:
    report, database_path, _daily_log = _run(tmp_path)

    assert report["decision"] == "PASS"
    assert report["eligible_count"] == 2
    assert report["hybrid_codes"] == ["600036"]
    assert report["full_v2_codes"] == []
    assert report["fallback_codes"] == ["601288"]
    assert report["rollback_verified"] is True
    assert "must-not-appear" not in json.dumps(report)
    with open_readonly_database(database_path) as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("UPDATE predictions SET total_score=0")


def test_mode_or_immutable_prediction_drift_returns_rollback(tmp_path: Path) -> None:
    mode_report, _database_path, _daily_log = _run(
        tmp_path / "mode",
        environment={"QUALITATIVE_V2_MODE": "off", "GEMINI_API_KEY": "must-not-appear"},
    )
    assert mode_report["decision"] == "ROLLBACK"
    assert "configured mode is off" in cast(list[str], mode_report["errors"])[0]

    drift_root = tmp_path / "drift"
    drift_root.mkdir()
    database_path = _database(drift_root)
    baseline_path = drift_root / "baseline.json"
    _baseline(database_path, baseline_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE predictions SET total_score=0 WHERE code='600036'")
    env_path = drift_root / ".env"
    env_path.write_text("QUALITATIVE_V2_MODE=on\n", encoding="utf-8")
    report = run_acceptance(
        database_path=database_path,
        env_path=env_path,
        baseline_path=baseline_path,
        daily_log_path=drift_root / "daily.log",
        watchlist=WATCHLIST,
        canary_codes=frozenset({"600036"}),
        environment={},
        today=date(2026, 7, 19),
        score_loader=_loader,
    )
    assert report["decision"] == "ROLLBACK"
    assert "immutable historical predictions drift" in cast(list[str], report["errors"])


def test_loader_failure_is_redacted_and_returns_rollback(tmp_path: Path) -> None:
    def broken_loader(
        connection: sqlite3.Connection,
        code: str,
        name: str,
        *,
        today: date | None = None,
    ) -> dict[str, int | None] | None:
        del connection, code, name, today
        raise RuntimeError("secret-value")

    report, _database_path, _daily_log = _run(tmp_path, score_loader=broken_loader)

    assert report["decision"] == "ROLLBACK"
    assert set(cast(dict[str, str], report["loader_errors"]).values()) == {"RuntimeError"}
    assert "secret-value" not in json.dumps(report)


def test_required_daily_evidence_must_match_rows_and_adoption_log(tmp_path: Path) -> None:
    def daily_loader(
        connection: sqlite3.Connection,
        code: str,
        name: str,
        *,
        today: date | None = None,
    ) -> dict[str, int | None] | None:
        del connection, name
        assert today == date(2026, 7, 20)
        score = EXPECTED_SCORES.get(code)
        return None if score is None else dict(score)

    database_path = _database(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    _baseline(database_path, baseline_path)
    with sqlite3.connect(database_path) as connection:
        for code, name in (("600036", "招商银行"), ("601288", "农业银行")):
            connection.execute(
                """INSERT INTO predictions
                   (code, name, framework, score_date, total_score, weights_hash, created_at)
                   VALUES (?, ?, 'A', '2026-07-20', 44.0, 'fixture', '2026-07-20T16:30:00+08:00')""",
                (code, name),
            )
    env_path = tmp_path / ".env"
    env_path.write_text("QUALITATIVE_V2_MODE=on\n", encoding="utf-8")
    daily_log = tmp_path / "daily.log"
    daily_log.write_text(
        "2026-07-20 16:30:00 INFO 600036 定性评分使用 hybrid_v2，维度来源={}\n",
        encoding="utf-8",
    )
    report = run_acceptance(
        database_path=database_path,
        env_path=env_path,
        baseline_path=baseline_path,
        daily_log_path=daily_log,
        watchlist=WATCHLIST,
        canary_codes=frozenset({"600036"}),
        environment={},
        today=date(2026, 7, 20),
        required_score_date="2026-07-20",
        score_loader=daily_loader,
    )
    assert report["decision"] == "PASS"
    assert report["daily"] == {
        "framework_a_codes": ["600036", "601288"],
        "observed_v2_log_codes": ["600036"],
        "score_date": "2026-07-20",
    }

    daily_log.write_text("2026-07-20 16:30:00 INFO daily 完成\n", encoding="utf-8")
    failed = run_acceptance(
        database_path=database_path,
        env_path=env_path,
        baseline_path=baseline_path,
        daily_log_path=daily_log,
        watchlist=WATCHLIST,
        canary_codes=frozenset({"600036"}),
        environment={},
        today=date(2026, 7, 20),
        required_score_date="2026-07-20",
        score_loader=daily_loader,
    )
    assert failed["decision"] == "ROLLBACK"
    assert "required daily log does not prove every expected v2 adoption" in cast(list[str], failed["errors"])


def test_baseline_rejects_extra_fields_and_off_never_reads_database(tmp_path: Path) -> None:
    database_path = _database(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    _baseline(database_path, baseline_path)
    raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    raw["unexpected"] = True
    baseline_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AcceptanceError, match="schema drift"):
        run_acceptance(
            database_path=database_path,
            env_path=tmp_path / ".env",
            baseline_path=baseline_path,
            daily_log_path=tmp_path / "daily.log",
            watchlist=WATCHLIST,
            canary_codes=frozenset({"600036"}),
            environment={"QUALITATIVE_V2_MODE": "on"},
            today=date(2026, 7, 19),
            score_loader=_loader,
        )
    assert verify_off_rollback(WATCHLIST, frozenset({"600036"}))
