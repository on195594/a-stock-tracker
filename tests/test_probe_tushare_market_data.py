from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "probe_tushare_market_data.py"
spec = importlib.util.spec_from_file_location("probe_tushare_market_data", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_close_cross_check_requires_same_reference_trade_date(monkeypatch) -> None:
    monkeypatch.setattr(
        probe,
        "_load_reference_close",
        lambda code, trade_date: probe.ReferenceClose(36.76, "baostock.query_history_k_data_plus", "2026-06-24"),
    )

    status, rows = probe._close_cross_checks(
        [{"code": "600036", "latest_trade_date": "2026-06-25", "latest_close": 36.23}]
    )

    assert status == "MANUAL_REQUIRED"
    assert "DATE_MISMATCH" in rows[0]
    assert "2026-06-24" in rows[0]


def test_close_cross_check_compares_same_day_reference(monkeypatch) -> None:
    monkeypatch.setattr(
        probe,
        "_load_reference_close",
        lambda code, trade_date: probe.ReferenceClose(36.22, "baostock.query_history_k_data_plus", trade_date),
    )

    status, rows = probe._close_cross_checks(
        [{"code": "600036", "latest_trade_date": "2026-06-25", "latest_close": 36.23}]
    )

    assert status == "PASS"
    assert "PASS" in rows[0]


def test_load_reference_close_uses_unadjusted_cache_row(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "market.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE daily_bars (
               code TEXT,
               trade_date TEXT,
               close REAL,
               source TEXT,
               adjusted TEXT,
               fetched_at TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?)",
            ("600036", "2026-06-25", 37.5, "legacy.qfq", "qfq", "2026-06-25T15:01:00"),
        )
        conn.execute(
            "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?)",
            ("600036", "2026-06-25", 36.23, "baostock.query_history_k_data_plus", "none", "2026-06-25T15:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(probe, "DB_PATH", str(db_path))

    reference = probe._load_reference_close("600036", "2026-06-25")

    assert reference == probe.ReferenceClose(36.23, "baostock.query_history_k_data_plus", "2026-06-25")


def test_load_reference_close_falls_back_when_cache_query_fails(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "market.db"
    db_path.write_text("not sqlite", encoding="utf-8")

    provider_module = ModuleType("a_stock_lib.providers.baostock_quotes")
    result = SimpleNamespace(
        status="ok",
        value=pd.DataFrame([{"date": "2026-06-25", "close": 36.23}]),
        source="baostock.query_history_k_data_plus",
    )

    class _Provider:
        def fetch_daily_bars_range(self, code: str, start_date: str, end_date: str):
            assert (code, start_date, end_date) == ("600036", "2026-06-25", "2026-06-25")
            return result

    setattr(provider_module, "IsolatedBaoStockMarketDataProvider", lambda: _Provider())

    monkeypatch.setattr(probe, "DB_PATH", str(db_path))
    monkeypatch.setitem(__import__("sys").modules, "a_stock_lib.providers.baostock_quotes", provider_module)

    reference = probe._load_reference_close("600036", "2026-06-25")

    assert reference == probe.ReferenceClose(36.23, "baostock.query_history_k_data_plus", "2026-06-25")
