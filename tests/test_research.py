import json

import pytest

from a_stock_tracker import research as screen


@pytest.mark.parametrize(
    "value",
    ["202681", 202681, "2026-8-01", "20-2608-01", "２０２６０８０１", "20260230", None, True],
)
def test_date_boundary_rejects_malformed_values(value) -> None:
    with pytest.raises(screen.ScreenError):
        screen.parse_date(value)


def test_date_and_annual_selection_boundary() -> None:
    assert screen.iso_date(20260801) == screen.iso_date("2026-08-01") == "2026-08-01"
    rows = [
        {
            "end_date": f"{year}1231",
            "ann_date": f"{year + 1}0420",
            "roe_waa": 10,
            "update_flag": "0",
        }
        for year in (2023, 2024, 2025)
    ]
    assert screen.select_annual_roes(rows, "2026-09-30", "2026-10-06")["error"] is None
    for invalid in ("202681", "20250101"):
        damaged = [*rows[:-1], {**rows[-1], "ann_date": invalid}]
        assert (
            screen.select_annual_roes(damaged, "2026-09-30", "2026-10-06")["error"]
            == "INVALID_REPORT_DATE"
        )


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


@pytest.mark.parametrize(
    "rows,error",
    [
        (
            [{"ts_code": "600001.SH"}, {"ts_code": "600002.SH"}],
            "fina_indicator 包含错配代码: 600002.SH",
        ),
        (
            [
                {
                    "ts_code": "600001.SH",
                    "ann_date": "20260415",
                    "end_date": "20251231",
                    "update_flag": "0",
                    "roe_waa": 10.0,
                }
            ]
            * 2,
            "重复记录",
        ),
    ],
    ids=["mismatched-security", "duplicate-response"],
)
def test_fetch_financials_rejects_invalid_response(rows, error) -> None:
    class Frame:
        columns = ["ts_code", "ann_date", "end_date", "update_flag", "roe_waa"]

        def to_json(self, **_kwargs: object) -> str:
            return json.dumps(rows)

    class Client:
        def fina_indicator(self, **_kwargs: object) -> Frame:
            return Frame()

    with pytest.raises(screen.ScreenError, match=error):
        screen.fetch_financials(
            Client(), "synthetic", "600001.SH", "2026-09-20", "2026-09-20T16:00:00+08:00"
        )


@pytest.mark.parametrize(
    "reverse,expected",
    [
        (False, {"600001.SH": 1.5, "600002.SH": 1.5, "600003.SH": 3.0}),
        (True, {"600001.SH": 2.5, "600002.SH": 2.5, "600003.SH": 1.0}),
    ],
)
def test_average_ranks_preserves_ties(reverse, expected) -> None:
    rows = [
        {"code": "600002.SH", "value": 10.0},
        {"code": "600003.SH", "value": 20.0},
        {"code": "600001.SH", "value": 10.0},
    ]
    assert screen.average_ranks(rows, "value", reverse) == expected
    assert screen.average_ranks([], "value", reverse) == {}


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
