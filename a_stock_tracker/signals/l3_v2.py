"""Core data types and signal-computation logic for L3 v2 (no-tech-gate).

This module is the single source of truth for the v2 candidate signal.
Both the offline backtest script and the production pipeline import from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

V2_VERSION = "v2.1-no-tech-gate"
V2_OVERSOLD_VERSION = "v2.2-oversold"
FREEFALL_THRESHOLD = 0.65
OVERSOLD_UPPER_THRESHOLD = 0.95
OVERSOLD_VOLUME_RATIO = 0.90


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None
    source: str
    adjusted: str
    volume_unit: str
    fetched_at: str | None
    quality_status: str | None


@dataclass(frozen=True)
class PricePanel:
    code: str
    adjusted: str
    bars: tuple[DailyBar, ...]
    source: str
    volume_unit: str
    preload_start: date
    stale_reason: str | None
    limitation: str | None


@dataclass(frozen=True)
class DataContractState:
    adjusted: str
    source: str
    volume_unit: str
    is_stale: bool
    stale_reason: str | None
    unavailable_reason: str | None
    alignment_reason: str | None


@dataclass(frozen=True)
class SignalResult:
    signal: int | None
    version: str
    status: str
    reason: str
    metrics: dict[str, float | str | None]


def empty_metrics() -> dict[str, float | str | None]:
    return {
        "close": None,
        "ma60": None,
        "ma120": None,
        "ma60_5d_ago": None,
        "vol5": None,
        "vol20": None,
        "high_20d_prev": None,
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def compute_l3_v2_candidate(
    price_panel: PricePanel | None,
    data_contract: DataContractState,
) -> SignalResult:
    """L3 v2 no-tech-gate: only FREEFALL (close < MA120×0.65) rejects."""
    if price_panel is None:
        return SignalResult(
            None,
            V2_VERSION,
            "unavailable",
            data_contract.unavailable_reason or "PRICE_PANEL_UNAVAILABLE",
            empty_metrics(),
        )
    bars = price_panel.bars
    if len(bars) < 120:
        return SignalResult(None, V2_VERSION, "unavailable", "INSUFFICIENT_WINDOW", empty_metrics())
    if data_contract.is_stale:
        return SignalResult(
            None, V2_VERSION, "unavailable", data_contract.stale_reason or "SOURCE_STALE", empty_metrics()
        )

    closes = [bar.close for bar in bars]
    latest_close = closes[-1]
    ma60 = _mean(closes[-60:])
    ma120 = _mean(closes[-120:])
    metrics: dict[str, float | str | None] = {
        "close": latest_close,
        "ma60": ma60,
        "ma120": ma120,
    }

    if latest_close < ma120 * FREEFALL_THRESHOLD:
        return SignalResult(0, V2_VERSION, "reject", "FREEFALL", metrics)

    qfq_ok = (
        data_contract.adjusted == "qfq" and not data_contract.unavailable_reason and not data_contract.alignment_reason
    )
    if qfq_ok:
        return SignalResult(1, V2_VERSION, "pass_strong", "PASS_STRONG", metrics)
    return SignalResult(1, V2_VERSION, "pass_weak", "QFQ_UNAVAILABLE", metrics)


def compute_l3_v2_oversold(
    price_panel: PricePanel | None,
    data_contract: DataContractState,
) -> SignalResult:
    """Oversold zone filter (方案1): pass only when close in MA120×[0.65, 0.95] and volume shrinking."""
    if price_panel is None:
        return SignalResult(
            None,
            V2_OVERSOLD_VERSION,
            "unavailable",
            data_contract.unavailable_reason or "PRICE_PANEL_UNAVAILABLE",
            empty_metrics(),
        )
    bars = price_panel.bars
    if len(bars) < 120:
        return SignalResult(None, V2_OVERSOLD_VERSION, "unavailable", "INSUFFICIENT_WINDOW", empty_metrics())
    if data_contract.is_stale:
        return SignalResult(
            None, V2_OVERSOLD_VERSION, "unavailable", data_contract.stale_reason or "SOURCE_STALE", empty_metrics()
        )
    if any(bar.volume is None for bar in bars[-20:]):
        return SignalResult(None, V2_OVERSOLD_VERSION, "unavailable", "MISSING_VOLUME", empty_metrics())

    closes = [bar.close for bar in bars]
    volumes = [float(bar.volume) for bar in bars[-20:]]  # type: ignore[arg-type]
    latest_close = closes[-1]
    ma120 = _mean(closes[-120:])
    vol5 = _mean(volumes[-5:])
    vol20 = _mean(volumes[-20:])
    metrics: dict[str, float | str | None] = {
        "close": latest_close,
        "ma120": ma120,
        "vol5": vol5,
        "vol20": vol20,
    }

    if latest_close < ma120 * FREEFALL_THRESHOLD:
        return SignalResult(0, V2_OVERSOLD_VERSION, "reject", "FREEFALL", metrics)
    if latest_close > ma120 * OVERSOLD_UPPER_THRESHOLD:
        return SignalResult(0, V2_OVERSOLD_VERSION, "reject", "ABOVE_OVERSOLD_ZONE", metrics)
    if vol5 > vol20 * OVERSOLD_VOLUME_RATIO:
        return SignalResult(0, V2_OVERSOLD_VERSION, "reject", "VOLUME_NOT_SHRINKING", metrics)

    qfq_ok = (
        data_contract.adjusted == "qfq" and not data_contract.unavailable_reason and not data_contract.alignment_reason
    )
    if qfq_ok:
        return SignalResult(1, V2_OVERSOLD_VERSION, "pass_strong", "PASS_STRONG", metrics)
    return SignalResult(1, V2_OVERSOLD_VERSION, "pass_weak", "QFQ_UNAVAILABLE", metrics)
