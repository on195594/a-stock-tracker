from __future__ import annotations

import os
import sys
from datetime import date

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.offline_l3_v2_backtest import (  # noqa: E402
    FREEFALL_THRESHOLD,
    V2_VERSION,
    DataContractState,
    DailyBar,
    MarketState,
    PricePanel,
    compute_l3_v2_candidate,
)


def _make_bar(close: float, date_: date | None = None) -> DailyBar:
    return DailyBar(
        date=date_ or date(2026, 1, 1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
        source="baostock",
        adjusted="qfq",
        volume_unit="lot",
        fetched_at=None,
        quality_status=None,
    )


def _make_panel(closes: list[float]) -> PricePanel:
    bars = tuple(_make_bar(c) for c in closes)
    return PricePanel(
        code="000001",
        adjusted="qfq",
        bars=bars,
        source="baostock",
        volume_unit="lot",
        preload_start=date(2025, 1, 1),
        stale_reason=None,
        limitation=None,
    )


def _qfq_contract() -> DataContractState:
    return DataContractState(
        adjusted="qfq",
        source="baostock",
        volume_unit="lot",
        is_stale=False,
        stale_reason=None,
        unavailable_reason=None,
        alignment_reason=None,
    )


def _none_contract() -> DataContractState:
    return DataContractState(
        adjusted="none",
        source="local",
        volume_unit="lot",
        is_stale=False,
        stale_reason=None,
        unavailable_reason="QFQ_MISSING",
        alignment_reason=None,
    )


def test_v2_version_constant() -> None:
    assert V2_VERSION == "v2.1-no-tech-gate"


def test_v2_freefall_threshold_constant() -> None:
    assert FREEFALL_THRESHOLD == 0.65


def test_v2_unavailable_when_price_panel_is_none() -> None:
    contract = _qfq_contract()
    result = compute_l3_v2_candidate(None, contract)

    assert result.signal is None
    assert result.version == V2_VERSION
    assert result.status == "unavailable"
    assert result.reason == "PRICE_PANEL_UNAVAILABLE"


def test_v2_unavailable_when_insufficient_bars() -> None:
    panel = _make_panel([100.0] * 119)
    result = compute_l3_v2_candidate(panel, _qfq_contract())

    assert result.signal is None
    assert result.status == "unavailable"
    assert result.reason == "INSUFFICIENT_WINDOW"


def test_v2_unavailable_when_data_contract_is_stale() -> None:
    panel = _make_panel([100.0] * 120)
    contract = DataContractState(
        adjusted="qfq",
        source="baostock",
        volume_unit="lot",
        is_stale=True,
        stale_reason="CACHE_EXPIRED",
        unavailable_reason=None,
        alignment_reason=None,
    )
    result = compute_l3_v2_candidate(panel, contract)

    assert result.signal is None
    assert result.status == "unavailable"
    assert result.reason == "CACHE_EXPIRED"


def test_v2_rejects_freefall() -> None:
    # ma120 = 100.0; latest_close must be < 100.0 * 0.65 = 65.0
    closes = [100.0] * 119 + [60.0]
    panel = _make_panel(closes)
    result = compute_l3_v2_candidate(panel, _qfq_contract())

    assert result.signal == 0
    assert result.version == V2_VERSION
    assert result.status == "reject"
    assert result.reason == "FREEFALL"


def test_v2_pass_strong_when_qfq_available_and_not_freefall() -> None:
    # All 100.0 → ma120=100.0; latest_close=100.0 ≥ 65.0 (not freefall)
    closes = [100.0] * 120
    panel = _make_panel(closes)
    result = compute_l3_v2_candidate(panel, _qfq_contract())

    assert result.signal == 1
    assert result.version == V2_VERSION
    assert result.status == "pass_strong"
    assert result.reason == "PASS_STRONG"


def test_v2_pass_strong_ignores_ma60_position() -> None:
    # Construct: latest_close < MA60 (old gate would reject BELOW_MA60)
    # but latest_close >= MA120 * 0.65 (no freefall, new gate allows it)
    # closes: [200]*60 + [130]*59 + [120]
    # MA120 ≈ 164.9 → freefall threshold ≈ 107.2; close=120 is above it
    # MA60 ≈ 129.8; close=120 < MA60 → old logic rejects, new logic passes
    closes = [200.0] * 60 + [130.0] * 59 + [120.0]
    panel = _make_panel(closes)
    result = compute_l3_v2_candidate(panel, _qfq_contract())

    assert result.signal == 1
    assert result.status == "pass_strong"


def test_v2_pass_strong_ignores_volume_ratio() -> None:
    # vol5 < vol20: old logic would reject LOW_VOLUME; new logic ignores it
    closes = [100.0] * 120
    bars = list(_make_bar(c) for c in closes)
    # override last 5 bars with low volume (vol5 << vol20)
    for i in range(-5, 0):
        b = bars[i]
        bars[i] = DailyBar(
            date=b.date, open=b.open, high=b.high, low=b.low, close=b.close,
            volume=1.0,
            source=b.source, adjusted=b.adjusted, volume_unit=b.volume_unit,
            fetched_at=b.fetched_at, quality_status=b.quality_status,
        )
    panel = PricePanel(
        code="000001", adjusted="qfq", bars=tuple(bars), source="baostock",
        volume_unit="lot", preload_start=date(2025, 1, 1), stale_reason=None, limitation=None,
    )
    result = compute_l3_v2_candidate(panel, _qfq_contract())

    assert result.signal == 1
    assert result.status == "pass_strong"


def test_v2_pass_weak_when_qfq_unavailable() -> None:
    closes = [100.0] * 120
    panel = _make_panel(closes)
    result = compute_l3_v2_candidate(panel, _none_contract())

    assert result.signal == 1
    assert result.version == V2_VERSION
    assert result.status == "pass_weak"
    assert result.reason == "QFQ_UNAVAILABLE"


def test_v2_pass_weak_when_alignment_reason_present() -> None:
    closes = [100.0] * 120
    panel = _make_panel(closes)
    contract = DataContractState(
        adjusted="qfq",
        source="baostock",
        volume_unit="lot",
        is_stale=False,
        stale_reason=None,
        unavailable_reason=None,
        alignment_reason="DATE_MISMATCH",
    )
    result = compute_l3_v2_candidate(panel, contract)

    assert result.signal == 1
    assert result.status == "pass_weak"


def test_v2_metrics_contain_close_ma60_ma120() -> None:
    closes = [100.0] * 120
    panel = _make_panel(closes)
    result = compute_l3_v2_candidate(panel, _qfq_contract())

    assert result.metrics["close"] == pytest.approx(100.0)
    assert result.metrics["ma60"] == pytest.approx(100.0)
    assert result.metrics["ma120"] == pytest.approx(100.0)
