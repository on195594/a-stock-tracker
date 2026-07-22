from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import reconcile_qfq_shadow as reconcile


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "tracker.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE daily_bars (
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL NOT NULL,
                volume REAL,
                adjusted TEXT NOT NULL,
                PRIMARY KEY (code, trade_date, adjusted)
            )"""
        )
    return path


def _insert(
    db_path: Path,
    trade_date: str,
    adjusted: str,
    *,
    code: str = "600036",
    open_: float = 10.0,
    high: float = 11.0,
    low: float = 9.0,
    close: float = 10.5,
    volume: float = 1000,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (code, trade_date, open_, high, low, close, volume, adjusted),
        )


def _run(db_path: Path, tmp_path: Path, code: str = "600036") -> tuple[int, dict]:
    report_path = tmp_path / "report.json"
    exit_code = reconcile.run_reconciliation(db_path, [code], report_path, report_date="2026-07-22")
    return exit_code, json.loads(report_path.read_text(encoding="utf-8"))


def test_row_within_price_and_volume_tolerances_passes(db_path: Path, tmp_path: Path) -> None:
    _insert(db_path, "2026-07-01", "qfq")
    _insert(
        db_path,
        "2026-07-01",
        "qfq_tushare_shadow",
        open_=10.01,
        high=11.011,
        low=8.991,
        close=10.51,
        volume=1001,
    )

    exit_code, report = _run(db_path, tmp_path)

    assert exit_code == 0
    assert report["per_code"]["600036"]["out_of_tolerance_rows"] == []


def test_price_above_tolerance_is_flagged_with_field(db_path: Path, tmp_path: Path) -> None:
    _insert(db_path, "2026-07-01", "qfq")
    _insert(db_path, "2026-07-01", "qfq_tushare_shadow", close=10.52)

    exit_code, report = _run(db_path, tmp_path)
    failure = report["per_code"]["600036"]["out_of_tolerance_rows"][0]["fields"][0]

    assert exit_code == 1
    assert failure == {
        "field": "close",
        "qfq_value": 10.5,
        "shadow_value": 10.52,
        "diff": pytest.approx(0.02),
    }


def test_volume_difference_above_one_is_flagged(db_path: Path, tmp_path: Path) -> None:
    _insert(db_path, "2026-07-01", "qfq")
    _insert(db_path, "2026-07-01", "qfq_tushare_shadow", volume=1002)

    exit_code, report = _run(db_path, tmp_path)
    failure = report["per_code"]["600036"]["out_of_tolerance_rows"][0]["fields"][0]

    assert exit_code == 1
    assert failure["field"] == "volume"
    assert failure["diff"] == 2


def test_baseline_only_date_is_listed(db_path: Path, tmp_path: Path) -> None:
    _insert(db_path, "2026-07-01", "qfq")
    _insert(db_path, "2026-07-02", "qfq", close=11)
    _insert(db_path, "2026-07-01", "qfq_tushare_shadow")

    exit_code, report = _run(db_path, tmp_path)

    assert exit_code == 0
    assert report["per_code"]["600036"]["only_qfq"] == ["2026-07-02"]


def test_shadow_only_date_is_listed(db_path: Path, tmp_path: Path) -> None:
    _insert(db_path, "2026-07-01", "qfq")
    _insert(db_path, "2026-07-01", "qfq_tushare_shadow")
    _insert(db_path, "2026-07-02", "qfq_tushare_shadow", close=11)

    exit_code, report = _run(db_path, tmp_path)

    assert exit_code == 0
    assert report["per_code"]["600036"]["only_shadow"] == ["2026-07-02"]


def test_database_is_opened_with_read_only_uri(db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _insert(db_path, "2026-07-01", "qfq")
    _insert(db_path, "2026-07-01", "qfq_tushare_shadow")
    real_connect = sqlite3.connect
    calls: list[tuple[object, object]] = []

    def recording_connect(database: str, *, uri: bool = False) -> sqlite3.Connection:
        calls.append((database, uri))
        return real_connect(database, uri=uri)

    monkeypatch.setattr(reconcile.sqlite3, "connect", recording_connect)
    _run(db_path, tmp_path)

    assert calls == [(f"file:{db_path}?mode=ro", True)]


def test_exit_code_is_one_when_any_checked_code_has_zero_common_dates(db_path: Path, tmp_path: Path) -> None:
    _insert(db_path, "2026-07-01", "qfq")

    exit_code, report = _run(db_path, tmp_path)

    assert exit_code == 1
    assert report["overall"]["zero_common_codes"] == 1
