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
        ("较大距离", "600002.SH"),
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
    assert "执行：部分结果" in text and "覆盖：未完成不可继续" in text
    assert "结果：4 家" in text and "本轮用量：未记录" in text
    assert "/analysis/" + "b" * 32 in routes
    assert not any("resume_from" in r for r in routes)
    assert all(word not in text for word in ("partial", "incomplete", "excluded", "passed"))


def test_wide_distance_visible_as_reference():
    from a_stock_tracker.opportunity_demo import demo_board
    from a_stock_tracker.opportunity_view import opportunity_view

    board = demo_board("wide")
    row = board["rows"][0]
    assert row["status"] == "reference"
    text = "\n".join(text_values(opportunity_view(board, row["code"], lambda _: None, None)))
    assert row["facts"]["distance"] in text and "28.57%" in text
    assert "规则上限" not in text and row["as_of"] in text
    assert row["facts"]["condition"] in text and row["facts"]["invalidation"] in text


@pytest.mark.parametrize(
    "status,review,error,label",
    [
        ("scoped_complete", "passed", None, "引用与语义复核通过，范围内完整"),
        ("incomplete", "passed", None, "引用已复核；有 2 页原文未纳入分析，结论范围受限"),
        ("incomplete", "failed", "复核失败", "独立复核未通过，草稿不展示"),
        ("incomplete", None, "读取失败", "原文/模型检查失败，没有可读结论"),
    ],
)
def test_structured_report_hierarchy_and_failed_draft_is_hidden(status, review, error, label):
    import flet as ft

    from a_stock_tracker.opportunity_demo import demo_report
    from a_stock_tracker.research_view import report_controls
    from tests.test_app import app_controls

    report = demo_report("600001.SH")
    report.update(status=status, evidence_review=review, error=error)
    report["gaps"] = [f"缺口{i}" for i in range(5)]
    report["analysis"]["counterevidence"]["text"] = "完整反证第一句。第二句也必须保留。"
    view = ft.Column(report_controls(report))
    texts = text_values(view)
    assert label in texts
    assert any("2026-04-01" in t and "2025-12-31" in t for t in texts)
    if review == "passed":
        assert texts.index("完整反证第一句。第二句也必须保留。") < texts.index("业务")
        assert "引用：F1" in texts
        tiles = [c for c in app_controls(view) if isinstance(c, ft.ExpansionTile)]
        assert any("[F1]" in t.title.value for t in tiles)
        assert any("PDF 第 12 页" in t for t in texts)
        assert "来源 URL：https://example.invalid/synthetic.pdf" in texts
        summary = text_values(view.controls[0])
        assert "缺口2" in summary and "缺口3" not in summary
        assert "缺口4" in texts
    else:
        assert "完整反证第一句。第二句也必须保留。" not in texts
        assert not any("合成收入集中事实" in t for t in texts)
    assert not any(isinstance(c, (ft.Markdown, ft.Image)) for c in app_controls(view))


def test_recent_summaries_show_counter_first_sentence_and_hide_expired_prices():
    import flet as ft

    from a_stock_tracker.opportunity_demo import demo_board, demo_report
    from a_stock_tracker.research_service import result_summary
    from a_stock_tracker.research_view import recent_controls

    run = screen_result()
    report = demo_report("600001.SH")
    run["summary"] = result_summary({"reports": [report]})
    routes = []
    text = "\n".join(text_values(ft.Column(recent_controls([run], lambda r: routes.append(r)))))
    assert "合成反证：单一产品收入集中。" in text
    assert "需求变化可能影响回款" not in text
    assert "2026-04-01" in text and "/analysis/" + run["id"] in routes
    board = demo_board("expired")
    run["summary"] = result_summary({"board": board})
    text = "\n".join(text_values(ft.Column(recent_controls([run], lambda _: None))))
    assert "已过期" in text
    assert "11.20" not in text and "11.00" not in text and "1.79%" not in text


@pytest.mark.parametrize("status", ["completed", "partial", "failed", "interrupted"])
def test_financial_usage_labels_frozen_scope_without_changing_data_or_report_gaps(status):
    from a_stock_tracker.opportunity_demo import demo_report

    run = screen_result()
    run["request"]["mode"] = "financial"
    run["request"]["codes"] = ["600001.SH"]
    run["status"] = status
    usage = {
        "requests": 8,
        "request_limit": 100,
        "seconds_used": 121.16,
        "stop_reason": "completed" if status in {"completed", "partial"} else "error",
        "newly_covered": 1,
        "covered": 2654,
        "remaining": 2920,
    }
    run["usage"] = usage
    run["result"]["usage"] = usage
    report = demo_report("600001.SH")
    report.update(status="incomplete", evidence_review="passed", omitted_pages=269)
    run["result"]["reports"] = [report]
    before = deepcopy(run)
    text = "\n".join(text_values(result_view(run, lambda _: None, None)))
    assert "本轮请求：8 / 100 次；耗时：121.16 秒" in text
    assert "冻结证券范围背景统计（非本次单家公司研究进度）" in text
    assert "本轮新增财报扫描：1；已归类或扫描：2654；尚未扫描：2920" in text
    assert "已归类或扫描包含预先排除和资料缺口" in text
    assert "本次指定范围以外的证券" in text
    assert "累计覆盖：2654" not in text
    if status in {"completed", "partial"}:
        assert "269 页原文未纳入分析" in text
    assert run == before


def test_screen_usage_keeps_progress_and_financial_missing_usage_stays_unknown():
    from a_stock_tracker.research_view import usage_text

    run = screen_result()
    text = "\n".join(text_values(result_view(run, lambda _: None, None)))
    assert "本轮新增覆盖：1；累计覆盖：5；剩余未覆盖：1" in text
    assert "冻结证券范围背景统计" not in text
    assert usage_text(None, "financial") == "本轮用量：未记录"
    assert "已归类或扫描：未记录；尚未扫描：未记录" in usage_text({"requests": 8}, "financial")


def test_screen_candidates_precede_execution_and_usage_with_one_summary():
    import flet as ft

    from tests.test_app import app_controls

    run = screen_result()
    view = result_view(run, lambda _: None, None)
    field_index = next(i for i, c in enumerate(view.controls) if isinstance(c, ft.TextField))
    prefix = view.controls[:field_index]
    assert sum(isinstance(c, ft.Container) for c in prefix) == 1
    prefix_text = "\n".join(text_values(ft.Column(prefix)))
    assert "可研究 4 家" in prefix_text and "尚未覆盖 1 家" in prefix_text
    assert "资料缺口 0 家" in prefix_text and "合成日期" in prefix_text
    assert "不是完整行业前三" in prefix_text
    assert "执行：" not in prefix_text and "本轮请求：" not in prefix_text
    evidence = view.controls[-1]
    assert evidence.title.value == "筛选依据与用量"
    assert "本轮请求：" in "\n".join(text_values(evidence))
    assert "PB 名次 " in "\n".join(text_values(evidence))
    assert not any(
        isinstance(c, ft.ExpansionTile) and c.title.value == "排名依据" for c in app_controls(view)
    )


@pytest.mark.parametrize("readable", [True, False])
def test_report_price_navigation_prefills_without_screen_origin_or_submission(readable):
    import flet as ft

    from a_stock_tracker.opportunity_demo import demo_report
    from tests.test_app import app_controls

    run = screen_result()
    run["request"]["mode"] = "financial"
    report = demo_report("600001.SH")
    if not readable:
        report.update(status="incomplete", evidence_review="failed")
    run["result"]["reports"] = [report]
    visited = []
    view = result_view(run, lambda route: lambda _: visited.append(route), None)
    action = next(
        c
        for c in app_controls(view)
        if isinstance(c, ft.TextButton) and c.content == "核对本公司价格参考"
    )
    assert action.height == 48 and not visited
    action.on_click(None)
    assert visited == ["/analysis?mode=swing&code=600001.SH"]
    if not readable:
        assert "不构成对财报判断的支持" in "\n".join(text_values(view))


@pytest.mark.parametrize("status", ["running", "completed", "partial", "failed", "interrupted"])
def test_recent_groups_and_actions_distinguish_execution_from_result(status):
    import flet as ft

    from a_stock_tracker.research_view import recent_controls
    from tests.test_app import app_controls

    run = screen_result()
    run["status"] = status
    view = ft.Column(recent_controls([run], lambda _: None))
    texts = text_values(view)
    assert ("进行中" in texts) == (status == "running")
    actions = [c for c in app_controls(view) if isinstance(c, ft.TextButton)]
    assert actions[0].content.value == ("查看进度" if status == "running" else "查看结果")
    if status == "partial":
        assert any("部分结果" in text for text in texts)
        assert not any("完成" in text or "成功" in text for text in texts)


@pytest.mark.parametrize("status", ["completed", "partial"])
def test_financial_without_report_artifact_is_not_a_screening_result(status):
    import flet as ft

    from tests.test_app import app_controls

    run = screen_result()
    run["request"].update(mode="financial", codes=["600001.SH"])
    run["status"] = status
    run["result"]["reports"] = []
    run["result"]["usage"]["stop_reason"] = "request_budget"
    before = deepcopy(run)
    visited = []
    view = result_view(run, lambda route: lambda _: visited.append(route), None)
    text = "\n".join(text_values(view))
    assert "没有可读的公司财报报告" in text
    assert "停止原因：请求预算用尽" in text
    assert "冻结证券范围背景统计（非本次单家公司研究进度）" in text
    assert "本次指定范围以外的证券" in text
    assert "不构成对财报判断的支持" in text
    assert text.count("执行：") == 1 and text.count("本轮请求：") == 1
    assert all(
        label not in text
        for label in (
            "选择公司研究财报",
            "可研究 0 家",
            "初筛尚未完成",
            "继续未覆盖初筛",
            "筛选依据与用量",
        )
    )
    action = next(
        c
        for c in app_controls(view)
        if isinstance(c, ft.TextButton) and c.content == "核对本公司价格参考"
    )
    assert not visited
    action.on_click(None)
    assert visited == ["/analysis?mode=swing&code=600001.SH"]
    assert run == before
