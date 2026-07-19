from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from a_stock_tracker.signals.l3_v2_pipeline import compute_l3_v2_from_daily_bars


CODE = "000001"
START_DATE = date(2025, 1, 1)
END_DATE = "2026-12-31"


def _make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE daily_bars (
        code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open REAL, high REAL, low REAL,
        close REAL NOT NULL,
        volume REAL,
        source TEXT NOT NULL,
        adjusted TEXT NOT NULL DEFAULT 'none',
        volume_unit TEXT NOT NULL DEFAULT 'lot',
        fetched_at TEXT NOT NULL,
        quality_status TEXT NOT NULL,
        error_code TEXT,
        PRIMARY KEY (code, trade_date, adjusted)
    )""")
    return db


def _insert_bars(
    db: sqlite3.Connection,
    *,
    adjusted: str,
    closes: list[float],
    source: str,
) -> None:
    for offset, close in enumerate(closes):
        trade_date = (START_DATE + timedelta(days=offset)).isoformat()
        db.execute(
            "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                CODE,
                trade_date,
                None,
                None,
                None,
                close,
                100.0,
                source,
                adjusted,
                "share" if adjusted == "qfq" else "lot",
                "2026-01-01T00:00:00+00:00",
                "ok",
                None,
            ),
        )
    db.commit()


def test_pass_strong_when_120_qfq_bars_available() -> None:
    db = _make_db()
    _insert_bars(db, adjusted="qfq", closes=[100.0] * 120, source="baostock.qfq")

    result = compute_l3_v2_from_daily_bars(db, CODE, END_DATE)

    assert result.signal == 1
    assert result.status == "pass_strong"
    assert result.reason == "PASS_STRONG"
    assert result.metrics["close"] == 100.0


def test_fallback_to_pass_weak_when_only_119_qfq_bars() -> None:
    db = _make_db()
    _insert_bars(db, adjusted="qfq", closes=[100.0] * 119, source="baostock.qfq")
    _insert_bars(db, adjusted="none", closes=[90.0] * 120, source="tushare.daily")

    result = compute_l3_v2_from_daily_bars(db, CODE, END_DATE)

    assert result.signal == 1
    assert result.status == "pass_weak"
    assert result.reason == "QFQ_UNAVAILABLE"
    assert result.metrics["close"] == 90.0


def test_fallback_to_pass_weak_when_no_qfq_bars() -> None:
    db = _make_db()
    _insert_bars(db, adjusted="none", closes=[80.0] * 120, source="tushare.daily")

    result = compute_l3_v2_from_daily_bars(db, CODE, END_DATE)

    assert result.signal == 1
    assert result.status == "pass_weak"
    assert result.reason == "QFQ_UNAVAILABLE"
    assert result.metrics["close"] == 80.0


def test_qfq_takes_priority_over_unadjusted() -> None:
    db = _make_db()
    _insert_bars(db, adjusted="qfq", closes=[100.0] * 120, source="baostock.qfq")
    _insert_bars(
        db,
        adjusted="none",
        closes=[100.0] * 119 + [60.0],
        source="tushare.daily",
    )

    result = compute_l3_v2_from_daily_bars(db, CODE, END_DATE)

    assert result.signal == 1
    assert result.status == "pass_strong"
    assert result.reason == "PASS_STRONG"
    assert result.metrics["close"] == 100.0
