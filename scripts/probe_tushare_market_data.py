#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from a_stock_lib.providers.tushare_quotes import (  # noqa: E402
    TUSHARE_VOLUME_UNIT,
    TushareMarketDataProvider,
    to_tushare_index_code,
    to_tushare_stock_code,
)
from a_stock_tracker.data.cache import DB_PATH  # noqa: E402

SAMPLES = ["600036", "000001", "002594"]
INDEX_SAMPLE = "000300"


class ReferenceClose(NamedTuple):
    close: float
    source: str
    trade_date: str


class ProbeCheck(NamedTuple):
    label: str
    kind: str
    blocking: bool
    code: str
    status: str
    source: str
    error_code: str
    error_message: str
    rows: int
    elapsed_ms: float
    latest_trade_date: str
    latest_close: float | None


class ProbeDecision(NamedTuple):
    write_gate_status: str
    capability_status: str
    exit_code: int
    production_decision: str
    dependent_jobs: str
    reason: str


CAPABILITY_KINDS = {"index_daily", "trade_cal"}
DEGRADED_CAPABILITY_ERRORS = {"RATE_LIMITED"}


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


def _timed(
    label: str,
    fn: Callable[[], Any],
    *,
    kind: str,
    blocking: bool,
    code: str | None = None,
) -> ProbeCheck:
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
    return ProbeCheck(
        label=label,
        kind=kind,
        blocking=blocking,
        code=code or "",
        status=result.status,
        source=result.source,
        error_code=result.error_code or "",
        error_message=result.error_message or "",
        rows=ok_rows,
        elapsed_ms=elapsed_ms,
        latest_trade_date=latest_trade_date,
        latest_close=latest_close,
    )


def _item_value(item: dict[str, Any] | ProbeCheck, key: str) -> Any:
    if isinstance(item, dict):
        return item[key]
    return getattr(item, key)


def _row(item: ProbeCheck) -> str:
    return (
        f"| {item.label} | {'yes' if item.blocking else 'no'} | {item.status} | {item.source} | "
        f"{item.rows} | {item.elapsed_ms} | {item.error_code} | {item.error_message[:120]} |"
    )


def _load_reference_close(code: str, trade_date: str) -> ReferenceClose | None:
    db_path = Path(DB_PATH).expanduser()
    if db_path.exists():
        try:
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    """SELECT close, source
                       FROM daily_bars
                       WHERE code=? AND trade_date=? AND adjusted='none' AND source='tushare.daily'
                       ORDER BY fetched_at DESC
                       LIMIT 1""",
                    (code, trade_date),
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            row = None
        if row is not None:
            return ReferenceClose(float(row[0]), str(row[1]), trade_date)
    return None


def _close_cross_checks(checks: list[dict[str, Any] | ProbeCheck]) -> tuple[str, list[str]]:
    rows: list[str] = []
    failures = 0
    checked = 0
    missing_reference = 0
    date_mismatches = 0
    for item in checks:
        code = _item_value(item, "code")
        trade_date = _item_value(item, "latest_trade_date")
        latest_close = _item_value(item, "latest_close")
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


def _classify_capabilities(checks: list[ProbeCheck]) -> str:
    capability_checks = [item for item in checks if item.kind in CAPABILITY_KINDS]
    failed = [item for item in capability_checks if item.status == "failed"]
    if not failed:
        return "PASS"
    if all(item.error_code in DEGRADED_CAPABILITY_ERRORS for item in failed):
        return "DEGRADED"
    return "BLOCKED"


def _decide_probe(token: str | None, checks: list[ProbeCheck], close_status: str) -> ProbeDecision:
    capability_status = _classify_capabilities(checks)
    blocking_failures = [item for item in checks if item.blocking and item.status == "failed"]
    if not token:
        write_gate_status = "FAIL"
        reason = "TUSHARE_TOKEN is not configured"
    elif blocking_failures:
        write_gate_status = "FAIL"
        reason = "blocking daily checks failed"
    elif close_status == "PASS":
        write_gate_status = "PASS"
        reason = "daily close cross-check passed"
    elif close_status == "MANUAL_REQUIRED":
        write_gate_status = "MANUAL_REQUIRED"
        reason = "daily close cross-check requires manual review"
    else:
        write_gate_status = "FAIL"
        reason = "daily close cross-check failed"

    exit_code = 0 if write_gate_status == "PASS" else 1
    production_decision = "DAILY_WRITES_ALLOWED" if write_gate_status == "PASS" else "DAILY_WRITES_BLOCKED"
    dependent_jobs = "ALLOWED" if write_gate_status == "PASS" and capability_status == "PASS" else "HOLD"
    return ProbeDecision(
        write_gate_status=write_gate_status,
        capability_status=capability_status,
        exit_code=exit_code,
        production_decision=production_decision,
        dependent_jobs=dependent_jobs,
        reason=reason,
    )


def _render_report(
    *,
    report_date: str,
    token: str | None,
    start: str,
    end: str,
    checks: list[ProbeCheck],
    close_status: str,
    close_rows: list[str],
    decision: ProbeDecision,
) -> list[str]:
    return [
        "# Tushare Capability Probe",
        "",
        f"Run date: {report_date}",
        f"Token configured: {'yes' if token else 'no'}",
        f"Date range: {start} -> {end}",
        f"Volume unit assumption: `{TUSHARE_VOLUME_UNIT}`",
        "",
        "## Decision",
        "",
        f"Write Gate: {decision.write_gate_status}",
        f"Capability Checks: {decision.capability_status}",
        f"Production Decision: {decision.production_decision}",
        f"Index/Calendar Dependent Jobs: {decision.dependent_jobs}",
        f"Reason: {decision.reason}",
        "",
        "| Check | Blocking | Status | Source | Rows | Latency ms | Error | Message |",
        "|---|---|---|---|---:|---:|---|---|",
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
        "## Write Gate Result",
        "",
        decision.write_gate_status,
        "",
        "Daily production writes require Write Gate PASS. Capability DEGRADED/BLOCKED means index/calendar-dependent jobs remain on hold.",
    ]


def main() -> int:
    _load_dotenv()
    token = os.environ.get("TUSHARE_TOKEN")
    today = date.today()
    start = (today - timedelta(days=365)).isoformat()
    end = today.isoformat()
    provider = TushareMarketDataProvider(token=token)

    checks: list[ProbeCheck] = []
    for code in SAMPLES:
        checks.append(
            _timed(
                f"daily {code} ({to_tushare_stock_code(code)})",
                lambda code=code: provider.fetch_l3_bars(code, end, 120),
                kind="daily",
                blocking=True,
                code=code,
            )
        )
    checks.append(
        _timed(
            f"index_daily {INDEX_SAMPLE} ({to_tushare_index_code(INDEX_SAMPLE)})",
            lambda: provider.fetch_index_bars(INDEX_SAMPLE),
            kind="index_daily",
            blocking=False,
        )
    )
    checks.append(
        _timed(
            "trade_cal SSE",
            lambda: provider.fetch_trade_calendar(start, end),
            kind="trade_cal",
            blocking=False,
        )
    )

    close_status, close_rows = _close_cross_checks(checks)
    decision = _decide_probe(token, checks, close_status)
    report_date = today.isoformat()
    report_path = PROJECT_ROOT / "docs" / "reviews" / f"{report_date}-tushare-capability-probe.md"
    lines = _render_report(
        report_date=report_date,
        token=token,
        start=start,
        end=end,
        checks=checks,
        close_status=close_status,
        close_rows=close_rows,
        decision=decision,
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"probe report: {report_path}")
    if decision.exit_code != 0:
        print("Tushare probe failed; stop before Phase 1 production enablement.", file=sys.stderr)
    return decision.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
