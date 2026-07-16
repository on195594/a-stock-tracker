from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib.l3_v2 import V2_VERSION  # noqa: E402
from lib.l3_v2_pipeline import compute_l3_v2_from_daily_bars  # noqa: E402


def _make_db(bars: list[dict]) -> sqlite3.Connection:
    """Create an in-memory DB with daily_bars rows (adjusted='none')."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE daily_bars (
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
    start = date(2025, 1, 1)
    for i, bar in enumerate(bars):
        trade_date = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO daily_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                bar.get("code", "000001"),
                bar.get("date", trade_date),
                None,
                None,
                None,
                float(bar["close"]),
                bar.get("volume", 100.0),
                "baostock",
                "none",
                "lot",
                "2026-01-01T00:00:00+00:00",
                "ok",
                None,
            ),
        )
    conn.commit()
    return conn


def _bars(n: int, close: float = 100.0) -> list[dict]:
    return [{"close": close} for _ in range(n)]


def _today_str(n_bars: int) -> str:
    """Return a date string that covers all inserted bars."""
    return (date(2025, 1, 1) + timedelta(days=n_bars + 1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Zero bars
# ---------------------------------------------------------------------------


def test_pipeline_unavailable_when_no_bars() -> None:
    conn = _make_db([])
    result = compute_l3_v2_from_daily_bars(conn, "000001", "2026-12-31")
    assert result.signal is None
    assert result.status == "unavailable"
    assert result.reason == "NO_DAILY_BARS"


# ---------------------------------------------------------------------------
# Insufficient bars (0 < n < 120)
# ---------------------------------------------------------------------------


def test_pipeline_unavailable_when_50_bars() -> None:
    db = _make_db(_bars(50))
    result = compute_l3_v2_from_daily_bars(db, "000001", _today_str(50))
    assert result.signal is None
    assert result.status == "unavailable"
    assert result.reason == "INSUFFICIENT_WINDOW"


def test_pipeline_unavailable_when_119_bars() -> None:
    db = _make_db(_bars(119))
    result = compute_l3_v2_from_daily_bars(db, "000001", _today_str(119))
    assert result.signal is None
    assert result.status == "unavailable"
    assert result.reason == "INSUFFICIENT_WINDOW"


# ---------------------------------------------------------------------------
# 120 bars — normal case (daily_bars is unadjusted → always pass_weak)
# ---------------------------------------------------------------------------


def test_pipeline_pass_weak_with_exactly_120_bars() -> None:
    db = _make_db(_bars(120))
    result = compute_l3_v2_from_daily_bars(db, "000001", _today_str(120))
    assert result.signal == 1
    assert result.version == V2_VERSION
    assert result.status == "pass_weak"
    assert result.reason == "QFQ_UNAVAILABLE"


def test_pipeline_pass_weak_close_just_above_freefall_threshold() -> None:
    # close at FREEFALL_THRESHOLD boundary: should NOT be freefall (strict <)
    # All bars = 100.0: MA120 = 100.0, threshold = 65.0
    # Last bar = 65.0: MA120 = (119*100 + 65)/120 ≈ 99.71, threshold ≈ 64.81; 65 > 64.81 → pass
    rows = _bars(119, close=100.0) + [{"close": 65.0}]
    db = _make_db(rows)
    result = compute_l3_v2_from_daily_bars(db, "000001", _today_str(120))
    assert result.signal == 1
    assert result.status == "pass_weak"


# ---------------------------------------------------------------------------
# FREEFALL rejection
# ---------------------------------------------------------------------------


def test_pipeline_rejects_freefall() -> None:
    # close=60 << MA120≈99.67, threshold≈64.78 → FREEFALL
    rows = _bars(119, close=100.0) + [{"close": 60.0}]
    db = _make_db(rows)
    result = compute_l3_v2_from_daily_bars(db, "000001", _today_str(120))
    assert result.signal == 0
    assert result.status == "reject"
    assert result.reason == "FREEFALL"


# ---------------------------------------------------------------------------
# Exception isolation: DB error must not propagate
# ---------------------------------------------------------------------------


def test_pipeline_returns_unavailable_on_db_error() -> None:
    conn = sqlite3.connect(":memory:")
    # daily_bars table does not exist — triggers OperationalError
    result = compute_l3_v2_from_daily_bars(conn, "000001", "2026-01-01")
    assert result.signal is None
    assert result.status == "unavailable"
    assert result.reason == "L3_V2_ERROR"
