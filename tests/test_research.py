import json

import pytest

from a_stock_tracker import research as screen


def test_fetch_universe_rejects_duplicate_valuations() -> None:
    import pandas as pd

    class Client:
        def stock_basic(self, **_kwargs):
            return pd.DataFrame(
                [
                    {
                        "ts_code": "600001.SH",
                        "name": "合成",
                        "industry": "电力",
                        "market": "主板",
                        "exchange": "SSE",
                        "list_status": "L",
                    }
                ]
            )

        def daily_basic(self, **_kwargs):
            return pd.DataFrame(
                [
                    {"ts_code": "600001.SH", "trade_date": "20260928", "pb": 1, "total_mv": 100},
                    {"ts_code": "600001.SH", "trade_date": "20260928", "pb": 9, "total_mv": 900},
                ]
            )

    with pytest.raises(screen.ScreenError, match="daily_basic 含重复证券代码"):
        screen.fetch_universe(Client(), "synthetic", "2026-09-28")


def test_fetch_financials_rejects_mismatched_code() -> None:
    class Frame:
        columns = ["ts_code", "ann_date", "end_date", "update_flag", "roe_waa"]

        def to_json(self, **_kwargs: object) -> str:
            return json.dumps(
                [
                    {"ts_code": "600001.SH"},
                    {"ts_code": "600002.SH"},
                ]
            )

    class Client:
        def fina_indicator(self, **_kwargs: object) -> Frame:
            return Frame()

    with pytest.raises(screen.ScreenError, match="fina_indicator 包含错配代码: 600002.SH"):
        screen.fetch_financials(
            Client(), "unused", "600001.SH", "2026-09-20", "2026-09-20T16:00:00+08:00"
        )


def test_fetch_financials_rejects_duplicate_response_rows() -> None:
    class Frame:
        columns = ["ts_code", "ann_date", "end_date", "update_flag", "roe_waa"]

        def to_json(self, **_kwargs: object) -> str:
            row = {
                "ts_code": "600001.SH",
                "ann_date": "20260415",
                "end_date": "20251231",
                "update_flag": "0",
                "roe_waa": 10.0,
            }
            return json.dumps([row, row])

    class Client:
        def fina_indicator(self, **_kwargs: object) -> Frame:
            return Frame()

    with pytest.raises(screen.ScreenError, match="重复记录"):
        screen.fetch_financials(
            Client(), "synthetic", "600001.SH", "2026-09-20", "2026-09-20T16:00:00+08:00"
        )


def test_scope_cap_and_average_tie_ranking() -> None:
    stocks = [
        {
            "ts_code": "600900.SH",
            "name": "参照",
            "industry": "水力发电",
            "market": "主板",
            "exchange": "SSE",
            "list_status": "L",
        },
        {
            "ts_code": "600001.SH",
            "name": "同业甲",
            "industry": "水力发电",
            "market": "主板",
            "exchange": "SSE",
            "list_status": "L",
        },
        {
            "ts_code": "000001.SZ",
            "name": "同业乙",
            "industry": "水力发电",
            "market": "主板",
            "exchange": "SZSE",
            "list_status": "L",
        },
        {
            "ts_code": "600002.SH",
            "name": "同业丙",
            "industry": "水力发电",
            "market": "主板",
            "exchange": "SSE",
            "list_status": "L",
        },
        {
            "ts_code": "300001.SZ",
            "name": "创业板",
            "industry": "水力发电",
            "market": "创业板",
            "exchange": "SZSE",
            "list_status": "L",
        },
        {
            "ts_code": "920001.BJ",
            "name": "北交所",
            "industry": "水力发电",
            "market": "北交所",
            "exchange": "BSE",
            "list_status": "L",
        },
    ]
    valuations = [
        {"ts_code": "600900.SH", "pb": 2, "total_mv": 50},
        {"ts_code": "600001.SH", "pb": 1, "total_mv": 100},
        {"ts_code": "000001.SZ", "pb": 2, "total_mv": 90},
        {"ts_code": "600002.SH", "pb": 3, "total_mv": 80},
    ]
    selected, scope, _ = screen.load_peers(stocks, valuations, "600900.SH", ["600900"], cap=3)
    assert [row["ts_code"] for row in selected] == [
        "600900.SH",
        "600001.SH",
        "000001.SZ",
    ]
    assert scope["excluded_by_cap"] == ["600002.SH"]

    rows = [
        {
            "code": "600900.SH",
            "pb": 2.0,
            "roe_mean": 10.0,
            "annual_roes": [{}, {}, {}],
            "exclusions": [],
        },
        {
            "code": "600001.SH",
            "pb": 1.0,
            "roe_mean": 10.0,
            "annual_roes": [{}, {}, {}],
            "exclusions": [],
        },
        {
            "code": "000001.SZ",
            "pb": 3.0,
            "roe_mean": -1.0,
            "annual_roes": [{}, {}, {}],
            "exclusions": ["NON_POSITIVE_ROE_MEAN"],
        },
    ]
    result = screen.rank_peers(rows, "600900.SH", ["600900"])
    ranking = {item["code"]: item for item in result["ranking"]}
    assert ranking["600001.SH"]["roe_rank"] == 1.5
    assert ranking["600900.SH"]["roe_rank"] == 1.5
    assert ranking["600001.SH"]["position"] == 1
    assert ranking["600900.SH"]["position"] == 2


def test_three_year_selection_prefers_unique_revision_and_rejects_gap() -> None:
    records = [
        {
            "end_date": "20251231",
            "ann_date": "20260320",
            "roe_waa": 8,
            "update_flag": "0",
            "source": "test",
        },
        {
            "end_date": "20251231",
            "ann_date": "20260321",
            "roe_waa": 9,
            "update_flag": "1",
            "source": "test",
            "acquired_at": "2026-09-20T10:00:00+08:00",
        },
        {
            "end_date": "20251231",
            "ann_date": "20260321",
            "roe_waa": 9,
            "update_flag": "1",
            "source": "test",
            "acquired_at": "2026-09-22T10:00:00+08:00",
        },
        {
            "end_date": "20241231",
            "ann_date": "20250320",
            "roe_waa": 10,
            "update_flag": "0",
            "source": "test",
        },
        {
            "end_date": "20231231",
            "ann_date": "20240320",
            "roe_waa": 11,
            "update_flag": "0",
            "source": "test",
        },
    ]
    selected = screen.select_annual_roes(records, "2026-09-22", "2026-09-22T20:00:00+08:00")
    assert selected["error"] is None
    assert selected["roe_mean"] == 10
    assert selected["annual_roes"][-1]["roe_waa"] == 9
    assert selected["annual_roes"][-1]["selection_basis"] == "unique_update_flag_1"
    assert selected["annual_roes"][-1]["acquired_at"] == "2026-09-22T10:00:00+08:00"

    gap = [row for row in records if row["end_date"] != "20241231"]
    assert (
        screen.select_annual_roes(gap, "2026-09-22", "2026-09-22T20:00:00+08:00")["error"]
        == "MISSING_THREE_ANNUAL_REPORTS"
    )
