import json
from datetime import date
from pathlib import Path

import pytest

from a_stock_tracker.calendar import latest_completed_day
from a_stock_tracker.config import read_anchors
from a_stock_tracker.research import ScreenError


def test_reference_list_includes_new_main_board_industries():
    anchors = read_anchors(Path(__file__).parents[1] / "config/anchors.json")
    by_code = {item["code"]: item for item in anchors}
    for code, name in {
        "600519": "贵州茅台",
        "000333": "美的集团",
        "600660": "福耀玻璃",
        "600585": "海螺水泥",
        "600276": "恒瑞医药",
    }.items():
        assert by_code[code]["name"] == name
        assert "discovery_unavailable" not in by_code[code]
    assert by_code["601288"]["discovery_unavailable"]


def test_native_reference_list_never_executes_python_or_accepts_duplicates(tmp_path):
    path = tmp_path / "anchors.json"
    path.write_text('[{"code":"600900", "name":"参照"}]')
    assert read_anchors(path) == [{"code": "600900", "ts_code": "600900.SH", "name": "参照"}]
    path.write_text('[{"code":"600900", "name":"甲"},{"code":"600900.SH", "name":"乙"}]')
    with pytest.raises(ScreenError):
        read_anchors(path)
    path.write_text('raise RuntimeError("must never execute")')
    with pytest.raises(ScreenError):
        read_anchors(path)


def test_calendar_rejects_future_duplicate_and_unproved_dates(tmp_path):
    path = tmp_path / "calendar.json"
    proof = {
        "source": "tushare.trade_cal:SSE;synthetic",
        "covered_from": "2026-09-01",
        "covered_to": "2026-09-25",
        "as_of": "2026-09-25",
        "dates": ["2026-09-24"],
    }
    path.write_text(json.dumps(proof))
    assert latest_completed_day(path, today=date(2026, 9, 28)) == "2026-09-24"
    for changed in (
        {"dates": ["2026-09-24", "2026-09-24"]},
        {"dates": ["2026-09-26"]},
        {"as_of": "2027-01-01"},
        {"source": "unverified"},
    ):
        path.write_text(json.dumps({**proof, **changed}))
        with pytest.raises(ValueError):
            latest_completed_day(path, today=date(2026, 9, 28))
