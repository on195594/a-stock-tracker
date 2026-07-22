from __future__ import annotations

from datetime import date, timedelta
import sqlite3
import time
from unittest.mock import ANY, Mock

import pandas as pd
import pytest

from a_stock_lib.providers.tushare_quotes import to_tushare_stock_code
from a_stock_tracker.data import cache as cache_mod
from scripts import fetch_qfq_daily_bars_tushare as script


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
    conn = cache_mod.get_db()
    conn.close()
    monkeypatch.setattr(script, "DB_PATH", db_path)
    return db_path


def _response(trade_date: str | None = None, volume: float = 123.45) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "600036.SH",
                "trade_date": trade_date or date.today().strftime("%Y%m%d"),
                "open": 10.1,
                "high": 10.8,
                "low": 9.9,
                "close": 10.5,
                "vol": volume,
                "amount": 999.0,
            }
        ]
    )


def test_happy_path_converts_and_writes_only_shadow_partition(isolated_db, monkeypatch) -> None:
    with sqlite3.connect(isolated_db) as conn:
        cache_mod.upsert_daily_bars(
            conn,
            "600036",
            pd.DataFrame([{"date": "2026-07-20", "close": 99.0, "volume": 1.0}]),
            source="baostock.qfq",
            adjusted="qfq",
            volume_unit="share",
        )
        conn.commit()

    pro_bar = Mock(return_value=_response("20260720", volume=1_433_610.15))
    monkeypatch.setattr(script.ts, "pro_bar", pro_bar)
    with sqlite3.connect(isolated_db) as conn:
        count, latest = script._fetch_one(conn, object(), "600036", "20260101", "20260722")
        rows = conn.execute(
            "SELECT trade_date, close, volume, source, adjusted, volume_unit "
            "FROM daily_bars WHERE code = ? ORDER BY adjusted",
            ("600036",),
        ).fetchall()

    assert count == 1
    assert latest == "2026-07-20"
    assert rows == [
        ("2026-07-20", 99.0, 1.0, "baostock.qfq", "qfq", "share"),
        ("2026-07-20", 10.5, 143_361_015.0, script.SOURCE, script.SHADOW_ADJUSTED, "share"),
    ]
    assert pro_bar.call_args.kwargs == {
        "ts_code": "600036.SH",
        "start_date": "20260101",
        "end_date": "20260722",
        "adj": "qfq",
        "api": ANY,
    }


def test_permission_error_fails_fast_without_retry(monkeypatch) -> None:
    pro_bar = Mock(side_effect=RuntimeError("40203: 您没有访问该接口的权限"))
    monkeypatch.setattr(script.ts, "pro_bar", pro_bar)

    with pytest.raises(RuntimeError, match="40203"):
        script._query_qfq(object(), "600036", "20260101", "20260722")

    assert pro_bar.call_count == 1


def test_permission_error_aborts_remaining_batch_codes(isolated_db, monkeypatch) -> None:
    pro_bar = Mock(side_effect=RuntimeError("40203: permission denied"))
    monkeypatch.setattr(script.ts, "pro_bar", pro_bar)

    with sqlite3.connect(isolated_db) as conn:
        failures, latest_dates = script._collect_codes(
            conn,
            object(),
            ["600036", "000001"],
            "20260101",
            "20260722",
        )

    assert failures == {"600036": "RuntimeError: 40203: permission denied"}
    assert latest_dates == []
    assert pro_bar.call_count == 1


def test_transient_error_retries_to_cap_then_gives_up(monkeypatch) -> None:
    pro_bar = Mock(side_effect=ConnectionError("connection timeout"))
    monkeypatch.setattr(script.ts, "pro_bar", pro_bar)
    monkeypatch.setattr(script.time, "sleep", Mock())
    monkeypatch.setattr(script.random, "uniform", lambda _start, _end: 0.0)

    with pytest.raises(ConnectionError, match="connection timeout"):
        script._query_qfq(object(), "600036", "20260101", "20260722")

    assert pro_bar.call_count == script.MAX_ATTEMPTS
    assert script.time.sleep.call_count == script.MAX_ATTEMPTS - 1


def test_hyphenated_rate_limit_error_is_transient() -> None:
    assert script._is_transient_error(RuntimeError("API rate-limit exceeded"))


def test_stuck_call_is_bounded_by_per_code_budget(monkeypatch) -> None:
    def stuck_pro_bar(**_kwargs):
        time.sleep(0.1)
        return _response()

    monkeypatch.setattr(script.ts, "pro_bar", stuck_pro_bar)
    monkeypatch.setattr(script, "RETRY_BUDGET_SECONDS", 0.01)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="budget exhausted"):
        script._query_qfq(object(), "600036", "20260101", "20260722")

    assert time.monotonic() - started < 0.08


def test_batch_failure_returns_nonzero_and_names_failed_code(isolated_db, monkeypatch, capsys) -> None:
    monkeypatch.setattr(script, "WATCHLIST", [{"code": "600036"}, {"code": "000001"}])
    monkeypatch.setattr(script, "_create_api", lambda: object())

    def fake_pro_bar(*, ts_code, **_kwargs):
        if ts_code == "000001.SZ":
            raise RuntimeError("40203 permission denied")
        return _response()

    monkeypatch.setattr(script.ts, "pro_bar", fake_pro_bar)

    exit_code = script.run(script.Args(backfill_days=200, code=None))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "QFQ_TUSHARE_BATCH_FAILED" in captured.err
    assert "000001" in captured.err
    assert "600036" not in captured.err


def test_client_initialization_failure_names_every_requested_code(monkeypatch, capsys) -> None:
    monkeypatch.setattr(script, "WATCHLIST", [{"code": "600036"}, {"code": "000001"}])

    def fail_create_api():
        raise RuntimeError("client unavailable")

    monkeypatch.setattr(script, "_create_api", fail_create_api)

    exit_code = script.run(script.Args(backfill_days=200, code=None))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "QFQ_TUSHARE_BATCH_FAILED: client initialization: client unavailable" in captured.err
    assert "  600036: client initialization failed" in captured.err
    assert "  000001: client initialization failed" in captured.err


def test_bse_style_code_pins_shared_converter_current_behavior() -> None:
    assert to_tushare_stock_code("830001") == "830001.BJ"


def test_full_watchlist_missing_today_emits_freshness_warning(isolated_db, monkeypatch, caplog) -> None:
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    monkeypatch.setattr(script, "WATCHLIST", [{"code": "600036"}])
    monkeypatch.setattr(script, "_create_api", lambda: object())
    monkeypatch.setattr(script.ts, "pro_bar", Mock(return_value=_response(yesterday)))

    exit_code = script.run(script.Args(backfill_days=200, code=None))

    assert exit_code == 0
    assert "QFQ_TUSHARE_FRESHNESS_WARNING" in caplog.text
    assert date.today().isoformat() in caplog.text
