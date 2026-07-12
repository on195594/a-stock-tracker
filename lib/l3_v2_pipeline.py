"""Production wrapper: compute L3 v2 signal from the daily_bars table."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timezone

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


def compute_l3_v2_from_daily_bars(
    db: sqlite3.Connection,
    code: str,
    today: str,
) -> SignalResult:
    """Compute L3 v2 signal from the local daily_bars table (no network requests).

    daily_bars stores unadjusted (adjusted='none') prices, so the result is
    always pass_weak at best.  pass_strong requires QFQ data (Phase 2).
    Any exception is caught and returned as unavailable/L3_V2_ERROR.
    """
    try:
        rows = load_daily_bars(db, code, today, 120)
        if not rows:
            return SignalResult(None, V2_VERSION, "unavailable", "NO_DAILY_BARS", empty_metrics())

        today_date = datetime.strptime(today, "%Y-%m-%d").date()
        first_date = datetime.strptime(rows[0]["date"], "%Y-%m-%d").date()

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

        panel = PricePanel(
            code=code,
            adjusted="none",
            bars=bars,
            source=rows[-1]["source"] if rows else "daily_bars",
            volume_unit=rows[-1]["volume_unit"] if rows else "unknown",
            preload_start=first_date,
            stale_reason=None,
            limitation=None,
        )

        contract = DataContractState(
            adjusted="none",
            source=rows[-1]["source"] if rows else "daily_bars",
            volume_unit=rows[-1]["volume_unit"] if rows else "unknown",
            is_stale=False,
            stale_reason=None,
            unavailable_reason="QFQ_UNAVAILABLE",
            alignment_reason=None,
        )

        return compute_l3_v2_candidate(panel, contract)

    except Exception as exc:
        logger.warning("L3 v2 computation failed for %s: %s", code, exc)
        return SignalResult(None, V2_VERSION, "unavailable", "L3_V2_ERROR", empty_metrics())


def now_isoformat() -> str:
    return datetime.now(timezone.utc).isoformat()
