"""Presentation rules ported from the retired Flet views; inputs are never mutated."""

from __future__ import annotations

from copy import deepcopy

import pytest

from a_stock_tracker.opportunity_demo import demo_board, demo_report
from a_stock_tracker.web import presenters as p


def test_source_times_are_labeled_and_keep_unknown_sources():
    times = {
        "stock_basic_acquired_at": "2026-10-12T08:01:02+00:00",
        "daily_basic_acquired_at": "2026-10-12T08:02:03+00:00",
    }
    text = p.source_times_text(times)
    assert "证券基本资料：2026-10-12 16:01" in text and "每日估值资料：2026-10-12 16:02" in text
    assert "stock_basic_acquired_at" not in text
    other = p.source_times_text({"other_source": "original-time"})
    assert "证券基本资料：未记录" in other and "other_source：original-time" in other


@pytest.mark.parametrize(
    "value,display",
    [
        (8.253333333333334, "8.25%"),
        (13.280000000000001, "13.28%"),
        (None, "未记录"),
        (True, "未记录"),
    ],
)
def test_roe_display_rounding(value, display):
    assert p.roe_text(value) == display


def test_budget_duration_preserves_exact_confirmed_limit():
    assert p.budget_duration(1800) == "30 分钟"
    assert p.budget_duration(66) == "1 分钟 6 秒"


def test_codes_normalize_like_the_old_form():
    assert p.normalize_codes(" 600519，000001.sz 600002\n") == [
        "600519.SH",
        "000001.SZ",
        "600002.SH",
    ]


def test_financial_usage_labels_frozen_scope_and_unknown_stays_unknown():
    usage = {
        "requests": 8,
        "request_limit": 100,
        "seconds_used": 121.16,
        "stop_reason": "completed",
        "newly_covered": 1,
        "covered": 2654,
        "remaining": 2920,
    }
    before = deepcopy(usage)
    text = p.usage_text(usage, "financial")
    assert "本轮请求：8 / 100 次；耗时：121.16 秒" in text
    assert "冻结证券范围背景统计（非本次单家公司研究进度）" in text
    assert "本轮新增财报扫描：1；已归类或扫描：2654；尚未扫描：2920" in text
    assert "本次指定范围以外的证券" in text and "累计覆盖" not in text
    assert usage == before
    assert "本轮新增覆盖：1；累计覆盖：2654" in p.usage_text(usage, "screen")
    assert p.usage_text(None, "financial") == "本轮用量：未记录"
    assert "已归类或扫描：未记录；尚未扫描：未记录" in p.usage_text({"requests": 8}, "financial")


@pytest.mark.parametrize(
    "status,label",
    [
        ("running", "执行中"),
        ("failed", "失败"),
        ("interrupted", "中断"),
        ("completed", "初筛完成"),
        ("partial", "初筛未全部完成"),
        ("unknown", "状态待核查"),
    ],
)
def test_recent_labels_distinguish_execution_from_result(status, label):
    run = {"id": "a" * 32, "status": status, "request": {"mode": "screen"}, "summary": {}}
    assert p.recent_result_label(run) == label


def test_recent_financial_labels_depend_on_readable_reports():
    readable = {"status": "completed", "summary": {"reports": [{"evidence_review": "passed"}]}}
    unreadable = {"status": "completed", "summary": {"reports": [{"evidence_review": "failed"}]}}
    assert p.recent_result_label(readable) == "研究完成"
    assert p.recent_result_label(unreadable) == "未能生成结论"


def test_board_summary_hides_expired_levels_and_explains_gaps():
    row = demo_board()["rows"][0]
    live = p.board_summary(row)
    assert live["badge"] == {"text": "有效期内", "tone": ""}
    assert live["levels"] == (
        "收盘 11.20 元，在 20 日和 60 日均线上方，走势偏强。站上 11.23 元（近 20 日最高）才算转强；"
        "跌破 11.00 元（近 10 日最低）说明走势转坏，离收盘还有 1.79%。"
    )
    assert live["expires"] == row["expires"] and live["helper"] == p.price_dates(row)
    expired_row = demo_board("expired")["rows"][0]
    expired = p.board_summary(expired_row)
    assert expired["badge"] == {"text": "已过期", "tone": "caution"}
    assert expired["levels"] is None and expired["expires"] is None
    shown = expired["text"] + expired["helper"]
    assert "11.00" not in shown and "1.79%" not in shown
    assert f"原有效至 {expired_row['expires']}，已过期" in expired["helper"]
    gap = p.board_summary(demo_board("gap")["rows"][0])
    assert gap["badge"]["text"] == "资料缺口" and gap["text"].startswith("合成日线资料缺失")
    assert gap["levels"] is None and gap["helper"] == ""


@pytest.mark.parametrize(
    "row,text,tone",
    [
        ({"status": "reference"}, "有效期内", ""),
        ({"status": "observation"}, "旧规则 · 有效期内", ""),
        ({"status": "observation", "expired": True}, "已过期", "caution"),
        ({"status": "excluded"}, "旧规则未入选", "plain"),
        ({"status": "gap"}, "资料缺口", "caution"),
    ],
)
def test_price_badges_never_use_a_success_tone(row, text, tone):
    assert p.price_badge(row) == {"text": text, "tone": tone}


def test_validity_and_disclosure_texts_keep_unknowns_explicit():
    assert p.validity_text({"status": "reference"}) == "有效期：未记录"
    assert p.disclosure_text({}) == "披露期：未记录"
    report = {"documents": [{"period": "2025-06-30", "announced": None}]}
    assert p.disclosure_text(report) == "披露期：2025-06-30（公告 未记录）"


def run(identifier, mode, status="completed", codes=("600001.SH",)):
    return {
        "id": identifier,
        "status": status,
        "request": {"mode": mode, "codes": [] if mode == "screen" else list(codes)},
        "created_at": "2026-10-15T08:00:00+00:00",
    }


def test_tab_groups_pin_running_work_and_refilter_modes():
    running = run("a" * 32, "financial", "running")
    runs = {
        "all": [running, run("b" * 32, "swing")],
        "financial": [running, run("c" * 32, "financial")],
        # A receiver that ignores the mode filter must not mix tabs.
        "screen": [run("d" * 32, "screen"), run("e" * 32, "swing")],
        "swing": [],
    }
    before = deepcopy(runs)
    view = p.tab_groups(runs, "overview", 3)
    assert [i["id"] for i in view["active"]] == ["a" * 32]
    groups = {g["mode"]: g for g in view["groups"]}
    assert list(groups) == ["financial", "screen", "swing"]
    assert [i["id"] for i in groups["financial"]["items"]] == ["c" * 32]
    assert [i["id"] for i in groups["screen"]["items"]] == ["d" * 32]
    assert groups["screen"]["items"][0]["scope"] == "全市场初筛"
    assert groups["financial"]["more"] == "/?view=financial" and groups["swing"]["more"] is None
    assert groups["swing"]["empty"] == "暂无价格参考记录。"
    assert all(g["next"] is None for g in view["groups"])
    assert runs == before


def test_tab_groups_offer_an_older_page_only_when_the_page_is_full():
    full = [run(f"{i:032x}", "swing") for i in range(2)]
    page = p.tab_groups({"all": [], "swing": full}, "swing", 2)
    assert [g["mode"] for g in page["groups"]] == ["swing"]
    assert page["groups"][0]["next"] == f"/?view=swing&cursor={full[-1]['id']}"
    assert page["groups"][0]["more"] is None
    assert p.tab_groups({"all": [], "swing": full[:1]}, "swing", 2)["groups"][0]["next"] is None


def test_company_view_orders_reports_and_surfaces_unreadable_attempts():
    reports = {
        "code": "600001.SH",
        "reports": [
            {"run_id": "a" * 32, "code": "600001.SH", "name": "", "status": "scoped_complete"},
            {"run_id": "b" * 32, "code": "600001.SH", "name": "演示公司", "status": "unchanged"},
        ],
    }
    failed = run("c" * 32, "financial", "failed")
    view = p.company_view("600001.SH", reports, [], [failed])
    assert view["name"] == "演示公司" and view["latest"]["run_id"] == "a" * 32
    assert [r["run_id"] for r in view["archive"]] == ["b" * 32]
    assert view["latest"]["status"] == "研究完成" and view["latest"]["conclusion"] == ""
    assert view["latest"]["gaps"] == "待补资料 0 条" and view["price"] is None
    assert view["attempt"]["id"] == "c" * 32
    readable = run("d" * 32, "financial") | {
        "summary": {"reports": [{"code": "600001.SH", "evidence_review": "passed"}]}
    }
    assert p.company_view("600001.SH", reports, [], [readable])["attempt"] is None
    synthetic = {"run_id": "e" * 32, "code": "600001.SH", "status": "synthetic"}
    synthetic["evidence_review"] = "not_real"
    view = p.company_view("600001.SH", {"reports": [synthetic]}, [], [])
    assert view["latest"]["status"] == "合成报告"


def test_company_view_keeps_only_the_company_price_rows():
    board = demo_board()
    other = deepcopy(board["rows"][0]) | {"code": "600002.SH"}
    swing = run("a" * 32, "swing", codes=("600001.SH", "600002.SH")) | {
        "summary": {"board": {"rows": [board["rows"][0], other]}}
    }
    price = p.company_view("600001.SH", {"reports": []}, [swing], [])["price"]
    assert price["id"] == "a" * 32 and not price["active"]
    assert [s["code"] for s in price["summaries"]] == ["600001.SH"]


@pytest.mark.parametrize("omitted", [{}, {"omitted_pages": None}, {"omitted_pages": -1}])
def test_missing_failed_report_evidence_never_implies_zero_pages_or_known_dates(omitted):
    report = {"code": "002236.SZ", "status": "incomplete", "error": "失败", **omitted}
    before = deepcopy(report)
    view = p.report_view(report)
    assert view["omitted"] == "未记录（无法确认页数）" and view["dates"] == "资料日期：未记录"
    assert not view["readable"] and not view["sections"] and not view["facts"]
    assert report == before


@pytest.mark.parametrize("count", [0, 2])
def test_recorded_omitted_page_counts_remain_visible(count):
    report = demo_report("600001.SH") | {"omitted_pages": count}
    assert p.report_view(report)["omitted"] == f"{count} 页"


def test_report_view_collapses_all_none_clues_but_keeps_recorded_ones():
    base = {"code": "002236.SZ", "status": "incomplete", "error": "失败"}
    assert p.report_view(base | {"clues": {"industry": None, "pb": None}})["clues"] == {}
    assert p.report_view(base | {"clues": None})["clues"] == {}
    clues = {"industry": "银行", "pb": None}
    assert p.report_view(base | {"clues": clues})["clues"] == clues


def test_report_view_includes_name_when_available():
    named = p.report_view({"code": "600519.SH", "name": "贵州茅台", "status": "scoped_complete"})
    assert named["name"] == "贵州茅台"
    unnamed = p.report_view({"code": "600519.SH", "status": "scoped_complete"})
    assert unnamed["name"] == ""


def test_failed_review_exposes_diagnostic_but_never_the_draft():
    report = demo_report("600001.SH") | {
        "evidence_review": "failed",
        "status": "incomplete",
        "diagnostic": {"stage": "independent_review", "category": "x", "message": "复核未通过"},
        "error": "复核未通过",
    }
    view = p.report_view(report)
    assert view["stage"] == "独立复核" and view["stage_message"] == "复核未通过"
    assert view["error"] is None and view["counter"] is None and not view["facts"]


@pytest.mark.parametrize(
    "mode,needle,absent",
    [
        ("financial", "输出 token 与费用无硬上限", None),
        ("screen", "初筛门槛：0＜PB≤3", "费用无硬上限"),
        ("swing", "不调用模型", "费用无硬上限"),
    ],
)
def test_quote_lines_follow_the_selected_mode(mode, needle, absent):
    from a_stock_tracker.research_client import DemoResearchClient
    from a_stock_tracker.research_service import quote

    info = DemoResearchClient().info().model_dump(by_alias=True, exclude_unset=True)
    lines = "\n".join(p.quote_lines(info, mode, quote(info, mode)))
    assert needle in lines and "合成演示：不调用真实接口" in lines
    if absent:
        assert absent not in lines


def test_frozen_costs_repeat_the_confirmed_offer():
    from a_stock_tracker.research_client import DemoResearchClient
    from a_stock_tracker.research_service import quote

    info = DemoResearchClient().info()
    payload = {"codes": ["600519.SH"], "quote": quote(info, "financial")}
    lines = "\n".join(p.frozen_cost_lines(payload))
    assert "范围：600519.SH" in lines and "最多 10 次调用" in lines
    assert "失败调用也可能计费" in lines
    assert "不调用模型" in "\n".join(
        p.frozen_cost_lines({"codes": [], "quote": quote(info, "screen")})
    )


def test_legacy_conclusion_drops_fact_ids_and_snapshot_rounds():
    report = {
        "evidence_review": "passed",
        "analysis": {
            "counterevidence": {
                "text": "推论（F2、F6）：最强反证是现金流为负（F4、F5）。其后不显示。",
                "facts": ["F4"],
            }
        },
    }
    assert p.conclusion_text(report) == "主要风险：现金流为负。"
    text = p.snapshot_text({"date": "2026-10-16", "close": 33.72, "pe_ttm": 20.4019, "pb": 3.2889})
    assert text == "收盘 33.72 元 · 市盈率(TTM) 20.4 倍 · 市净率 3.29 倍（2026-10-16）"


def test_malformed_receiver_sections_are_treated_as_absent():
    report = {
        "code": "600001.SH",
        "evidence_review": "passed",
        "status": "scoped_complete",
        "analysis": {
            "summary": "bad",
            "business": {"text": "业务。", "facts": ["F1"]},
            "financial": {"text": 1, "facts": []},
        },
    }
    assert p.conclusion_text(report) == ""
    view = p.report_view(report)
    assert view["summary"] is None and [s["title"] for s in view["sections"]] == ["业务"]
    assert p.conclusion_text(report | {"analysis": "bad"}) == ""
