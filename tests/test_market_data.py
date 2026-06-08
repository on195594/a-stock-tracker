from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib import cache as cache_mod  # noqa: E402
from lib.market_data import (  # noqa: E402
    EMPTY_RESPONSE,
    REMOTE_DISCONNECTED,
    AkshareMarketDataProvider,
    MISSING_COLUMNS,
    MarketDataCacheService,
    MarketDataResult,
    _normalize_bars_result,
)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tracker.db")
    monkeypatch.setattr(cache_mod, "DB_PATH", db_path)
    conn = cache_mod.get_db()
    yield conn
    conn.close()


def _bars(days: int = 120) -> pd.DataFrame:
    start = date.today() - timedelta(days=days)
    return pd.DataFrame(
        {
            "date": [(start + timedelta(days=i)).isoformat() for i in range(days)],
            "close": [100.0] * days,
            "volume": [1000.0] * days,
        }
    )


def test_normalize_tencent_bars_returns_ok_result() -> None:
    result = _normalize_bars_result(
        pd.DataFrame([{"date": date.today().isoformat(), "close": 10.0, "volume": 100.0}]),
        "akshare.stock_zh_a_hist_tx",
        "l3_bars",
    )

    assert result.status == "ok"
    assert result.value is not None
    assert list(result.value.columns) == ["date", "close", "volume"]


def test_normalize_empty_response_returns_structured_failure() -> None:
    result = _normalize_bars_result(pd.DataFrame(), "source", "l3_bars")

    assert result.status == "failed"
    assert result.error_code == EMPTY_RESPONSE


def test_normalize_missing_volume_for_l3_returns_failure() -> None:
    result = _normalize_bars_result(
        pd.DataFrame([{"date": date.today().isoformat(), "close": 10.0}]),
        "source",
        "l3_bars",
    )

    assert result.status == "failed"
    assert result.error_code == MISSING_COLUMNS


def test_daily_bars_upsert_and_load_are_stable(tmp_db) -> None:
    inserted = cache_mod.upsert_daily_bars(tmp_db, "600036", _bars(2), "source")
    cache_mod.upsert_daily_bars(tmp_db, "600036", _bars(2), "source")
    rows = cache_mod.load_daily_bars(tmp_db, "600036", date.today().isoformat(), 120)

    assert inserted == 2
    assert len(rows) == 2
    assert tmp_db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 2


class _FakeProvider:
    def __init__(self, results):
        self.results = results

    def fetch_l3_bars(self, code: str, end_date: str, window: int):
        return self.results[code]

    def fetch_score_price(self, code: str, score_date: str):
        raise AssertionError("not used")

    def fetch_outcome_price(self, code: str, target_date: str):
        raise AssertionError("not used")


def test_refresh_daily_bars_writes_bars_and_audit(tmp_db) -> None:
    service = MarketDataCacheService(
        tmp_db,
        _FakeProvider({"600036": MarketDataResult(_bars(), "ok", "source", date.today().isoformat())}),
    )

    coverage = service.refresh_daily_bars(["600036"], date.today().isoformat(), 120)

    assert coverage.ok == 1
    assert tmp_db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 120
    assert tmp_db.execute("SELECT status FROM market_data_audit").fetchone()[0] == "ok"


def test_refresh_daily_bars_failed_provider_only_writes_audit(tmp_db) -> None:
    service = MarketDataCacheService(
        tmp_db,
        _FakeProvider({"600036": MarketDataResult(None, "failed", "source", date.today().isoformat(), error_code=EMPTY_RESPONSE)}),
    )

    coverage = service.refresh_daily_bars(["600036"], date.today().isoformat(), 120)

    assert coverage.failed == 1
    assert tmp_db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 0
    row = tmp_db.execute("SELECT status, error_code FROM market_data_audit").fetchone()
    assert row == ("failed", EMPTY_RESPONSE)


def test_akshare_provider_retries_transient_l3_disconnect(monkeypatch) -> None:
    attempts = {"count": 0}

    def flaky_hist(**kw):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("RemoteDisconnected")
        return pd.DataFrame([{"日期": date.today().isoformat(), "收盘": 10.0, "成交量": 100.0}] * 120)

    monkeypatch.setattr("lib.market_data.time.sleep", lambda *_: None)
    monkeypatch.setattr("lib.market_data.ak.stock_zh_a_hist_tx", lambda **kw: pd.DataFrame(columns=["date", "close"]))
    monkeypatch.setattr("lib.market_data.ak.stock_zh_a_hist", flaky_hist)

    result = AkshareMarketDataProvider().fetch_l3_bars("600036", date.today().isoformat(), 120)

    assert attempts["count"] == 3
    assert result.status == "degraded"
    assert result.value is not None
    assert len(result.value) == 120


def test_akshare_provider_returns_structured_failure_after_retries(monkeypatch) -> None:
    monkeypatch.setattr("lib.market_data.time.sleep", lambda *_: None)
    monkeypatch.setattr("lib.market_data.ak.stock_zh_a_hist_tx", lambda **kw: pd.DataFrame(columns=["date", "close"]))
    monkeypatch.setattr("lib.market_data.ak.stock_zh_a_hist", lambda **kw: (_ for _ in ()).throw(ConnectionError("RemoteDisconnected")))

    result = AkshareMarketDataProvider().fetch_l3_bars("600036", date.today().isoformat(), 120)

    assert result.status == "failed"
    assert result.error_code == REMOTE_DISCONNECTED
