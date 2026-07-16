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
    V2_OVERSOLD_VERSION,
    V2_VERSION,
    DataContractState,
    DailyBar,
    PricePanel,
    compute_l3_v2_candidate,
    compute_l3_v2_oversold,
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
    # ma120 ≈ 99.667 (= (119*100 + 60)/120); threshold ≈ 64.78; close=60 < threshold
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
            date=b.date,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=1.0,
            source=b.source,
            adjusted=b.adjusted,
            volume_unit=b.volume_unit,
            fetched_at=b.fetched_at,
            quality_status=b.quality_status,
        )
    panel = PricePanel(
        code="000001",
        adjusted="qfq",
        bars=tuple(bars),
        source="baostock",
        volume_unit="lot",
        preload_start=date(2025, 1, 1),
        stale_reason=None,
        limitation=None,
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


def test_v2_freefall_boundary_exact_equal_is_not_freefall() -> None:
    # close == MA120 * FREEFALL_THRESHOLD should NOT be rejected (strict <, not <=)
    # Solve: c = ((119*100 + c)/120) * FREEFALL_THRESHOLD  =>  c = 11900*T/(120-T)
    boundary = 11900.0 * FREEFALL_THRESHOLD / (120.0 - FREEFALL_THRESHOLD)
    closes = [100.0] * 119 + [boundary]
    panel = _make_panel(closes)
    result = compute_l3_v2_candidate(panel, _qfq_contract())

    assert result.signal == 1
    assert result.status == "pass_strong"


# ---------------------------------------------------------------------------
# compute_l3_v2_oversold — basic coverage
# ---------------------------------------------------------------------------


def _make_oversold_panel(
    last_close: float = 85.0,
    vol_early: float = 100.0,
    vol_last5: float = 50.0,
) -> PricePanel:
    """115 early bars (vol=vol_early, close=100) + 5 recent bars (vol=vol_last5, close=last_close)."""
    early_bars = [_make_bar(100.0) for _ in range(115)]
    for i, b in enumerate(early_bars):
        early_bars[i] = DailyBar(
            date=b.date,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=vol_early,
            source=b.source,
            adjusted=b.adjusted,
            volume_unit=b.volume_unit,
            fetched_at=b.fetched_at,
            quality_status=b.quality_status,
        )
    last_bars = []
    for _ in range(5):
        b = _make_bar(last_close)
        last_bars.append(
            DailyBar(
                date=b.date,
                open=b.open,
                high=b.high,
                low=b.low,
                close=last_close,
                volume=vol_last5,
                source=b.source,
                adjusted=b.adjusted,
                volume_unit=b.volume_unit,
                fetched_at=b.fetched_at,
                quality_status=b.quality_status,
            )
        )
    return PricePanel(
        code="000001",
        adjusted="qfq",
        bars=tuple(early_bars + last_bars),
        source="baostock",
        volume_unit="lot",
        preload_start=date(2025, 1, 1),
        stale_reason=None,
        limitation=None,
    )


def test_oversold_version_constant() -> None:
    assert V2_OVERSOLD_VERSION == "v2.2-oversold"


def test_oversold_unavailable_when_insufficient_bars() -> None:
    panel = _make_panel([80.0] * 119)
    result = compute_l3_v2_oversold(panel, _qfq_contract())

    assert result.signal is None
    assert result.version == V2_OVERSOLD_VERSION
    assert result.reason == "INSUFFICIENT_WINDOW"


def test_oversold_rejects_freefall() -> None:
    # close=60 is well below MA120*0.65; checked before zone filter
    panel = _make_oversold_panel(last_close=60.0, vol_early=100.0, vol_last5=50.0)
    result = compute_l3_v2_oversold(panel, _qfq_contract())

    assert result.signal == 0
    assert result.version == V2_OVERSOLD_VERSION
    assert result.reason == "FREEFALL"


def test_oversold_rejects_above_zone() -> None:
    # close=97: MA120≈(119*100+97)/120≈99.975; OVERSOLD_UPPER=0.95 → threshold≈94.98; 97>94.98
    panel = _make_oversold_panel(last_close=97.0, vol_early=100.0, vol_last5=50.0)
    result = compute_l3_v2_oversold(panel, _qfq_contract())

    assert result.signal == 0
    assert result.version == V2_OVERSOLD_VERSION
    assert result.reason == "ABOVE_OVERSOLD_ZONE"


def test_oversold_rejects_volume_not_shrinking() -> None:
    # close=85 is in zone; vol5=100, vol20=100 → 100 > 100*0.90=90 → not shrinking
    panel = _make_oversold_panel(last_close=85.0, vol_early=100.0, vol_last5=100.0)
    result = compute_l3_v2_oversold(panel, _qfq_contract())

    assert result.signal == 0
    assert result.version == V2_OVERSOLD_VERSION
    assert result.reason == "VOLUME_NOT_SHRINKING"


def test_oversold_pass_strong_in_zone_with_shrinking_volume() -> None:
    # close=85 in zone; vol_last5=50 < vol20*0.90 → shrinking; QFQ available
    # vol20 = (115*100 + 5*50)/120... wait, only last 20 bars matter:
    # last 20 = 15 early (100) + 5 last (50) → vol20=(15*100+5*50)/20=87.5; vol5=50
    # 50 <= 87.5*0.90=78.75 → shrinking ✓
    panel = _make_oversold_panel(last_close=85.0, vol_early=100.0, vol_last5=50.0)
    result = compute_l3_v2_oversold(panel, _qfq_contract())

    assert result.signal == 1
    assert result.version == V2_OVERSOLD_VERSION
    assert result.status == "pass_strong"
    assert result.reason == "PASS_STRONG"


def test_oversold_pass_weak_when_qfq_unavailable() -> None:
    panel = _make_oversold_panel(last_close=85.0, vol_early=100.0, vol_last5=50.0)
    result = compute_l3_v2_oversold(panel, _none_contract())

    assert result.signal == 1
    assert result.version == V2_OVERSOLD_VERSION
    assert result.status == "pass_weak"
    assert result.reason == "QFQ_UNAVAILABLE"
