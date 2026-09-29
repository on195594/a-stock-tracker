"""Calendar evidence needed to freeze research dates; no price/history collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd  # type: ignore[import-untyped]

from a_stock_tracker.paths import CALENDAR_PATH, CONFIG_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)
TRACKED_TRADING_CALENDAR_PATH = CONFIG_DIR / "trading_calendar.json"
RUNTIME_TRADING_CALENDAR_PATH = CALENDAR_PATH
CALENDAR_SOURCE_DIR = CALENDAR_PATH.parent / "sources"


def latest_completed_day(path: Path, *, today: date | None = None) -> str:
    """Select a proved trading date before Shanghai today; never invent a holiday."""
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    proof = json.loads(path.read_text(encoding="utf-8"))
    start, end, as_of = (
        date.fromisoformat(proof[k]) for k in ("covered_from", "covered_to", "as_of")
    )
    yesterday = today - timedelta(days=1)
    if not str(proof.get("source", "")).startswith("tushare.trade_cal:SSE;"):
        raise ValueError("unverified calendar source")
    if start > end or start > as_of or as_of > today or start > yesterday:
        raise ValueError("invalid calendar coverage")
    proved_end = min(end, as_of)
    # A weekend gap requires no guess about exchange holidays; weekdays require evidence.
    if any(
        (proved_end + timedelta(days=i)).weekday() < 5
        for i in range(1, (yesterday - proved_end).days + 1)
    ):
        raise ValueError("calendar coverage is stale")
    days = [date.fromisoformat(day) for day in proof["dates"]]
    if (
        not days
        or len(days) != len(set(days))
        or any(day < start or day > proved_end or day.weekday() >= 5 for day in days)
    ):
        raise ValueError("invalid trading dates")
    return max(day for day in days if day <= yesterday).isoformat()


CALENDAR_SOURCE = "tushare.trade_cal"


CALENDAR_OFFICIAL_REFERENCE = "上证公告〔2025〕45号"


CALENDAR_OFFICIAL_CLOSURES = (
    (date(2026, 1, 1), date(2026, 1, 3), "元旦"),
    (date(2026, 2, 15), date(2026, 2, 23), "春节"),
    (date(2026, 4, 4), date(2026, 4, 6), "清明节"),
    (date(2026, 5, 1), date(2026, 5, 5), "劳动节"),
    (date(2026, 6, 19), date(2026, 6, 21), "端午节"),
    (date(2026, 9, 25), date(2026, 9, 27), "中秋节"),
    (date(2026, 10, 1), date(2026, 10, 7), "国庆节"),
)


SHANGHAI_ZONE = ZoneInfo("Asia/Shanghai")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    rollback = path.with_name(f".{path.name}.{os.getpid()}.rollback")
    previous = path.read_bytes() if path.is_file() else None
    replaced = False
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if replaced:
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    with rollback.open("wb") as handle:
                        handle.write(previous)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(rollback, path)
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"atomic write failed and rollback failed: {path}"
                ) from rollback_error
        raise
    finally:
        temporary.unlink(missing_ok=True)
        rollback.unlink(missing_ok=True)


def _calendar_seed_start(path: Path) -> date:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != {"dates", "covered_from", "covered_to", "as_of", "source"}:
            raise ValueError("invalid shape")
        if (
            not isinstance(payload["dates"], list)
            or not isinstance(payload["source"], str)
            or not payload["source"]
        ):
            raise ValueError("invalid metadata")
        return date.fromisoformat(payload["covered_from"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid tracked calendar seed: {path}") from exc


def _compact_calendar_date(value: Any, field: str) -> date:
    text = str(value)
    if len(text) != 8 or not text.isdigit():
        raise RuntimeError(f"invalid {field}: {value}")
    try:
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    except ValueError as exc:
        raise RuntimeError(f"invalid {field}: {value}") from exc


def refresh_calendar(
    api: Any,
    as_of: date,
    *,
    tracked_path: Path = TRACKED_TRADING_CALENDAR_PATH,
    runtime_path: Path = RUNTIME_TRADING_CALENDAR_PATH,
    source_dir: Path = CALENDAR_SOURCE_DIR,
) -> Path:
    covered_from = _calendar_seed_start(tracked_path)
    if as_of < covered_from:
        raise RuntimeError("calendar as_of precedes tracked coverage start")
    frame = api.trade_cal(
        exchange="SSE",
        start_date=covered_from.strftime("%Y%m%d"),
        end_date=as_of.strftime("%Y%m%d"),
        fields="exchange,cal_date,is_open,pretrade_date",
    )
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise RuntimeError("no SSE trading-calendar rows")
    missing = {"cal_date", "is_open"} - set(frame.columns)
    if missing:
        raise RuntimeError(f"missing SSE trading-calendar columns: {', '.join(sorted(missing))}")

    rows: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        exchange = str(raw.get("exchange") or "")
        if exchange not in {"", "SSE"}:
            raise RuntimeError(f"unexpected trading-calendar exchange: {exchange}")
        calendar_date = _compact_calendar_date(raw.get("cal_date"), "cal_date")
        raw_is_open = raw.get("is_open")
        if isinstance(raw_is_open, bool) or raw_is_open not in (0, 1):
            raise RuntimeError(f"invalid is_open for {calendar_date}: {raw_is_open}")
        raw_pretrade = raw.get("pretrade_date")
        pretrade_date = None
        if raw_pretrade is not None and not pd.isna(raw_pretrade) and str(raw_pretrade):
            pretrade_date = _compact_calendar_date(raw_pretrade, "pretrade_date").isoformat()
        rows.append(
            {
                "exchange": "SSE",
                "cal_date": calendar_date.isoformat(),
                "is_open": int(raw_is_open),
                "pretrade_date": pretrade_date,
            }
        )
    rows.sort(key=lambda item: item["cal_date"])
    observed_dates = [date.fromisoformat(item["cal_date"]) for item in rows]
    if len(observed_dates) != len(set(observed_dates)):
        raise RuntimeError("duplicate SSE trading-calendar dates")
    expected_dates = [
        covered_from + timedelta(days=offset) for offset in range((as_of - covered_from).days + 1)
    ]
    if observed_dates != expected_dates:
        raise RuntimeError(
            "SSE trading calendar does not cover every natural date in the requested range"
        )
    open_dates = [date.fromisoformat(item["cal_date"]) for item in rows if item["is_open"] == 1]
    open_set = set(open_dates)
    weekend_open = [item.isoformat() for item in open_dates if item.weekday() >= 5]
    if weekend_open:
        raise RuntimeError(f"SSE calendar marks weekend open: {','.join(weekend_open)}")
    closure_conflicts: list[str] = []
    for closure_start, closure_end, label in CALENDAR_OFFICIAL_CLOSURES:
        current = max(closure_start, covered_from)
        last = min(closure_end, as_of)
        while current <= last:
            if current in open_set:
                closure_conflicts.append(f"{label}:{current.isoformat()}:marked_open")
            current += timedelta(days=1)
    if closure_conflicts:
        raise RuntimeError(
            f"SSE calendar conflicts with {CALENDAR_OFFICIAL_REFERENCE}: {','.join(closure_conflicts)}"
        )

    official_range_start = max(covered_from, date(2026, 1, 1))
    official_range_end = min(as_of, date(2026, 12, 31))
    official_cross_check = (
        {
            "notice": CALENDAR_OFFICIAL_REFERENCE,
            "covered_from": official_range_start.isoformat(),
            "covered_to": official_range_end.isoformat(),
            "closure_conflicts": [],
        }
        if official_range_start <= official_range_end
        else None
    )
    fetched_at = datetime.now(SHANGHAI_ZONE).isoformat()
    raw_payload = {
        "schema_version": 1,
        "source": CALENDAR_SOURCE,
        "exchange": "SSE",
        "request": {"start_date": covered_from.isoformat(), "end_date": as_of.isoformat()},
        "fetched_at": fetched_at,
        "official_cross_check": official_cross_check,
        "rows": rows,
    }
    raw_bytes = (
        json.dumps(raw_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    raw_path = source_dir / f"{raw_hash}.json"
    if raw_path.exists() and raw_path.read_bytes() != raw_bytes:
        raise RuntimeError(f"calendar source hash collision: {raw_hash}")
    if not raw_path.exists():
        _atomic_write_bytes(raw_path, raw_bytes)
    try:
        raw_reference = raw_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        raw_reference = str(raw_path)
    official_source = (
        f";official_cross_check={CALENDAR_OFFICIAL_REFERENCE};"
        f"official_cross_check_range={official_range_start.isoformat()}..{official_range_end.isoformat()}"
        if official_cross_check is not None
        else ""
    )
    calendar_payload = {
        "dates": [item.isoformat() for item in open_dates],
        "covered_from": covered_from.isoformat(),
        "covered_to": as_of.isoformat(),
        "as_of": as_of.isoformat(),
        "source": (
            f"tushare.trade_cal:SSE;raw_path={raw_reference};raw_sha256={raw_hash};"
            f"fetched_at={fetched_at}{official_source}"
        ),
    }
    calendar_bytes = (json.dumps(calendar_payload, ensure_ascii=False, indent=2) + "\n").encode()
    _atomic_write_bytes(runtime_path, calendar_bytes)
    logger.info(
        "TRADING_CALENDAR_REFRESH_OK as_of=%s open_dates=%d raw_sha256=%s",
        as_of.isoformat(),
        len(open_dates),
        raw_hash,
    )
    return runtime_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh only the research trading calendar")
    parser.add_argument("--as-of", type=date.fromisoformat)
    args = parser.parse_args()
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        parser.error("TUSHARE_TOKEN is required")
    today = datetime.now(SHANGHAI_ZONE).date()
    as_of = args.as_of or today
    if as_of > today:
        parser.error("future calendar coverage is not allowed")
    import tushare as ts  # type: ignore[import-untyped]

    try:
        refresh_calendar(ts.pro_api(token, timeout=30), as_of)
    except Exception as exc:
        # Provider errors may include credentials. Preserve existing evidence; fail loudly.
        print(f"Calendar update failed ({type(exc).__name__}); previous evidence retained")
        return 1
    print(f"Calendar evidence updated through {as_of.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
