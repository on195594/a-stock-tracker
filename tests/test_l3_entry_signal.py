from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from lib.entry_signal import ENTRY_SIGNAL_VERSION, compute_entry_signal  # noqa: E402


def _bars(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    if volumes is None:
        volumes = [100.0] * len(closes)
    start = date(2026, 1, 1) - timedelta(days=len(closes))
    return pd.DataFrame(
        {
            "date": [(start + timedelta(days=i)).isoformat() for i in range(len(closes))],
            "close": closes,
            "volume": volumes,
        }
    )


def test_compute_entry_signal_passes_when_price_and_volume_rules_all_pass() -> None:
    result = compute_entry_signal(
        _bars([100.0] * 119 + [130.0], [100.0] * 115 + [300.0] * 5)
    )

    assert result.signal == 1
    assert result.version == ENTRY_SIGNAL_VERSION
    assert result.reason == "PASS"


def test_compute_entry_signal_rejects_when_latest_close_below_ma60() -> None:
    result = compute_entry_signal(
        _bars([130.0] * 119 + [90.0], [100.0] * 115 + [300.0] * 5)
    )

    assert result.signal == 0
    assert result.version == ENTRY_SIGNAL_VERSION
    assert result.reason == "BELOW_MA60"


def test_compute_entry_signal_rejects_when_latest_close_passes_ma60_but_not_ma120() -> None:
    result = compute_entry_signal(
        _bars([200.0] * 60 + [100.0] * 59 + [120.0], [100.0] * 115 + [300.0] * 5)
    )

    assert result.signal == 0
    assert result.version == ENTRY_SIGNAL_VERSION
    assert result.reason == "BELOW_MA120"


def test_compute_entry_signal_rejects_when_volume_5d_average_not_above_20d_average() -> None:
    result = compute_entry_signal(
        _bars([100.0] * 119 + [130.0], [300.0] * 115 + [100.0] * 5)
    )

    assert result.signal == 0
    assert result.version == ENTRY_SIGNAL_VERSION
    assert result.reason == "LOW_VOLUME"


def test_compute_entry_signal_returns_null_when_window_is_insufficient() -> None:
    result = compute_entry_signal(_bars([100.0] * 119, [100.0] * 119))

    assert result.signal is None
    assert result.version == ENTRY_SIGNAL_VERSION
    assert result.reason == "INSUFFICIENT_DATA"


def test_compute_entry_signal_returns_null_when_required_columns_are_missing() -> None:
    result = compute_entry_signal(pd.DataFrame([{"date": "2026-01-01", "close": 100.0}]))

    assert result.signal is None
    assert result.version == ENTRY_SIGNAL_VERSION
    assert result.reason == "MISSING_COLUMNS"
