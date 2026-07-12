"""Production wrapper: compute L3 v2 signal from the daily_bars table."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from lib.cache import load_daily_bars
from lib.l3_v2 import (
    V2_VERSION,
    DataContractState,
    DailyBar,
    PricePanel,
    SignalResult,
    compute_l3_v2_candidate,
    empty_metrics,
)


logger = logging.getLogger(__name__)

_UNAVAILABLE_CONTRACT = DataContractState(
    adjusted="none",
    source="daily_bars",
    volume_unit="unknown",
    is_stale=False,
    stale_reason=None,
    unavailable_reason="QFQ_UNAVAILABLE",
    alignment_reason=None,
)


def _select_daily_rows(
    db: sqlite3.Connection,
    code: str,
    today: str,
) -> tuple[list[dict[str, Any]], DataContractState]:
    qfq_rows = load_daily_bars(db, code, today, 120, adjusted="qfq")
    if len(qfq_rows) >= 120:
        return qfq_rows, DataContractState(
            adjusted="qfq",
            source="baostock.qfq",
            volume_unit=qfq_rows[-1]["volume_unit"],
            is_stale=False,
            stale_reason=None,
            unavailable_reason=None,
            alignment_reason=None,
        )

    rows = load_daily_bars(db, code, today, 120, adjusted="none")
    return rows, DataContractState(
        adjusted="none",
        source=rows[-1]["source"] if rows else "daily_bars",
        volume_unit=rows[-1]["volume_unit"] if rows else "unknown",
        is_stale=False,
        stale_reason=None,
        unavailable_reason="QFQ_UNAVAILABLE",
        alignment_reason=None,
    )


def _build_price_panel(
    code: str,
    rows: list[dict[str, Any]],
    contract: DataContractState,
) -> PricePanel:
    bars = tuple(
        DailyBar(
            date=datetime.strptime(row["date"], "%Y-%m-%d").date(),
            open=None,
            high=None,
            low=None,
            close=float(row["close"]),
            volume=float(row["volume"]) if row["volume"] is not None else None,
            source=row["source"],
            adjusted=row["adjusted"],
            volume_unit=row["volume_unit"],
            fetched_at=row["fetched_at"],
            quality_status=row["quality_status"],
        )
        for row in rows
    )
    return PricePanel(
        code=code,
        adjusted=contract.adjusted,
        bars=bars,
        source=contract.source,
        volume_unit=contract.volume_unit,
        preload_start=bars[0].date,
        stale_reason=None,
        limitation=None,
    )


def compute_l3_v2_from_daily_bars(
    db: sqlite3.Connection,
    code: str,
    today: str,
) -> SignalResult:
    """Compute L3 v2 signal from local QFQ bars with unadjusted fallback."""
    try:
        rows, contract = _select_daily_rows(db, code, today)
        if not rows:
            return SignalResult(None, V2_VERSION, "unavailable", "NO_DAILY_BARS", empty_metrics())
        panel = _build_price_panel(code, rows, contract)
        return compute_l3_v2_candidate(panel, contract)
    except Exception as exc:
        logger.warning("L3 v2 computation failed for %s: %s", code, exc)
        return SignalResult(None, V2_VERSION, "unavailable", "L3_V2_ERROR", empty_metrics())


def now_isoformat() -> str:
    return datetime.now(timezone.utc).isoformat()
