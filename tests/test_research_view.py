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
    assert "证券基本资料：2026-10-12 16:01" in text
    assert "每日估值资料：2026-10-12 16:02" in text
    assert "stock_basic_acquired_at" not in text
    assert "daily_basic_acquired_at" not in text
    assert run == before


@pytest.mark.parametrize(
    "value,display",
    [(8.253333333333334, "8.25%"), (13.280000000000001, "13.28%"), (None, "未记录")],
)
def test_roe_display_rounding_does_not_change_research_data(value, display):
    run = screen_result()
    run["result"]["groups"][0]["ranking"][0]["roe_mean"] = value
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


def test_group_filter_and_source_routes_preserve_rankings_and_guard_late_events():
    from types import SimpleNamespace
    from unittest.mock import Mock

    import flet as ft

    from a_stock_tracker.research_view import ranking_controls

    run = screen_result()
    groups = run["result"]["groups"]
    before = deepcopy(groups)
    routes = []
    active = [True]
    controls = ranking_controls(groups, run["id"], lambda r: routes.append(r), lambda: active[0])
    field, matches, browse = controls
    tiles = browse.controls
    assert [t.expanded for t in tiles] == [True, True, True, False]
    for term, code in [
        ("600004", "600004.SH"),
        ("距离排除", "600002.SH"),
        ("合成能源", "600003.SH"),
    ]:
        field.value = term
        field.on_change(SimpleNamespace(control=field, page=Mock()))
        assert matches.visible and not browse.visible
        assert any(code in v for v in text_values(matches))
    field.value = ""
    field.on_change(SimpleNamespace(control=field, page=Mock()))
    assert browse.visible and not matches.visible
    active[0] = False
    field.value = "不存在"
    page = Mock()
    field.on_change(SimpleNamespace(control=field, page=page))
    page.update.assert_not_called()
    assert groups == before
    assert all("from=" + run["id"] in route for route in routes)
    assert sum(isinstance(t, ft.ExpansionTile) for t in tiles) == 4


def test_large_groups_render_lazily_and_cap_search_results():
    from types import SimpleNamespace
    from unittest.mock import Mock

    from a_stock_tracker.research_view import RANK_PREVIEW, SEARCH_LIMIT, ranking_controls

    rows = [
        {"code": f"600{i:03d}.SH", "name": f"公司{i}", "pb": 1.0, "order": float(i)}
        for i in range(40)
    ]
    groups = [{"industry": "大行业", "periods": ["2025-12-31"], "ranking": rows}]
    field, matches, browse = ranking_controls(groups, "a" * 32, lambda r: None, lambda: True)
    tile = browse.controls[0]
    more = tile.controls[-1]
    assert len(tile.controls) == RANK_PREVIEW + 1 and "全部 40 家" in more.content
    more.on_click(SimpleNamespace(page=Mock()))
    assert len(tile.controls) == 40
    field.value = "公司"
    field.on_change(SimpleNamespace(control=field, page=Mock()))
    assert len(matches.controls) == SEARCH_LIMIT + 1
    assert "共 40 家匹配" in text_values(matches)[-1]


def test_three_status_layers_legacy_usage_and_continued_checkpoint():
    run = screen_result()
    run["result"].pop("usage")
    run["continued_by"] = "b" * 32
    run["can_continue"] = False
    routes = []
    text = "\n".join(text_values(result_view(run, lambda r: routes.append(r), None)))
    assert "执行：已结束" in text and "覆盖：未完成不可继续" in text
    assert "结果：4 家" in text and "本轮用量：未记录" in text
    assert "/analysis/" + "b" * 32 in routes
    assert not any("resume_from" in r for r in routes)
    assert all(word not in text for word in ("partial", "incomplete", "excluded", "passed"))


def test_excluded_distance_visible_without_actionable_prices():
    from a_stock_tracker.opportunity_demo import demo_board
    from a_stock_tracker.opportunity_view import opportunity_view

    board = demo_board("excluded")
    row = board["rows"][0]
    assert row["status"] == "excluded"
    text = "\n".join(text_values(opportunity_view(board, None, lambda _: None, None)))
    assert row["facts"]["distance"] in text
    assert "规则上限：8%" in text and row["as_of"] in text
    assert row["facts"]["condition"] not in text and row["facts"]["invalidation"] not in text
