"""Research presentation preserves evidence while avoiding raw object/float output."""

from copy import deepcopy

import pytest

from a_stock_tracker.research_client import DemoResearchClient
from a_stock_tracker.research_service import quote
from a_stock_tracker.research_view import result_view
from tests.test_opportunity_view import text_values


def screen_result():
    client = DemoResearchClient()
    request = {
        "id": "a" * 32,
        "mode": "screen",
        "codes": [],
        "confirmed": True,
        "quote": quote(client.info(), "screen"),
    }
    client.submit(request)
    return client.status(request["id"])


def test_source_times_are_labeled_and_preserve_original_timestamps():
    run = screen_result()
    run["result"]["coverage"]["source_times"] = {
        "stock_basic_acquired_at": "2026-10-12T08:01:02+00:00",
        "daily_basic_acquired_at": "2026-10-12T08:02:03+00:00",
    }
    before = deepcopy(run)
    text = "\n".join(text_values(result_view(run, lambda _: None, None)))
    assert "证券基本资料：2026-10-12T08:01:02+00:00" in text
    assert "每日估值资料：2026-10-12T08:02:03+00:00" in text
    assert "stock_basic_acquired_at" not in text
    assert "daily_basic_acquired_at" not in text
    assert run == before


@pytest.mark.parametrize(
    "value,display",
    [(8.253333333333334, "8.25%"), (13.280000000000001, "13.28%"), (None, "未记录")],
)
def test_roe_display_rounding_does_not_change_research_data(value, display):
    run = screen_result()
    run["result"]["candidates"][0]["roe_mean"] = value
    before = deepcopy(run)
    text = "\n".join(text_values(result_view(run, lambda _: None, None)))
    assert f"历史年度平均 ROE：{display}" in text
    assert run == before


def test_source_times_keep_missing_and_unknown_source_evidence():
    run = screen_result()
    run["result"]["coverage"]["source_times"] = {"other_source": "original-time"}
    text = "\n".join(text_values(result_view(run, lambda _: None, None)))
    assert "证券基本资料：未记录" in text
    assert "每日估值资料：未记录" in text
    assert "other_source：original-time" in text
