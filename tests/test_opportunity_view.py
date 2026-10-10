"""Synthetic window-index hierarchy, status and contrast regressions."""

from copy import deepcopy

import flet as ft
import pytest

from a_stock_tracker import opportunity_view as ui
from a_stock_tracker.opportunity_demo import demo_board
from tests.test_app import app_controls


def text_values(view):
    return [c.value or "" for c in app_controls(view) if isinstance(c, ft.Text)]


def test_detail_keeps_counter_before_program_conditions_and_full_sources():
    board = demo_board()
    board["created_at"] = "2026-09-28T16:30:00+08:00"
    board["rows"][0]["model"] = {"provider": "offline", "model": "synthetic-only"}
    view = ui.opportunity_view(board, "600001.SH", lambda _: None, None)
    texts = text_values(view)
    row = board["rows"][0]
    assert texts.index("最强反对理由") < texts.index("程序价格条件")
    assert texts.index("什么时候放弃") < texts.index(row["facts"]["invalidation"])
    assert row["facts"]["condition"] in texts
    assert f"依据截至 {row['as_of']}" in texts
    assert f"观察有效至 {row['expires']}" in texts
    assert any(board["source"] in s and board["created_at"] in s for s in texts)
    tile = next(c for c in app_controls(view) if isinstance(c, ft.ExpansionTile))
    facts = [c for c in tile.controls if isinstance(c, ft.Semantics)]
    assert facts and all(c.read_only and c.exclude_semantics for c in facts)
    assert all(c.content.selectable and c.label == c.content.value for c in facts)
    assert "为什么观察依据：trend、relative" in texts
    assert "解释与复核模型：offline / synthetic-only" in texts
    headings = [
        c for c in app_controls(view) if isinstance(c, ft.Semantics) and c.heading_level is not None
    ]
    levels = {c.content.value: c.heading_level for c in headings}
    assert levels[row["name"]] == 1
    assert levels["为什么观察"] == 2
    assert levels["最强反对理由"] == 2
    assert levels["还要等什么"] == 2
    assert levels["程序价格条件"] == 2


def test_list_hierarchy_and_avoids_unheaded_price_conditions():
    board = demo_board()
    view = ui.opportunity_view(board, None, lambda _: None, None)
    texts = text_values(view)
    row = board["rows"][0]
    assert row["facts"]["condition"] not in texts
    headings = [
        c for c in app_controls(view) if isinstance(c, ft.Semantics) and c.heading_level is not None
    ]
    levels = {c.content.value: c.heading_level for c in headings}
    assert levels["波段机会"] == 1
    assert levels[row["name"]] == 2
    assert levels["为什么观察"] == 3
    assert levels["最强反对理由"] == 3
    assert levels["还要等什么"] == 3


@pytest.mark.parametrize("status", ["excluded", "gap", "expired"])
def test_unavailable_observations_never_render_actionable_conditions(status):
    board = deepcopy(demo_board())
    row = board["rows"][0]
    if status == "expired":
        row["expired"] = True
        label = "观察已过期"
    else:
        row.update(status=status, reasons=["合成核查缺口"])
        label = "未入选" if status == "excluded" else "资料或解读核验失败"
    view = ui.opportunity_view(board, row["code"], lambda _: None, None)
    text = "\n".join(text_values(view))
    assert label in text
    assert row["facts"]["condition"] not in text
    assert row["facts"]["invalidation"] not in text
    assert "11.23" not in text and "11.00" not in text


def test_missing_batch_zero_observations_and_unknown_company_stay_distinct():
    missing = "\n".join(text_values(ui.opportunity_view(None, None, lambda _: None, None)))
    assert "尚未配置或发布批次" in missing
    board = demo_board()
    board["rows"] = []
    empty = "\n".join(text_values(ui.opportunity_view(board, None, lambda _: None, None)))
    assert "0 项有效观察 · 0 家显式股票池" in empty
    assert "没有有效观察候选" in empty and "尚未配置" not in empty
    unknown = ui.opportunity_view(demo_board(), "600002.SH", lambda _: None, None)
    assert "本批次没有该公司的观察记录。" in text_values(unknown)
    assert not any(isinstance(c, ft.Button) for c in app_controls(unknown))


def test_long_identity_wraps_without_truncation_and_actions_have_touch_height():
    board = demo_board()
    board["rows"][0]["name"] = "合成的超长公司全称用于核查移动页面换行而非省略" * 2
    view = ui.opportunity_view(board, None, lambda _: None, None)
    texts = [c for c in app_controls(view) if isinstance(c, ft.Text)]
    name = next(c for c in texts if c.value == board["rows"][0]["name"])
    assert name.max_lines is None and name.no_wrap is not True
    assert "600001.SH" in text_values(view)
    action_text = next(c for c in texts if c.value == "查看条件与反证")
    assert action_text.semantics_label == "查看条件与反证 · " + name.value
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
