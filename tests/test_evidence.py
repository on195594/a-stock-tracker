"""Financial selection for the independent batch; synthetic records only."""

import pytest

from a_stock_tracker.evidence import REPORT_FIELDS, select_latest_report


def report_row(**changes):
    return {
        "end_date": "20260630",
        "ann_date": "20260820",
        "update_flag": "0",
        "source": "tushare.fina_indicator",
        "acquired_at": "2026-09-20T16:00:00+08:00",
        **{field: 2.0 for field in REPORT_FIELDS},
        **changes,
    }


def test_latest_interim_report_selection_and_missing_values():
    annual = report_row(end_date="20251231", ann_date="20260420")
    latest = select_latest_report([annual, report_row()], "2026-09-20", "2026-09-21")
    assert latest["period"] == "2026-06-30" and latest["status"] == "ok"
    incomplete = select_latest_report([annual, report_row(ocfps=None)], "2026-09-20", "2026-09-21")
    assert incomplete["status"] == "partial" and incomplete["metrics"]["ocfps"] is None
    assert incomplete["period"] == "2026-06-30"


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf")])
def test_invalid_metric_never_becomes_a_fact(bad):
    selected = select_latest_report([report_row(debt_to_assets=bad)], "2026-09-20", "2026-09-21")
    assert selected["status"] == "partial" and selected["metrics"]["debt_to_assets"] is None


def test_report_versions_and_dates():
    original = report_row()
    conflicting = report_row(ocfps=-2)
    assert (
        select_latest_report([original, conflicting], "2026-09-20", "2026-09-21")["status"]
        == "conflict"
    )
    revised = select_latest_report(
        [original, {**conflicting, "update_flag": "1"}], "2026-09-20", "2026-09-21"
    )
    assert revised["metrics"]["ocfps"] == -2 and revised["status"] == "ok"
    assert (
        select_latest_report([report_row(ann_date="20260601")], "2026-09-20", "2026-09-21")[
            "status"
        ]
        == "conflict"
    )
    assert (
        select_latest_report([report_row(ann_date="20260922")], "2026-09-20", "2026-09-21")[
            "status"
        ]
        == "missing"
    )
