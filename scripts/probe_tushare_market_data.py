#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_lib.providers.tushare_quotes import (  # noqa: E402
    TUSHARE_VOLUME_UNIT,
    TushareMarketDataProvider,
    to_tushare_index_code,
    to_tushare_stock_code,
)
from lib.cache import DB_PATH  # noqa: E402

SAMPLES = ["600036", "000001", "002594"]
INDEX_SAMPLE = "000300"


class ReferenceClose(NamedTuple):
    close: float
    source: str
    trade_date: str


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _timed(label: str, fn, code: str | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    result = fn()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    ok_rows = 0
    if result.value is not None and hasattr(result.value, "__len__"):
        ok_rows = len(result.value)
    latest_trade_date = ""
    latest_close = None
    if result.value is not None and hasattr(result.value, "empty") and not result.value.empty:
        if {"date", "close"}.issubset(result.value.columns):
            row = result.value.iloc[-1]
            latest_trade_date = str(row["date"])[:10]
            latest_close = float(row["close"])
    return {
        "label": label,
        "code": code or "",
        "status": result.status,
        "source": result.source,
        "error_code": result.error_code or "",
        "error_message": result.error_message or "",
        "rows": ok_rows,
        "elapsed_ms": elapsed_ms,
        "latest_trade_date": latest_trade_date,
        "latest_close": latest_close,
    }


def _row(item: dict[str, Any]) -> str:
    return (
        f"| {item['label']} | {item['status']} | {item['source']} | {item['rows']} | "
        f"{item['elapsed_ms']} | {item['error_code']} | {item['error_message'][:120]} |"
    )


def _load_reference_close(code: str, trade_date: str) -> ReferenceClose | None:
    db_path = Path(DB_PATH).expanduser()
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                """SELECT close, source
                   FROM daily_bars
                   WHERE code=? AND trade_date=? AND source != 'tushare.daily'
                   ORDER BY fetched_at DESC
                   LIMIT 1""",
                (code, trade_date),
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            return ReferenceClose(float(row[0]), str(row[1]), trade_date)

    try:
        from a_stock_lib.providers.baostock_quotes import IsolatedBaoStockMarketDataProvider

        provider = IsolatedBaoStockMarketDataProvider()
        result = provider.fetch_daily_bars_range(code, trade_date, trade_date)
        if result.value is not None and not result.value.empty:
            row = result.value.iloc[-1]
            return ReferenceClose(float(row["close"]), result.source, str(row["date"])[:10])

        result = provider.fetch_score_price(code, trade_date)
    except Exception:
        return None
    if result.value is None or result.status == "failed":
        return None
    reference_date = trade_date
    if result.freshness_days is not None:
        reference_date = (date.fromisoformat(trade_date) - timedelta(days=result.freshness_days)).isoformat()
    return ReferenceClose(float(result.value), result.source, reference_date)


def _close_cross_checks(checks: list[dict[str, Any]]) -> tuple[str, list[str]]:
    rows: list[str] = []
    failures = 0
    checked = 0
    missing_reference = 0
    date_mismatches = 0
    for item in checks:
        code = item["code"]
        trade_date = item["latest_trade_date"]
        latest_close = item["latest_close"]
        if not code or not trade_date or latest_close is None:
            continue
        reference = _load_reference_close(code, trade_date)
        if reference is None:
            missing_reference += 1
            rows.append(f"| {code} | {trade_date} | {latest_close:.4f} |  |  |  |  | MISSING_REFERENCE |")
            continue
        if reference.trade_date != trade_date:
            date_mismatches += 1
            rows.append(
                f"| {code} | {trade_date} | {latest_close:.4f} | {reference.trade_date} | "
                f"{reference.close:.4f} | {reference.source} |  | DATE_MISMATCH |"
            )
            continue
        reference_close, reference_source = reference.close, reference.source
        diff_pct = abs(latest_close / reference_close - 1) * 100 if reference_close else 999.0
        status = "PASS" if diff_pct <= 0.5 else "FAIL"
        checked += 1
        if status == "FAIL":
            failures += 1
        rows.append(
            f"| {code} | {trade_date} | {latest_close:.4f} | {reference.trade_date} | {reference_close:.4f} | "
            f"{reference_source} | {diff_pct:.3f}% | {status} |"
        )
    if failures:
        return "FAIL", rows
    if checked == 0 or missing_reference or date_mismatches:
        return "MANUAL_REQUIRED", rows
    return "PASS", rows


def main() -> int:
    _load_dotenv()
    token = os.environ.get("TUSHARE_TOKEN")
    today = date.today()
    start = (today - timedelta(days=365)).isoformat()
    end = today.isoformat()
    provider = TushareMarketDataProvider(token=token)

    checks: list[dict[str, Any]] = []
    for code in SAMPLES:
        checks.append(
            _timed(
                f"daily {code} ({to_tushare_stock_code(code)})",
                lambda code=code: provider.fetch_l3_bars(code, end, 120),
                code=code,
            )
        )
    checks.append(
        _timed(
            f"index_daily {INDEX_SAMPLE} ({to_tushare_index_code(INDEX_SAMPLE)})",
            lambda: provider.fetch_index_bars(INDEX_SAMPLE),
        )
    )
    checks.append(
        _timed(
            "trade_cal SSE",
            lambda: provider.fetch_trade_calendar(start, end),
        )
    )

    close_status, close_rows = _close_cross_checks(checks)
    passed = all(item["status"] != "failed" for item in checks) and close_status == "PASS"
    report_date = today.isoformat()
    report_path = PROJECT_ROOT / "docs" / "reviews" / f"{report_date}-tushare-capability-probe.md"
    lines = [
        "# Tushare Capability Probe",
        "",
        f"Run date: {report_date}",
        f"Token configured: {'yes' if token else 'no'}",
        f"Date range: {start} -> {end}",
        f"Volume unit assumption: `{TUSHARE_VOLUME_UNIT}`",
        "",
        "| Check | Status | Source | Rows | Latency ms | Error | Message |",
        "|---|---|---|---:|---:|---|---|",
        *[_row(item) for item in checks],
        "",
        "## Close Cross-Check",
        "",
        f"Close cross-check: {close_status}",
        "",
        "| Code | Trade Date | Tushare Close | Reference Trade Date | Reference Close | Reference Source | Diff | Status |",
        "|---|---|---:|---|---:|---|---:|---|",
        *(close_rows or ["|  |  |  |  |  |  |  | MISSING_REFERENCE |"]),
        "",
        "## Result",
        "",
        "PASS" if passed else "FAIL",
        "",
        "Close-price cross-check must be PASS before enabling production writes.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"probe report: {report_path}")
    if not passed:
        print("Tushare probe failed; stop before Phase 1 production enablement.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
