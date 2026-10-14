"""Synthetic window-index hierarchy, status and contrast regressions."""

import flet as ft
import pytest

from a_stock_tracker import opportunity_view as ui
from a_stock_tracker.opportunity_demo import demo_board
from tests.test_app import app_controls


def text_values(view):
    return [c.value or "" for c in app_controls(view) if isinstance(c, ft.Text)]


def test_detail_exposes_deterministic_facts_without_model_or_old_trigger():
    board = demo_board()
    board["created_at"] = "2026-09-28T16:30:00+08:00"
    texts = text_values(ui.opportunity_view(board, "600001.SH", lambda _: None, None))
    row = board["rows"][0]
    assert "量价事实" in texts and "观察线与失效线" in texts
    for key in ("trend", "relative", "condition", "invalidation", "distance"):
        assert row["facts"][key] in texts
    assert any(row["as_of"] in t and row["expires"] in t for t in texts)
    assert any(board["source"] in t and "2026-09-28 16:30" in t for t in texts)
    assert all(
        word not in "\n".join(texts)
        for word in ("11.45", "15000", "有效观察", "等待条件核对", "AI复核")
    )


def test_list_summary_has_close_stop_distance_and_dates():
    board = demo_board()
    texts = text_values(ui.opportunity_view(board, None, lambda _: None, None))
    assert "价格纪律参考" in texts
    summary = ui.price_summary(board["rows"][0])
    assert summary in texts
    assert "收盘 11.20 元" in summary and "失效线 11.00 元" in summary
    assert "1.79%" in summary and "后续5个交易日" in summary


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("status", ["gap", "expired"])
def test_unavailable_references_never_render_actionable_conditions(status, legacy):
    from tests.test_opportunities import legacy_board

    board = legacy_board() if legacy else demo_board()
    row = board["rows"][0]
    if status == "expired":
        row["expired"] = True
        label = "已过期"
    else:
        row.update(status=status, reasons=["合成核查缺口"])
        label = "资料核验失败"
    view = ui.opportunity_view(board, row["code"], lambda _: None, None)
    text = "\n".join(text_values(view))
    assert label in text
    if legacy:
        assert "旧规则批次" in text
    assert row["facts"]["condition"] not in text
    assert row["facts"]["invalidation"] not in text
    assert "11.23" not in text and "11.00" not in text


def test_missing_batch_zero_references_and_unknown_company_stay_distinct():
    missing = "\n".join(text_values(ui.opportunity_view(None, None, lambda _: None, None)))
    assert "尚未配置或发布批次" in missing
    board = demo_board("zero")
    empty = "\n".join(text_values(ui.opportunity_view(board, None, lambda _: None, None)))
    assert "0 项价格参考 · 0 家显式股票池" in empty
    assert "没有价格参考" in empty and "尚未配置" not in empty
    unknown = ui.opportunity_view(demo_board(), "600002.SH", lambda _: None, None)
    assert "本批次没有该公司的价格参考记录。" in text_values(unknown)
    assert not any(isinstance(c, ft.Button) for c in app_controls(unknown))


@pytest.mark.parametrize("status", ["excluded", "gap", "expired", "empty"])
def test_no_reference_heading_does_not_invent_a_data_gap(status):
    from tests.test_opportunities import legacy_board

    board = legacy_board()
    row = board["rows"][0]
    if status == "empty":
        board["rows"] = []
        expected = "0 项价格参考 · 0 家显式股票池"
    elif status == "expired":
        row["expired"] = True
        expected = "已过期 · 仅供历史核查"
    else:
        row.update(
            status=status, reasons=["合成排除原因" if status == "excluded" else "合成资料缺口"]
        )
        expected = "未入选" if status == "excluded" else "资料核验失败"
    texts = text_values(ui.opportunity_view(board, None, lambda _: None, None))
    assert "本批次没有价格参考。" in texts
    assert "本批次没有价格参考，请核查资料缺口。" not in texts
    assert expected in texts
    if status in {"excluded", "gap"}:
        assert row["reasons"][0] in texts
    if status == "gap":
        assert "下一步：核对并补齐资料后，再明确确认新的研究。" in texts


def test_long_identity_wraps_without_truncation_and_actions_have_touch_height():
    board = demo_board()
    board["rows"][0]["name"] = "合成的超长公司全称用于核查移动页面换行而非省略" * 2
    view = ui.opportunity_view(board, None, lambda _: None, None)
    texts = [c for c in app_controls(view) if isinstance(c, ft.Text)]
    name = next(c for c in texts if c.value == board["rows"][0]["name"])
    assert name.max_lines is None and name.no_wrap is not True
    assert "600001.SH" in text_values(view)
    action_text = next(c for c in texts if c.value == "查看价格参考")
    assert action_text.semantics_label == "查看价格参考 · " + name.value
    actions = [c for c in app_controls(view) if isinstance(c, (ft.Button, ft.TextButton))]
    assert actions and all(c.height >= 48 for c in actions)


@pytest.mark.parametrize(
    "foreground,background",
    [
        (ui.INK, ui.BACKGROUND),
        (ui.MUTED, ui.BAND),
        (ui.ACCENT, ui.SURFACE),
        (ui.SURFACE, ui.ACCENT),
        (ui.CAUTION, ui.CAUTION_BG),
        (ui.INK, ui.CAUTION_BG),
        (ui.ERROR, ui.BACKGROUND),
    ],
)
def test_semantic_text_colors_meet_normal_text_contrast(foreground, background):
    def luminance(color):
        channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * w for c, w in zip(linear, [0.2126, 0.7152, 0.0722], strict=True))

    bright, dark = sorted([luminance(foreground), luminance(background)], reverse=True)
    assert (bright + 0.05) / (dark + 0.05) >= 4.5
    theme = ui.window_theme()
    assert theme.font_family == "NotoSansSC"
    assert theme.color_scheme.primary == ui.ACCENT
    assert theme.button_theme.style.bgcolor[ft.ControlState.DISABLED] == ui.BAND
    assert theme.button_theme.style.color[ft.ControlState.DISABLED] == ui.MUTED


def test_common_disclaimer_is_only_rendered_once_in_detail():
    view = ui.opportunity_view(demo_board(), "600001.SH", lambda _: None, None)
    assert sum(ui.LIMITS in text for text in text_values(view)) == 1


@pytest.mark.parametrize("detail", [True, False])
@pytest.mark.parametrize("legacy", [True, False])
def test_expired_label_once_and_recheck_is_only_navigation(detail, legacy):
    from tests.test_opportunities import legacy_board

    board = legacy_board() if legacy else demo_board()
    row = board["rows"][0]
    row["expired"] = True
    visited = []
    view = ui.opportunity_view(
        board,
        row["code"] if detail else None,
        lambda _: None,
        None,
        recheck_route=lambda route: lambda _: visited.append(route),
    )
    texts = text_values(view)
    assert texts.count("已过期 · 仅供历史核查") == 1
    assert row["facts"]["condition"] not in texts
    assert row["facts"]["invalidation"] not in texts
    assert row["facts"]["distance"] not in texts
    if detail:
        assert any(row["facts"]["trend"] in text for text in texts)
    action = next(
        c
        for c in app_controls(view)
        if isinstance(c, ft.TextButton) and c.content == "重新核对价格参考"
    )
    assert not visited and action.height == 48
    action.on_click(None)
    assert visited == ["/analysis?mode=swing&code=" + row["code"]]


def test_detail_does_not_duplicate_core_price_facts():
    board = demo_board()
    text = "\n".join(text_values(ui.opportunity_view(board, "600001.SH", lambda _: None, None)))
    assert text.count("11.20") == 1
    assert text.count("11.00") == 1
    assert text.count("1.79%") == 1
    assert text.count("11.23") == 1
