"""tests/test_sheets_sync.py — sheets_sync 单元测试，全部 mock gspread。"""
from unittest.mock import MagicMock

import pytest
gspread = pytest.importorskip("gspread")


# ─── Test 1: accuracy tab 为空时写入公式 ─────────────────────────────────────

def test_init_accuracy_tab_empty():
    """accuracy_report tab 为空时写入 COUNTIFS 公式，使用 USER_ENTERED 模式。
    header 行末尾附带 schema hash；数据行从第二行起。
    """
    import sheets_sync

    ws = MagicMock()
    ws.get_all_values.return_value = []
    sh = MagicMock()
    sh.worksheet.side_effect = gspread.WorksheetNotFound
    sh.add_worksheet.return_value = ws

    sheets_sync._init_accuracy_formula_tab(sh)

    assert ws.update.call_count == 1
    kwargs = ws.update.call_args[1]
    assert kwargs["value_input_option"] == sheets_sync.ValueInputOption.user_entered

    data = kwargs["values"]
    # header 行 = _ACC_HEADERS + [schema_hash]
    assert data[0][: len(sheets_sync._ACC_HEADERS)] == sheets_sync._ACC_HEADERS
    assert data[0][len(sheets_sync._ACC_HEADERS)] == sheets_sync._acc_schema_hash()
    # 第一分段标签与 _ACC_SEGMENTS 一致
    assert data[1][0] == sheets_sync._ACC_SEGMENTS[0][0]
    assert data[1][1].startswith("=")   # 记录数是公式
    assert data[1][2].startswith("=")   # 命中率是公式
    assert len(data) == 1 + len(sheets_sync._ACC_SEGMENTS)  # header + 5 分段
    # 每行有 11 列（_ACC_HEADERS 宽度）
    assert len(data[1]) == len(sheets_sync._ACC_HEADERS)


# ─── Test 2: accuracy tab schema 未变时跳过（幂等） ──────────────────────────

def test_init_accuracy_tab_existing_same_schema():
    """accuracy_report tab schema hash 未变时不覆盖。"""
    import sheets_sync

    ws = MagicMock()
    # 首行末尾带有当前 hash
    valid_header = sheets_sync._ACC_HEADERS + [sheets_sync._acc_schema_hash()]
    ws.get_all_values.return_value = [valid_header, ["≥55分", "=COUNTIFS(...)"]]
    sh = MagicMock()
    sh.worksheet.return_value = ws

    sheets_sync._init_accuracy_formula_tab(sh)

    ws.update.assert_not_called()


# ─── Test 3: accuracy tab schema 变更时重写 ─────────────────────────────────

def test_init_accuracy_tab_schema_changed():
    """accuracy_report tab 存在但 schema hash 与当前不一致时，自动重写。"""
    import sheets_sync

    ws = MagicMock()
    # 首行末尾是旧 hash
    old_header = sheets_sync._ACC_HEADERS + ["oldabc"]
    ws.get_all_values.return_value = [old_header, ["≥65分", "=COUNTIFS(...)"]]
    sh = MagicMock()
    sh.worksheet.return_value = ws

    sheets_sync._init_accuracy_formula_tab(sh)

    assert ws.update.call_count == 1
    kwargs = ws.update.call_args[1]
    data = kwargs["values"]
    # 重写后 header 行包含新 hash
    assert data[0][len(sheets_sync._ACC_HEADERS)] == sheets_sync._acc_schema_hash()


# ─── Test 4: accuracy tab _acc_row 包含 90d 公式 ──────────────────────────

def test_acc_row_includes_90d():
    """_acc_row 应生成 11 列（标签 + 10个公式），包含 90d 命中率和 alpha。"""
    import sheets_sync

    row = sheets_sync._acc_row("≥55分", 55, 200)

    assert len(row) == len(sheets_sync._ACC_HEADERS)
    # 90d 列（索引 8/9/10）应是公式
    assert row[8].startswith("=")   # 90d命中率(绝对)
    assert row[9].startswith("=")   # 90d命中率(vs沪深300)
    assert row[10].startswith("=")  # 90d平均alpha
    # 90d 公式引用 N 列（outcome_90d）和 P 列（alpha_90d）
    assert "!N:N" in row[8]
    assert "!P:P" in row[9]
    assert "!P:P" in row[10]
