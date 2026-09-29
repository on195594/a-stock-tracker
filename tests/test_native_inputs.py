import json
from datetime import date

import pytest

from a_stock_tracker.calendar import latest_completed_day
from a_stock_tracker.config import read_anchors
from a_stock_tracker.research import ScreenError


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
