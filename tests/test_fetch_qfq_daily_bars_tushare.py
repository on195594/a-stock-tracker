from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from unittest.mock import ANY, Mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from a_stock_lib.providers.tushare_quotes import to_tushare_stock_code
from a_stock_tracker.data import cache as cache_mod
from a_stock_tracker.reporting.evaluation import load_calendar_evidence
from scripts import fetch_qfq_daily_bars_tushare as script


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
    conn = cache_mod.get_db()
    conn.close()
    monkeypatch.setattr(script, "DB_PATH", db_path)
    return db_path


@pytest.fixture(autouse=True)
def stub_total_return_index(monkeypatch):
    original = script._fetch_total_return_index
    monkeypatch.setattr(script, "_fetch_total_return_index", Mock(return_value=(1, date.today().isoformat())))
    return original


@pytest.fixture(autouse=True)
def stub_calendar_refresh(monkeypatch):
    original = script._refresh_trading_calendar
    monkeypatch.setattr(script, "_refresh_trading_calendar", Mock(return_value=Path("calendar.json")))
    return original


def _freeze_shanghai_clock(monkeypatch, utc_now: datetime) -> date:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = utc_now.astimezone(timezone.utc)
            return current.astimezone(tz) if tz is not None else current.replace(tzinfo=None)

    monkeypatch.setattr(script, "datetime", FrozenDateTime)
    return utc_now.astimezone(ZoneInfo("Asia/Shanghai")).date()


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


def _calendar_response(start: date, end: date) -> pd.DataFrame:
    rows = []
    previous_open = None
    current = start
    while current <= end:
        is_open = int(current.weekday() < 5)
        rows.append(
            {
                "exchange": "SSE",
                "cal_date": current.strftime("%Y%m%d"),
                "is_open": is_open,
                "pretrade_date": previous_open,
            }
        )
        if is_open:
            previous_open = current.strftime("%Y%m%d")
        current += timedelta(days=1)
    return pd.DataFrame(reversed(rows))


def _seed_target(db_path: str, code: str, dates: list[str], closes: list[float]) -> None:
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
            source="seed.target",
            adjusted=script.PRODUCTION_ADJUSTED,
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


def test_calendar_refresh_writes_auditable_runtime_evidence(tmp_path, stub_calendar_refresh) -> None:
    tracked = tmp_path / "config" / "trading_calendar.json"
    runtime = tmp_path / "data" / "trading_calendar.json"
    sources = tmp_path / "data" / "trading-calendar-sources"
    tracked.parent.mkdir()
    tracked.write_text(
        json.dumps(
            {
                "dates": ["2026-09-18"],
                "covered_from": "2026-09-18",
                "covered_to": "2026-09-18",
                "as_of": "2026-09-18",
                "source": "seed",
            }
        ),
        encoding="utf-8",
    )
    api = Mock()
    api.trade_cal.return_value = _calendar_response(date(2026, 9, 18), date(2026, 9, 20))

    result = stub_calendar_refresh(
        api,
        date(2026, 9, 20),
        tracked_path=tracked,
        runtime_path=runtime,
        source_dir=sources,
    )

    evidence = load_calendar_evidence(result)
    raw_files = list(sources.glob("*.json"))
    assert evidence.covered_from == date(2026, 9, 18)
    assert evidence.covered_to == date(2026, 9, 20)
    assert evidence.as_of == date(2026, 9, 20)
    assert evidence.dates == (date(2026, 9, 18),)
    assert len(raw_files) == 1
    raw_hash = hashlib.sha256(raw_files[0].read_bytes()).hexdigest()
    raw_payload = json.loads(raw_files[0].read_text(encoding="utf-8"))
    assert raw_files[0].stem == raw_hash
    assert raw_payload["official_cross_check"] == {
        "notice": script.CALENDAR_OFFICIAL_REFERENCE,
        "covered_from": "2026-09-18",
        "covered_to": "2026-09-20",
        "closure_conflicts": [],
    }
    assert f"raw_sha256={raw_hash}" in evidence.source
    assert script.CALENDAR_OFFICIAL_REFERENCE in evidence.source
    assert "official_cross_check_range=2026-09-18..2026-09-20" in evidence.source
    api.trade_cal.assert_called_once_with(
        exchange="SSE",
        start_date="20260918",
        end_date="20260920",
        fields="exchange,cal_date,is_open,pretrade_date",
    )


def test_calendar_refresh_failure_preserves_previous_runtime_file(tmp_path, stub_calendar_refresh) -> None:
    tracked = tmp_path / "tracked.json"
    runtime = tmp_path / "runtime.json"
    tracked.write_text(
        json.dumps(
            {
                "dates": ["2026-09-18"],
                "covered_from": "2026-09-18",
                "covered_to": "2026-09-18",
                "as_of": "2026-09-18",
                "source": "seed",
            }
        ),
        encoding="utf-8",
    )
    runtime.write_bytes(b"previous-calendar\n")
    api = Mock()
    incomplete = _calendar_response(date(2026, 9, 18), date(2026, 9, 20))
    api.trade_cal.return_value = incomplete[incomplete["cal_date"] != "20260919"]

    with pytest.raises(RuntimeError, match="does not cover every natural date"):
        stub_calendar_refresh(
            api,
            date(2026, 9, 20),
            tracked_path=tracked,
            runtime_path=runtime,
            source_dir=tmp_path / "sources",
        )

    assert runtime.read_bytes() == b"previous-calendar\n"


def test_atomic_write_restores_previous_file_after_post_replace_failure(tmp_path, monkeypatch) -> None:
    target = tmp_path / "calendar.json"
    target.write_bytes(b"previous-calendar\n")
    real_fsync = script.os.fsync
    calls = 0

    def fail_first_directory_sync(file_descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory fsync failure")
        real_fsync(file_descriptor)

    monkeypatch.setattr(script.os, "fsync", fail_first_directory_sync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        script._atomic_write_bytes(target, b"new-calendar\n")

    assert target.read_bytes() == b"previous-calendar\n"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.rollback"))


def test_calendar_refresh_rejects_official_closure_marked_open(tmp_path, stub_calendar_refresh) -> None:
    tracked = tmp_path / "tracked.json"
    tracked.write_text(
        json.dumps(
            {
                "dates": [],
                "covered_from": "2026-02-15",
                "covered_to": "2026-02-15",
                "as_of": "2026-02-15",
                "source": "seed",
            }
        ),
        encoding="utf-8",
    )
    api = Mock()
    api.trade_cal.return_value = _calendar_response(date(2026, 2, 15), date(2026, 2, 16))

    with pytest.raises(RuntimeError, match="春节:2026-02-16:marked_open"):
        stub_calendar_refresh(
            api,
            date(2026, 2, 16),
            tracked_path=tracked,
            runtime_path=tmp_path / "runtime.json",
            source_dir=tmp_path / "sources",
        )


@pytest.mark.parametrize(
    "utc_now, expected_today",
    [
        (datetime(2026, 9, 19, 15, 59, 59, tzinfo=timezone.utc), date(2026, 9, 19)),
        (datetime(2026, 9, 19, 16, 0, tzinfo=timezone.utc), date(2026, 9, 20)),
        (datetime(2026, 12, 31, 16, 0, tzinfo=timezone.utc), date(2027, 1, 1)),
    ],
)
def test_calendar_only_refresh_avoids_database_access(monkeypatch, utc_now, expected_today) -> None:
    api = object()
    calendar_refresh = Mock(return_value=Path("calendar.json"))
    monkeypatch.setattr(script, "_create_api", lambda: api)
    monkeypatch.setattr(script, "_refresh_trading_calendar", calendar_refresh)
    monkeypatch.setattr(script.sqlite3, "connect", Mock(side_effect=AssertionError("database must not open")))

    assert _freeze_shanghai_clock(monkeypatch, utc_now) == expected_today
    assert script.run(script.Args(backfill_days=200, code=None, calendar_only=True)) == 0
    calendar_refresh.assert_called_once_with(api, expected_today)


def test_calendar_refresh_failure_blocks_batch_before_database(monkeypatch, capsys) -> None:
    calendar_refresh = Mock(side_effect=RuntimeError("calendar unavailable"))
    monkeypatch.setattr(script, "_create_api", lambda: object())
    monkeypatch.setattr(script, "_refresh_trading_calendar", calendar_refresh)
    monkeypatch.setattr(script.sqlite3, "connect", Mock(side_effect=AssertionError("database must not open")))

    assert script.run(script.Args(backfill_days=200, code=None)) == 1
    assert "TRADING_CALENDAR_REFRESH_FAILED: calendar unavailable" in capsys.readouterr().err


def test_total_return_index_is_upserted_for_automatic_report(isolated_db, stub_total_return_index) -> None:
    api = Mock()
    api.index_daily.return_value = pd.DataFrame(
        [
            {"trade_date": "20260727", "close": 7123.45},
            {"trade_date": "20260724", "close": 7099.00},
        ]
    )

    with sqlite3.connect(isolated_db) as conn:
        count, latest = stub_total_return_index(conn, api, "20260101", "20260728")
        rows = conn.execute(
            "SELECT symbol,date,close FROM index_prices WHERE symbol=? ORDER BY date",
            (script.BENCHMARK_DB_SYMBOL,),
        ).fetchall()

    assert count == 2
    assert latest == "2026-07-27"
    assert rows == [
        ("H00300", "2026-07-24", 7099.0),
        ("H00300", "2026-07-27", 7123.45),
    ]
    api.index_daily.assert_called_once_with(
        ts_code="H00300.CSI",
        start_date="20260101",
        end_date="20260728",
    )


def test_happy_path_converts_and_writes_only_production_partition(isolated_db, monkeypatch) -> None:
    with sqlite3.connect(isolated_db) as conn:
        cache_mod.upsert_daily_bars(
            conn,
            "600036",
            pd.DataFrame([{"date": "2026-07-20", "close": 99.0, "volume": 1.0}]),
            source="seed.shadow",
            adjusted="qfq_tushare_shadow",
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
        ("2026-07-20", 10.5, 143_361_015.0, script.SOURCE, script.PRODUCTION_ADJUSTED, "share"),
        ("2026-07-20", 99.0, 1.0, "seed.shadow", "qfq_tushare_shadow", "share"),
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
    _seed_target(isolated_db, "600036", existing_dates, [100.0] * len(existing_dates))
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
            ("600036", script.PRODUCTION_ADJUSTED),
        ).fetchall()

    expected_start = (date.fromisoformat(existing_dates[-1]) - timedelta(days=40)).strftime("%Y%m%d")
    assert pro_bar.call_count == 1
    assert pro_bar.call_args.kwargs["start_date"] == expected_start
    assert count == len(new_dates)
    assert latest == new_dates[-1]
    assert rows[: len(existing_dates)] == [
        (trade_date, 100.0, "seed.target", "2026-01-01T00:00:00") for trade_date in existing_dates
    ]
    assert [(row[0], row[1], row[2]) for row in rows[len(existing_dates) :]] == [
        (trade_date, 101.0, script.SOURCE) for trade_date in new_dates
    ]


def test_consistent_overlap_drift_triggers_complete_full_reprocess(isolated_db, monkeypatch, caplog) -> None:
    caplog.set_level("INFO")
    existing_dates, new_dates = _history_dates()
    _seed_target(isolated_db, "600036", existing_dates, [120.0] * len(existing_dates))
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
            ("600036", script.PRODUCTION_ADJUSTED),
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
    _seed_target(isolated_db, "600036", existing_dates, [100.0] * len(existing_dates))
    overlap_dates = existing_dates[-20:]
    overlap_closes = [100.0] * len(overlap_dates)
    overlap_closes[8] = 80.0
    pro_bar = Mock(return_value=_responses(overlap_dates + new_dates, overlap_closes + [101.0] * len(new_dates)))
    monkeypatch.setattr(script.ts, "pro_bar", pro_bar)

    with sqlite3.connect(isolated_db) as conn:
        count, _latest = script._fetch_one(conn, object(), "600036", "20200101", date.today().strftime("%Y%m%d"))
        noisy_close = conn.execute(
            "SELECT close FROM daily_bars WHERE code = ? AND adjusted = ? AND trade_date = ?",
            ("600036", script.PRODUCTION_ADJUSTED, overlap_dates[8]),
        ).fetchone()[0]

    assert count == len(new_dates)
    assert pro_bar.call_count == 1
    assert noisy_close == 100.0
    assert "QFQ_TUSHARE_DRIFT_DETECTED" not in caplog.text


def test_non_finite_overlap_ratio_is_excluded_from_drift_run(isolated_db, caplog) -> None:
    existing_dates, _new_dates = _history_dates()
    overlap_dates = existing_dates[-20:]
    _seed_target(isolated_db, "600036", overlap_dates, [100.0] * len(overlap_dates))
    fresh_closes = [99.0] * 4 + [float("nan")] + [100.0] * 15
    frame = pd.DataFrame({"date": overlap_dates, "close": fresh_closes})

    with sqlite3.connect(isolated_db) as conn:
        drift_run = script._detect_drift(conn, "600036", frame, overlap_dates[-1])

    assert drift_run is None
    assert f"QFQ_TUSHARE_DRIFT_SKIP code=600036 trade_date={overlap_dates[4]} reason=non_finite_ratio" in caplog.text


def test_non_finite_overlap_ratio_breaks_drift_run(isolated_db, caplog) -> None:
    existing_dates, _new_dates = _history_dates()
    overlap_dates = existing_dates[-20:]
    _seed_target(isolated_db, "600036", overlap_dates, [100.0] * len(overlap_dates))
    fresh_closes = [99.0] * 4 + [float("nan")] + [99.0] + [100.0] * 14
    frame = pd.DataFrame({"date": overlap_dates, "close": fresh_closes})

    with sqlite3.connect(isolated_db) as conn:
        drift_run = script._detect_drift(conn, "600036", frame, overlap_dates[-1])

    assert drift_run is None
    assert f"QFQ_TUSHARE_DRIFT_SKIP code=600036 trade_date={overlap_dates[4]} reason=non_finite_ratio" in caplog.text


def test_zero_existing_close_breaks_drift_run(isolated_db, caplog) -> None:
    existing_dates, _new_dates = _history_dates()
    overlap_dates = existing_dates[-20:]
    existing_closes = [100.0] * 4 + [0.0] + [100.0] * 15
    _seed_target(isolated_db, "600036", overlap_dates, existing_closes)
    fresh_closes = [99.0] * 6 + [100.0] * 14
    frame = pd.DataFrame({"date": overlap_dates, "close": fresh_closes})

    with sqlite3.connect(isolated_db) as conn:
        drift_run = script._detect_drift(conn, "600036", frame, overlap_dates[-1])

    assert drift_run is None
    assert f"QFQ_TUSHARE_DRIFT_SKIP code=600036 trade_date={overlap_dates[4]} existing_close=0.0" in caplog.text


def test_incomplete_full_reprocess_writes_nothing_and_fails_batch(isolated_db, monkeypatch, capsys, caplog) -> None:
    existing_dates, new_dates = _history_dates()
    _seed_target(isolated_db, "600036", existing_dates, [120.0] * len(existing_dates))
    with sqlite3.connect(isolated_db) as conn:
        before = conn.execute(
            "SELECT * FROM daily_bars WHERE code = ? AND adjusted = ? ORDER BY trade_date",
            ("600036", script.PRODUCTION_ADJUSTED),
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
            ("600036", script.PRODUCTION_ADJUSTED),
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


def test_benchmark_failure_fails_batch_without_blocking_stock_refresh(isolated_db, monkeypatch, capsys) -> None:
    monkeypatch.setattr(script, "WATCHLIST", [{"code": "600036"}])
    monkeypatch.setattr(script, "_create_api", lambda: object())
    monkeypatch.setattr(script, "_fetch_total_return_index", Mock(side_effect=RuntimeError("benchmark unavailable")))
    monkeypatch.setattr(script.ts, "pro_bar", Mock(return_value=_response()))

    exit_code = script.run(script.Args(backfill_days=200, code=None))

    with sqlite3.connect(isolated_db) as conn:
        stock_rows = conn.execute("SELECT COUNT(*) FROM daily_bars WHERE code='600036' AND adjusted='qfq'").fetchone()[
            0
        ]
    captured = capsys.readouterr()
    assert exit_code == 1
    assert stock_rows == 1
    assert "H00300: RuntimeError: benchmark unavailable" in captured.err


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
    expected_today = _freeze_shanghai_clock(monkeypatch, datetime(2026, 9, 19, 16, 0, tzinfo=timezone.utc))
    yesterday = (expected_today - timedelta(days=1)).strftime("%Y%m%d")
    monkeypatch.setattr(script, "WATCHLIST", [{"code": "600036"}])
    monkeypatch.setattr(script, "_create_api", lambda: object())
    monkeypatch.setattr(script.ts, "pro_bar", Mock(return_value=_response(yesterday)))

    exit_code = script.run(script.Args(backfill_days=200, code=None))

    assert exit_code == 0
    assert isinstance(script._fetch_total_return_index, Mock)
    script._fetch_total_return_index.assert_called_once()
    assert "QFQ_TUSHARE_FRESHNESS_WARNING" in caplog.text
    assert expected_today.isoformat() in caplog.text
