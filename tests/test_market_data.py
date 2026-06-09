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
    AUTH_MISSING,
    EMPTY_RESPONSE,
    CompositeMarketDataProvider,
    MISSING_COLUMNS,
    PERMISSION_DENIED,
    MarketDataCacheService,
    MarketDataResult,
    SOURCE_DISABLED,
    get_default_market_data_provider,
    get_market_data_backfill_provider,
    _normalize_bars_result,
)
from lib.baostock_provider import (  # noqa: E402
    BAOSTOCK_SOURCE,
    BaoStockMarketDataProvider,
    to_baostock_index_code,
    to_baostock_stock_code,
)
from lib.tushare_provider import (  # noqa: E402
    INDEX_DAILY_SOURCE,
    TushareMarketDataProvider,
    to_tushare_index_code,
    to_tushare_stock_code,
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
    inserted = cache_mod.upsert_daily_bars(tmp_db, "600036", _bars(2), "source", volume_unit="share")
    cache_mod.upsert_daily_bars(tmp_db, "600036", _bars(2), "source", volume_unit="share")
    rows = cache_mod.load_daily_bars(tmp_db, "600036", date.today().isoformat(), 120)

    assert inserted == 2
    assert len(rows) == 2
    assert rows[0]["adjusted"] == "none"
    assert rows[0]["volume_unit"] == "share"
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
        _FakeProvider({
            "600036": MarketDataResult(
                _bars(),
                "ok",
                "source",
                date.today().isoformat(),
                adjusted="none",
                volume_unit="share",
            )
        }),
    )

    coverage = service.refresh_daily_bars(["600036"], date.today().isoformat(), 120)

    assert coverage.ok == 1
    assert tmp_db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 120
    row = tmp_db.execute("SELECT DISTINCT adjusted, volume_unit FROM daily_bars").fetchone()
    assert row == ("none", "share")
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


def test_default_market_data_provider_is_disabled(monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = get_default_market_data_provider()

    result = provider.fetch_l3_bars("600036", date.today().isoformat(), 120)

    assert result.status == "failed"
    assert result.error_code == SOURCE_DISABLED


def test_refresh_daily_bars_uses_disabled_default_provider(tmp_db, monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    coverage = MarketDataCacheService(tmp_db).refresh_daily_bars(["600036"], date.today().isoformat(), 120)

    assert coverage.failed == 1
    assert tmp_db.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 0
    row = tmp_db.execute("SELECT status, error_code FROM market_data_audit").fetchone()
    assert row == ("failed", SOURCE_DISABLED)


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
        self.daily_calls = []
        self.index_calls = []

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
                {"ts_code": "000300.SH", "trade_date": "20260609", "close": "4000.5"},
                {"ts_code": "000300.SH", "trade_date": "20260608", "close": "3980.0"},
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

    assert isinstance(provider, CompositeMarketDataProvider)
    assert isinstance(provider.primary, TushareMarketDataProvider)


def test_baostock_code_conversion() -> None:
    assert to_baostock_stock_code("600036") == "sh.600036"
    assert to_baostock_stock_code("000001") == "sz.000001"
    assert to_baostock_stock_code("300750") == "sz.300750"
    assert to_baostock_index_code("000300") == "sh.000300"


class _BaoLogin:
    error_code = "0"
    error_msg = "success"


class _BaoResult:
    error_code = "0"
    error_msg = "success"
    fields = ["date", "code", "open", "high", "low", "close", "volume"]

    def __init__(self) -> None:
        self.rows = [
            ["2026-06-08", "sh.600036", "35.1", "35.8", "34.9", "35.5", "123456"],
            ["2026-06-09", "sh.600036", "35.5", "36.0", "35.2", "35.9", "223456"],
        ]
        self.index = -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


class _FakeBaoStockClient:
    def __init__(self) -> None:
        self.login_count = 0
        self.logout_count = 0
        self.calls = []

    def login(self):
        self.login_count += 1
        return _BaoLogin()

    def logout(self):
        self.logout_count += 1

    def query_history_k_data_plus(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _BaoResult()


def test_baostock_provider_login_query_logout_and_normalize() -> None:
    client = _FakeBaoStockClient()
    provider = BaoStockMarketDataProvider(client)

    result = provider.fetch_score_price("600036", "2026-06-09")

    assert result.status == "ok"
    assert result.source == BAOSTOCK_SOURCE
    assert result.value == pytest.approx(35.9)
    assert result.adjusted == "none"
    assert result.volume_unit == "share"
    assert client.login_count == 1
    assert client.logout_count == 1
    assert client.calls[0][0][0] == "sh.600036"
    assert client.calls[0][1]["adjustflag"] == "3"


class _BaoErrorResult:
    error_code = "100"
    error_msg = "query failed"
    fields = []

    def next(self):
        return False


class _BaoErrorClient(_FakeBaoStockClient):
    def query_history_k_data_plus(self, *args, **kwargs):
        return _BaoErrorResult()


def test_baostock_provider_query_error_is_structured() -> None:
    result = BaoStockMarketDataProvider(_BaoErrorClient()).fetch_l3_bars("600036", "2026-06-09", 120)

    assert result.status == "failed"
    assert result.error_code
    assert result.error_message == "query failed"


class _AlwaysFailedProvider:
    def fetch_score_price(self, code: str, score_date: str):
        return MarketDataResult(None, "failed", "primary", date.today().isoformat(), error_code=EMPTY_RESPONSE)

    def fetch_l3_bars(self, code: str, end_date: str, window: int):
        return MarketDataResult(None, "failed", "primary", date.today().isoformat(), error_code=EMPTY_RESPONSE)

    def fetch_outcome_price(self, code: str, target_date: str):
        return MarketDataResult(None, "failed", "primary", date.today().isoformat(), error_code=EMPTY_RESPONSE)

    def fetch_index_bars(self, symbol: str):
        return MarketDataResult(None, "failed", "primary", date.today().isoformat(), error_code=EMPTY_RESPONSE)


def test_composite_provider_uses_baostock_fallback_as_degraded() -> None:
    provider = CompositeMarketDataProvider(_AlwaysFailedProvider(), BaoStockMarketDataProvider(_FakeBaoStockClient()))

    result = provider.fetch_score_price("600036", "2026-06-09")

    assert result.status == "degraded"
    assert result.source == BAOSTOCK_SOURCE
    assert result.fallback_source == "primary"
    assert result.fallback_reason == EMPTY_RESPONSE
    assert result.value == pytest.approx(35.9)


def test_backfill_provider_allows_explicit_baostock_only(monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("MARKET_DATA_ALLOW_BAOSTOCK_ONLY", "1")

    provider = get_market_data_backfill_provider()

    assert isinstance(provider, BaoStockMarketDataProvider)
