from __future__ import annotations

import sqlite3

from a_stock_tracker.qualitative.production import (
    get_production_qualitative_selection,
    production_mode,
)


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute(
        """CREATE TABLE qualitative_scores_v2 (
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            as_of_date TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            model TEXT NOT NULL,
            overall_status TEXT NOT NULL,
            moat INTEGER,
            market_pos INTEGER,
            sentiment INTEGER,
            context_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            scored_at TEXT NOT NULL
        )"""
    )
    return db


def _legacy(_code: str, _name: str) -> dict[str, int]:
    return {"moat": 5, "market_pos": 2, "sentiment": 3}


def test_mode_defaults_off_and_rejects_retired_canary() -> None:
    assert production_mode({}) == "off"
    try:
        production_mode({"QUALITATIVE_V2_MODE": "canary"})
    except ValueError as exc:
        assert "off or on" in str(exc)
    else:
        raise AssertionError("retired canary mode must be rejected")


def test_existing_partial_v2_is_read_only_and_combined_with_local_v1() -> None:
    db = _db()
    db.execute(
        """INSERT INTO qualitative_scores_v2 VALUES
           ('600036','招商银行','2026-07-19','hash','retired-model',
            'insufficient_data',7,4,NULL,'{}','{}','2026-07-19T00:00:00+08:00')"""
    )

    selection = get_production_qualitative_selection(
        db,
        "600036",
        "招商银行",
        legacy_getter=_legacy,
        mode="on",
    )

    assert selection.scores == {"moat": 7, "market_pos": 4, "sentiment": 3}
    assert selection.sources == {
        "moat": "v2",
        "market_pos": "v2",
        "sentiment": "v1",
    }
    assert selection.mode == "hybrid_v2"


def test_invalid_local_v2_falls_back_without_writing() -> None:
    db = _db()
    db.execute(
        """INSERT INTO qualitative_scores_v2 VALUES
           ('600036','错误名称','2026-07-19','hash','retired-model',
            'insufficient_data',7,4,NULL,'{}','{}','2026-07-19T00:00:00+08:00')"""
    )
    before = db.total_changes

    selection = get_production_qualitative_selection(
        db,
        "600036",
        "招商银行",
        legacy_getter=_legacy,
        mode="on",
    )

    assert selection.scores == _legacy("", "")
    assert selection.mode == "v1"
    assert db.total_changes == before
