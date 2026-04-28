"""tests/test_sheets_sync.py — sheets_sync 单元测试，全部 mock gspread。"""
from unittest.mock import MagicMock

import gspread
import pytest


# ─── Test 1: holdings 行号公式正确性 ─────────────────────────────────────────

def test_holdings_row_formula(monkeypatch):
    """push_holdings_template 每行 cost_total 公式行号与实际行位置一致。"""
    import sheets_sync

    fake_watchlist = [
        {"code": "600036", "name": "招商银行"},
        {"code": "601288", "name": "农业银行"},
        {"code": "000963", "name": "华东医药"},
    ]
    monkeypatch.setattr(sheets_sync.config, "WATCHLIST", fake_watchlist)

    db_mock = MagicMock()
    db_mock.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(sheets_sync, "get_db", lambda: db_mock)

    ws = MagicMock()
    ws.get_all_values.return_value = []
    sh = MagicMock()
    sh.worksheet.side_effect = gspread.WorksheetNotFound
    sh.add_worksheet.return_value = ws

    sheets_sync.push_holdings_template(sh)

    data = ws.update.call_args[1]["values"]
    # row 0 = header，row 1-3 = 三只股票
    assert data[1][4] == "=C2*D2", "第1只股票 cost_total 公式应引用第2行"
    assert data[2][4] == "=C3*D3", "第2只股票 cost_total 公式应引用第3行"
    assert data[3][4] == "=C4*D4", "第3只股票 cost_total 公式应引用第4行"


# ─── Test 2: accuracy tab 为空时写入公式 ─────────────────────────────────────

def test_init_accuracy_tab_empty():
    """accuracy_report tab 为空时写入 COUNTIFS 公式，使用 USER_ENTERED 模式。"""
    import sheets_sync

    ws = MagicMock()
    ws.get_all_values.return_value = []
    sh = MagicMock()
    sh.worksheet.side_effect = gspread.WorksheetNotFound
    sh.add_worksheet.return_value = ws

    sheets_sync._init_accuracy_formula_tab(sh)

    assert ws.update.call_count == 1
    kwargs = ws.update.call_args[1]
    assert kwargs["value_input_option"] == "USER_ENTERED"

    data = kwargs["values"]
    assert data[0] == sheets_sync._ACC_HEADERS          # header 行正确
    assert data[1][0] == "≥65分"                        # 第一分段标签
    assert data[1][1].startswith("=")                   # 记录数是公式
    assert data[1][2].startswith("=")                   # 命中率是公式
    assert len(data) == 1 + len(sheets_sync._ACC_SEGMENTS)  # header + 5 分段


# ─── Test 3: accuracy tab 非空时跳过（幂等） ─────────────────────────────────

def test_init_accuracy_tab_existing():
    """accuracy_report tab 已有内容时不覆盖。"""
    import sheets_sync

    ws = MagicMock()
    ws.get_all_values.return_value = [["分段", "记录数", "30d命中率(绝对)"]]
    sh = MagicMock()
    sh.worksheet.return_value = ws

    sheets_sync._init_accuracy_formula_tab(sh)

    ws.update.assert_not_called()
