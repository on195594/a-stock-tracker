from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from a_stock_lib import __version__ as A_STOCK_LIB_VERSION  # noqa: E402
from a_stock_tracker.data import cache as cache_mod  # noqa: E402
from a_stock_tracker.data.market_data import (  # noqa: E402
    AUTH_MISSING,
    PERMISSION_DENIED,
    SOURCE_DISABLED,
    get_default_market_data_provider,
)
from a_stock_lib.providers.tushare_quotes import (  # noqa: E402
    INDEX_DAILY_SOURCE,
    TushareMarketDataProvider,
    to_tushare_index_code,
    to_tushare_stock_code,
)

REQUIRES_A_STOCK_LIB_040 = pytest.mark.skipif(
    tuple(int(part) for part in A_STOCK_LIB_VERSION.split(".")[:2]) < (0, 4),
    reason="requires a-stock-lib 0.4.0 retry contract",
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


def test_daily_bars_upsert_and_load_are_stable(tmp_db) -> None:
    inserted = cache_mod.upsert_daily_bars(tmp_db, "600036", _bars(2), "source", volume_unit="share")
    cache_mod.upsert_daily_bars(tmp_db, "600036", _bars(2), "source", volume_unit="share")
    rows = cache_mod.load_daily_bars(tmp_db, "600036", date.today().isoformat(), 120)

    assert inserted == 2
    assert len(rows) == 2
    assert rows[0]["adjusted"] == "none"
    assert rows[0]["volume_unit"] == "share"
    assert tmp_db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 2


def test_default_market_data_provider_is_disabled(monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = get_default_market_data_provider()

    result = provider.fetch_l3_bars("600036", date.today().isoformat(), 120)

    assert result.status == "failed"
    assert result.error_code == SOURCE_DISABLED


def test_tushare_code_conversion() -> None:
    assert to_tushare_stock_code("600036") == "600036.SH"
    assert to_tushare_stock_code("000001") == "000001.SZ"
    assert to_tushare_stock_code("300750") == "300750.SZ"
    assert to_tushare_index_code("000300") == "000300.SH"


def test_tushare_provider_token_missing_returns_auth_missing() -> None:
    provider = TushareMarketDataProvider(token="")

    result = provider.fetch_l3_bars("600036", date.today().isoformat(), 120)

    assert result.status == "failed"
    assert result.error_code == AUTH_MISSING


class _FakeTushareClient:
    def __init__(self) -> None:
        self.daily_calls: list[dict] = []
        self.index_calls: list[dict] = []

    def daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": "600036.SH",
                    "trade_date": "20260608",
                    "open": "35.1",
                    "high": "35.8",
                    "low": "34.9",
                    "close": "35.5",
                    "vol": "123456.0",
                },
                {
                    "ts_code": "600036.SH",
                    "trade_date": "20260609",
                    "open": "35.5",
                    "high": "36.0",
                    "low": "35.2",
                    "close": "35.9",
                    "vol": "223456.0",
                },
            ]
        )

    def index_daily(self, **kwargs):
        self.index_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": "000300.SH",
                    "trade_date": "20260608",
                    "open": "3970.0",
                    "high": "3990.0",
                    "low": "3960.0",
                    "close": "3980.0",
                },
                {
                    "ts_code": "000300.SH",
                    "trade_date": "20260609",
                    "open": "3980.0",
                    "high": "4010.0",
                    "low": "3975.0",
                    "close": "4000.5",
                },
            ]
        )


def test_tushare_provider_normalizes_daily_and_score_price() -> None:
    client = _FakeTushareClient()
    provider = TushareMarketDataProvider(token="token", client=client)

    result = provider.fetch_score_price("600036", "2026-06-09")

    assert result.status == "ok"
    assert result.source == "tushare.daily"
    assert result.value == pytest.approx(35.9)
    assert result.adjusted == "none"
    assert result.volume_unit == "hand"
    assert client.daily_calls[0]["ts_code"] == "600036.SH"
    assert client.daily_calls[0]["fields"] == "ts_code,trade_date,open,high,low,close,vol,amount"


def test_tushare_provider_normalizes_index_daily() -> None:
    client = _FakeTushareClient()
    provider = TushareMarketDataProvider(token="token", client=client)

    result = provider.fetch_index_bars("000300")

    assert result.status == "ok"
    assert result.source == INDEX_DAILY_SOURCE
    assert result.volume_unit == "hand"
    assert result.value is not None
    assert result.value["date"].tolist() == ["2026-06-08", "2026-06-09"]
    assert result.value["close"].tolist() == [3980.0, 4000.5]
    assert client.index_calls[0]["ts_code"] == "000300.SH"


class _PermissionDeniedClient:
    def daily(self, **kwargs):
        raise RuntimeError("抱歉，您没有权限，积分不足")


def test_tushare_provider_permission_failure_is_structured() -> None:
    provider = TushareMarketDataProvider(token="token", client=_PermissionDeniedClient())

    result = provider.fetch_score_price("600036", "2026-06-09")

    assert result.status == "failed"
    assert result.error_code == PERMISSION_DENIED


def test_default_market_data_provider_uses_tushare_when_token_present(monkeypatch) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "token")

    provider = get_default_market_data_provider()

    assert isinstance(provider, TushareMarketDataProvider)


class _TransientFailureTushareClient:
    def __init__(self, failure_count: int) -> None:
        self.failure_count = failure_count
        self.calls = 0

    def daily(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failure_count:
            raise Exception("Rate limit exceeded")
        return pd.DataFrame(
            [
                {
                    "ts_code": "600036.SH",
                    "trade_date": "20260609",
                    "open": "35.5",
                    "high": "36.0",
                    "low": "35.2",
                    "close": "35.9",
                    "vol": "223456.0",
                }
            ]
        )


@REQUIRES_A_STOCK_LIB_040
def test_tushare_provider_does_not_retry_rate_limit() -> None:
    client = _TransientFailureTushareClient(failure_count=2)
    provider = TushareMarketDataProvider(token="token", client=client)

    result = provider.fetch_score_price("600036", "2026-06-09")

    assert result.status == "failed"
    assert result.error_code == "RATE_LIMITED"
    assert client.calls == 1


class _TimeoutOnceTushareClient(_TransientFailureTushareClient):
    def __init__(self) -> None:
        super().__init__(failure_count=0)
        self.timed_out = False

    def daily(self, **kwargs):
        if not self.timed_out:
            self.timed_out = True
            self.calls += 1
            raise TimeoutError("temporary read timeout")
        return super().daily(**kwargs)


@REQUIRES_A_STOCK_LIB_040
def test_tushare_provider_retries_typed_timeout_once() -> None:
    client = _TimeoutOnceTushareClient()
    provider = TushareMarketDataProvider(token="token", client=client)

    result = provider.fetch_score_price("600036", "2026-06-09")

    assert result.status == "ok"
    assert result.value == pytest.approx(35.9)
    assert client.calls == 2
