from __future__ import annotations

import importlib.util
from pathlib import Path


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
