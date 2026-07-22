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


def _responses(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "600036.SH",
                "trade_date": trade_date.replace("-", ""),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "vol": 100.0,
            }
            for trade_date, close in zip(dates, closes, strict=True)
        ]
    )


def _seed_shadow(db_path: str, code: str, dates: list[str], closes: list[float]) -> None:
    bars = pd.DataFrame(
        [
            {"date": trade_date, "open": close, "high": close, "low": close, "close": close, "volume": 1.0}
            for trade_date, close in zip(dates, closes, strict=True)
        ]
    )
    with sqlite3.connect(db_path) as conn:
        cache_mod.upsert_daily_bars(
            conn,
            code,
            bars,
            source="seed.shadow",
            adjusted=script.SHADOW_ADJUSTED,
            volume_unit="share",
            fetched_at="2026-01-01T00:00:00",
        )
        conn.commit()


def _history_dates() -> tuple[list[str], list[str]]:
    existing = [stamp.date().isoformat() for stamp in pd.bdate_range(end=date.today() - timedelta(days=3), periods=35)]
    new = [
        stamp.date().isoformat()
        for stamp in pd.bdate_range(start=date.fromisoformat(existing[-1]) + timedelta(days=1), end=date.today())
    ]
    if not new:
        new = [(date.fromisoformat(existing[-1]) + timedelta(days=1)).isoformat()]
    return existing, new


def test_create_api_prefers_environment_token(monkeypatch) -> None:
    api = object()
    pro_api = Mock(return_value=api)
    read_token = Mock(return_value="dotenv-token")
    monkeypatch.setenv("TUSHARE_TOKEN", "environment-token")
    monkeypatch.setattr(script, "read_tushare_token", read_token)
    monkeypatch.setattr(script.ts, "pro_api", pro_api)

    assert script._create_api() is api
    pro_api.assert_called_once_with("environment-token")
    read_token.assert_not_called()


def test_create_api_uses_shared_token_reader_when_environment_token_is_absent(monkeypatch) -> None:
    api = object()
    pro_api = Mock(return_value=api)
    read_token = Mock(return_value="dotenv-token")
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(script, "read_tushare_token", read_token)
    monkeypatch.setattr(script.ts, "pro_api", pro_api)

    assert script._create_api() is api
    read_token.assert_called_once_with()
    pro_api.assert_called_once_with("dotenv-token")


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


def test_incremental_fetch_writes_only_new_dates_without_drift(isolated_db, monkeypatch) -> None:
    existing_dates, new_dates = _history_dates()
    _seed_shadow(isolated_db, "600036", existing_dates, [100.0] * len(existing_dates))
    overlap_dates = existing_dates[-20:]
    fetched_dates = overlap_dates + new_dates
    fetched_closes = [100.01] * len(overlap_dates) + [101.0] * len(new_dates)
    pro_bar = Mock(return_value=_responses(fetched_dates, fetched_closes))
    monkeypatch.setattr(script.ts, "pro_bar", pro_bar)

    with sqlite3.connect(isolated_db) as conn:
        count, latest = script._fetch_one(conn, object(), "600036", "20200101", date.today().strftime("%Y%m%d"))
        rows = conn.execute(
            "SELECT trade_date, close, source, fetched_at FROM daily_bars "
            "WHERE code = ? AND adjusted = ? ORDER BY trade_date",
            ("600036", script.SHADOW_ADJUSTED),
        ).fetchall()

    expected_start = (date.fromisoformat(existing_dates[-1]) - timedelta(days=40)).strftime("%Y%m%d")
    assert pro_bar.call_count == 1
    assert pro_bar.call_args.kwargs["start_date"] == expected_start
    assert count == len(new_dates)
    assert latest == new_dates[-1]
    assert rows[: len(existing_dates)] == [
        (trade_date, 100.0, "seed.shadow", "2026-01-01T00:00:00") for trade_date in existing_dates
    ]
    assert [(row[0], row[1], row[2]) for row in rows[len(existing_dates) :]] == [
        (trade_date, 101.0, script.SOURCE) for trade_date in new_dates
    ]


def test_consistent_overlap_drift_triggers_complete_full_reprocess(isolated_db, monkeypatch, caplog) -> None:
    caplog.set_level("INFO")
    existing_dates, new_dates = _history_dates()
    _seed_shadow(isolated_db, "600036", existing_dates, [120.0] * len(existing_dates))
    overlap_dates = existing_dates[-20:]
    incremental = _responses(overlap_dates + new_dates, [99.96] * len(overlap_dates) + [100.0] * len(new_dates))
    full_dates = existing_dates + new_dates
    full = _responses(full_dates, [99.96] * len(existing_dates) + [100.0] * len(new_dates))
    pro_bar = Mock(side_effect=[incremental, full])
    monkeypatch.setattr(script.ts, "pro_bar", pro_bar)

    with sqlite3.connect(isolated_db) as conn:
        count, latest = script._fetch_one(conn, object(), "600036", "20200101", date.today().strftime("%Y%m%d"))
        rows = conn.execute(
            "SELECT trade_date, close, source FROM daily_bars WHERE code = ? AND adjusted = ? ORDER BY trade_date",
            ("600036", script.SHADOW_ADJUSTED),
        ).fetchall()

    assert pro_bar.call_count == 2
    assert pro_bar.call_args_list[1].kwargs["start_date"] == existing_dates[0].replace("-", "")
    assert count == len(full_dates)
    assert latest == new_dates[-1]
    assert rows == [
        (trade_date, 99.96 if trade_date in existing_dates else 100.0, script.SOURCE) for trade_date in full_dates
    ]
    assert "QFQ_TUSHARE_DRIFT_DETECTED code=600036" in caplog.text
    assert "run_length=20" in caplog.text
    assert "QFQ_TUSHARE_REPROCESS_OK code=600036" in caplog.text


def test_single_day_overlap_noise_does_not_trigger_reprocess(isolated_db, monkeypatch, caplog) -> None:
    existing_dates, new_dates = _history_dates()
    _seed_shadow(isolated_db, "600036", existing_dates, [100.0] * len(existing_dates))
    overlap_dates = existing_dates[-20:]
    overlap_closes = [100.0] * len(overlap_dates)
    overlap_closes[8] = 80.0
    pro_bar = Mock(return_value=_responses(overlap_dates + new_dates, overlap_closes + [101.0] * len(new_dates)))
    monkeypatch.setattr(script.ts, "pro_bar", pro_bar)

    with sqlite3.connect(isolated_db) as conn:
        count, _latest = script._fetch_one(conn, object(), "600036", "20200101", date.today().strftime("%Y%m%d"))
        noisy_close = conn.execute(
            "SELECT close FROM daily_bars WHERE code = ? AND adjusted = ? AND trade_date = ?",
            ("600036", script.SHADOW_ADJUSTED, overlap_dates[8]),
        ).fetchone()[0]

    assert count == len(new_dates)
    assert pro_bar.call_count == 1
    assert noisy_close == 100.0
    assert "QFQ_TUSHARE_DRIFT_DETECTED" not in caplog.text


def test_non_finite_overlap_ratio_is_excluded_from_drift_run(isolated_db, caplog) -> None:
    existing_dates, _new_dates = _history_dates()
    overlap_dates = existing_dates[-20:]
    _seed_shadow(isolated_db, "600036", overlap_dates, [100.0] * len(overlap_dates))
    fresh_closes = [99.0] * 4 + [float("nan")] + [100.0] * 15
    frame = pd.DataFrame({"date": overlap_dates, "close": fresh_closes})

    with sqlite3.connect(isolated_db) as conn:
        drift_run = script._detect_drift(conn, "600036", frame, overlap_dates[-1])

    assert drift_run is None
    assert f"QFQ_TUSHARE_DRIFT_SKIP code=600036 trade_date={overlap_dates[4]} reason=non_finite_ratio" in caplog.text


def test_non_finite_overlap_ratio_breaks_drift_run(isolated_db, caplog) -> None:
    existing_dates, _new_dates = _history_dates()
    overlap_dates = existing_dates[-20:]
    _seed_shadow(isolated_db, "600036", overlap_dates, [100.0] * len(overlap_dates))
    fresh_closes = [99.0] * 4 + [float("nan")] + [99.0] + [100.0] * 14
    frame = pd.DataFrame({"date": overlap_dates, "close": fresh_closes})

    with sqlite3.connect(isolated_db) as conn:
        drift_run = script._detect_drift(conn, "600036", frame, overlap_dates[-1])

    assert drift_run is None
    assert f"QFQ_TUSHARE_DRIFT_SKIP code=600036 trade_date={overlap_dates[4]} reason=non_finite_ratio" in caplog.text


def test_incomplete_full_reprocess_writes_nothing_and_fails_batch(isolated_db, monkeypatch, capsys, caplog) -> None:
    existing_dates, new_dates = _history_dates()
    _seed_shadow(isolated_db, "600036", existing_dates, [120.0] * len(existing_dates))
    with sqlite3.connect(isolated_db) as conn:
        before = conn.execute(
            "SELECT * FROM daily_bars WHERE code = ? AND adjusted = ? ORDER BY trade_date",
            ("600036", script.SHADOW_ADJUSTED),
        ).fetchall()

    overlap_dates = existing_dates[-20:]
    incremental = _responses(overlap_dates + new_dates, [99.96] * len(overlap_dates) + [100.0] * len(new_dates))
    missing_date = existing_dates[0]
    incomplete_dates = existing_dates[1:] + new_dates
    incomplete = _responses(incomplete_dates, [99.96] * (len(existing_dates) - 1) + [100.0] * len(new_dates))
    monkeypatch.setattr(script, "_create_api", lambda: object())
    monkeypatch.setattr(script.ts, "pro_bar", Mock(side_effect=[incremental, incomplete]))

    exit_code = script.run(script.Args(backfill_days=200, code="600036"))

    with sqlite3.connect(isolated_db) as conn:
        after = conn.execute(
            "SELECT * FROM daily_bars WHERE code = ? AND adjusted = ? ORDER BY trade_date",
            ("600036", script.SHADOW_ADJUSTED),
        ).fetchall()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert before == after
    assert "QFQ_TUSHARE_BATCH_FAILED" in captured.err
    assert "600036" in captured.err
    assert "QFQ_TUSHARE_REPROCESS_INCOMPLETE code=600036" in caplog.text
    assert missing_date in caplog.text


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
