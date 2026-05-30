from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ENTRY_SIGNAL_VERSION = "v1"

REASON_PASS = "PASS"
REASON_BELOW_MA60 = "BELOW_MA60"
REASON_BELOW_MA120 = "BELOW_MA120"
REASON_LOW_VOLUME = "LOW_VOLUME"
REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
REASON_MISSING_COLUMNS = "MISSING_COLUMNS"


@dataclass(frozen=True)
class EntrySignalResult:
    signal: int | None
    version: str | None
    reason: str


def compute_entry_signal(daily_bars: Any) -> EntrySignalResult:
    """计算 L3 v1 买点信号；输入必须已归一化为 date/close/volume。"""
    required_columns = {"date", "close", "volume"}
    columns = set(getattr(daily_bars, "columns", []))
    if not required_columns.issubset(columns):
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, REASON_MISSING_COLUMNS)

    bars = daily_bars.sort_values("date") if "date" in columns else daily_bars
    if len(bars) < 120:
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, REASON_INSUFFICIENT_DATA)

    close = bars["close"].astype(float)
    volume = bars["volume"].astype(float)
    latest_close = float(close.iloc[-1])
    ma60 = float(close.tail(60).mean())
    ma120 = float(close.tail(120).mean())
    volume_5d_avg = float(volume.tail(5).mean())
    volume_20d_avg = float(volume.tail(20).mean())

    if latest_close <= ma60:
        return EntrySignalResult(0, ENTRY_SIGNAL_VERSION, REASON_BELOW_MA60)
    if latest_close <= ma120:
        return EntrySignalResult(0, ENTRY_SIGNAL_VERSION, REASON_BELOW_MA120)
    if volume_5d_avg <= volume_20d_avg:
        return EntrySignalResult(0, ENTRY_SIGNAL_VERSION, REASON_LOW_VOLUME)
    return EntrySignalResult(1, ENTRY_SIGNAL_VERSION, REASON_PASS)
