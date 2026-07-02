"""tests/test_framework_b_report.py — lib/framework_b_report.py 的 ROE 趋势预警单元测试。

覆盖 _has_roe_trend_warning（纯函数）和 _score_framework_b_candidate（确认打分行为不受影响）。
不发起任何网络请求。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib.framework_b_report import _has_roe_trend_warning, _score_framework_b_candidate
from scorer import score_stock

WEIGHTS = {
    "frameworks": {
        "A": {
            "fundamental": {
                "roe_3y_avg": {
                    "max_score": 15,
                    "breakpoints": [[0, 0], [8, 5], [12, 9], [15, 15], [25, 15]],
                    "interpolate": True,
                },
                "net_profit_growth": {
                    "max_score": 10,
                    "breakpoints": [[-20, 0], [0, 2], [8, 6], [15, 10], [30, 10]],
                    "interpolate": True,
                },
                "debt_ratio": {
                    "max_score": 10,
                    "breakpoints": [[30, 10], [50, 7], [65, 3], [80, 0]],
                    "interpolate": True,
                    "invert": True,
                },
                "gross_margin": {
                    "max_score": 10,
                    "breakpoints": [[0, 0], [15, 4], [25, 7], [35, 10], [60, 10]],
                    "interpolate": True,
                },
            },
            "valuation": {
                "pb_percentile_10y": {
                    "max_score": 15,
                    "breakpoints": [[5, 15], [20, 12], [40, 8], [60, 4], [80, 0]],
                    "interpolate": True,
                    "invert": True,
                },
            },
        },
        "B": {
            "fundamental": {
                "roe_3y_avg": {
                    "max_score": 20,
                    "breakpoints": [[0, 0], [8, 7], [12, 12], [15, 20], [25, 20]],
                    "interpolate": True,
                },
                "net_profit_growth": {
                    "max_score": 10,
                    "breakpoints": [[-20, 0], [0, 2], [8, 6], [15, 10], [30, 10]],
                    "interpolate": True,
                },
                "debt_ratio": {
                    "max_score": 10,
                    "breakpoints": [[30, 10], [50, 7], [65, 3], [80, 0]],
                    "interpolate": True,
                    "invert": True,
                },
                "gross_margin": {
                    "max_score": 15,
                    "breakpoints": [[0, 0], [15, 6], [25, 11], [35, 15], [60, 15]],
                    "interpolate": True,
                },
            },
            "valuation": {
                "pb_percentile_10y": {
                    "max_score": 5,
                    "breakpoints": [[5, 5], [20, 4], [40, 3], [60, 1], [80, 0]],
                    "interpolate": True,
                    "invert": True,
                },
            },
        },
    }
}

FULL_DATA = {
    "roe_3y_avg": 16.5,
    "net_profit_growth": 5.5,
    "debt_ratio": 45.0,
    "gross_margin": 56.8,
    "pb_percentile_10y": 18.0,
}


# ---------------------------------------------------------------------------
# _has_roe_trend_warning
# ---------------------------------------------------------------------------

def test_roe_trend_warning_triggers_when_gap_exceeds_threshold():
    """3年均值15%、最新单年8%，差值7pts > 3.0pts 阈值 → True。"""
    data = {**FULL_DATA, "roe_3y_avg": 15.0, "roe_latest": 8.0}
    assert _has_roe_trend_warning(data) is True


def test_roe_trend_warning_does_not_trigger_when_gap_within_threshold():
    """差值恰好等于阈值（不超过）→ False。"""
    data = {**FULL_DATA, "roe_3y_avg": 15.0, "roe_latest": 12.0}
    assert _has_roe_trend_warning(data) is False


def test_roe_trend_warning_false_when_roe_latest_missing():
    """roe_latest 缺失（None）→ False，不抛异常。"""
    data = {**FULL_DATA, "roe_3y_avg": 15.0}
    assert _has_roe_trend_warning(data) is False


def test_roe_trend_warning_false_when_roe_3y_avg_missing():
    """roe_3y_avg 缺失（None）→ False，不抛异常。"""
    data = {**FULL_DATA, "roe_3y_avg": None, "roe_latest": 8.0}
    assert _has_roe_trend_warning(data) is False


# ---------------------------------------------------------------------------
# _score_framework_b_candidate：确认打分行为不受影响
# ---------------------------------------------------------------------------

def test_score_framework_b_candidate_flags_warning_without_changing_scores():
    """触发预警时 roe_trend_warning=True，但 score_a/score_b 数值跟不加这个判断时完全一致。"""
    data = {**FULL_DATA, "roe_3y_avg": 15.0, "roe_latest": 8.0}

    result = _score_framework_b_candidate("600036", "招商银行", "银行", data, WEIGHTS)

    assert result["roe_trend_warning"] is True

    expected_a = score_stock("600036", "A", data, weights=WEIGHTS)
    expected_b = score_stock("600036", "B", data, weights=WEIGHTS, enforce_supported=False)
    assert result["score_a"]["total_score"] == pytest.approx(expected_a["total_score"])
    assert result["score_b"]["total_score"] == pytest.approx(expected_b["total_score"])
    # 打分用的仍是原始 data 里的 roe_3y_avg=15.0，不是被替换成 roe_latest=8.0 的保守值
    assert result["score_b"]["component_scores"]["roe_3y_avg"] == pytest.approx(20.0)


def test_score_framework_b_candidate_no_warning_when_trend_stable():
    """ROE 无显著下滑时 roe_trend_warning=False。"""
    data = {**FULL_DATA, "roe_3y_avg": 15.0, "roe_latest": 14.5}

    result = _score_framework_b_candidate("600036", "招商银行", "银行", data, WEIGHTS)

    assert result["roe_trend_warning"] is False
