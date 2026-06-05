from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ENTRY_SIGNAL_VERSION = "v1"

REASON_PASS = "PASS"
REASON_BELOW_MA60 = "BELOW_MA60"
REASON_BELOW_MA120 = "BELOW_MA120"
REASON_LOW_VOLUME = "LOW_VOLUME"
REASON_INSUFFICIENT_DATA = "INSUFFICIENT_WINDOW"
REASON_MISSING_COLUMNS = "MISSING_COLUMNS"
REASON_MISSING_VOLUME = "MISSING_VOLUME"
REASON_MIXED_SOURCE_VOLUME_UNSAFE = "MIXED_SOURCE_VOLUME_UNSAFE"


@dataclass(frozen=True)
class EntrySignalResult:
    signal: int | None
    version: str | None
    reason: str
    status: str | None = None
    source: str | None = None
    fetched_at: str | None = None


def compute_entry_signal(daily_bars: Any) -> EntrySignalResult:
    """计算 L3 v1 买点信号；输入必须已归一化为 date/close/volume。"""
    required_columns = {"date", "close", "volume"}
    columns = set(getattr(daily_bars, "columns", []))
    if not required_columns.issubset(columns):
        missing = required_columns - columns
        reason = REASON_MISSING_VOLUME if "volume" in missing else REASON_MISSING_COLUMNS
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, reason, "insufficient")

    bars = daily_bars.sort_values("date") if "date" in columns else daily_bars
    if len(bars) < 120:
        return EntrySignalResult(None, ENTRY_SIGNAL_VERSION, REASON_INSUFFICIENT_DATA, "insufficient")

    close = bars["close"].astype(float)
    volume = bars["volume"].astype(float)
    latest_close = float(close.iloc[-1])
    ma60 = float(close.tail(60).mean())
    ma120 = float(close.tail(120).mean())
    volume_5d_avg = float(volume.tail(5).mean())
    volume_20d_avg = float(volume.tail(20).mean())

    if latest_close <= ma60:
        return EntrySignalResult(0, ENTRY_SIGNAL_VERSION, REASON_BELOW_MA60, "reject")
    if latest_close <= ma120:
        return EntrySignalResult(0, ENTRY_SIGNAL_VERSION, REASON_BELOW_MA120, "reject")
    if volume_5d_avg <= volume_20d_avg:
        return EntrySignalResult(0, ENTRY_SIGNAL_VERSION, REASON_LOW_VOLUME, "reject")
    return EntrySignalResult(1, ENTRY_SIGNAL_VERSION, REASON_PASS, "pass")
