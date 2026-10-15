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
        ("completed", "初筛结果可查看"),
        ("partial", "部分结果 · 初筛结果可查看"),
        ("unknown", "状态待核查"),
    ],
)
def test_recent_labels_distinguish_execution_from_result(status, label):
    run = {"id": "a" * 32, "status": status, "request": {"mode": "screen"}, "summary": {}}
    assert p.recent_result_label(run) == label


def test_recent_financial_labels_depend_on_readable_reports():
    readable = {"status": "completed", "summary": {"reports": [{"evidence_review": "passed"}]}}
    unreadable = {"status": "completed", "summary": {"reports": [{"evidence_review": "failed"}]}}
    assert p.recent_result_label(readable) == "财报可查看"
    assert p.recent_result_label(unreadable) == "无可读财报结论"


def test_price_summary_hides_expired_levels_and_explains_gaps():
    row = demo_board()["rows"][0]
    live = p.price_summary(row)
    assert "收盘 11.20 元" in live and "失效线 11.00 元" in live and "1.79%" in live
    expired = p.price_summary(demo_board("expired")["rows"][0])
    assert "已过期" in expired and "11.00" not in expired and "1.79%" not in expired
    gap = p.price_summary(demo_board("gap")["rows"][0])
    assert gap.startswith("资料缺口：")


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
