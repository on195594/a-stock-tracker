#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROBE_GLOB = "*-tushare-capability-probe.md"


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


def latest_probe_report() -> Path | None:
    reports = sorted((PROJECT_ROOT / "docs" / "reviews").glob(PROBE_GLOB))
    return reports[-1] if reports else None


def probe_passed(report_path: Path) -> bool:
    text = report_path.read_text(encoding="utf-8")
    return "\nPASS\n" in text and "| failed |" not in text and "Close cross-check: PASS" in text


def readiness_status() -> tuple[bool, list[str]]:
    _load_dotenv()
    reasons: list[str] = []
    if not os.environ.get("TUSHARE_TOKEN"):
        reasons.append("TUSHARE_TOKEN is not configured")

    report = latest_probe_report()
    if report is None:
        reasons.append("no Tushare capability probe report found")
    elif not probe_passed(report):
        reasons.append(f"latest Tushare capability probe is not PASS: {report}")

    return not reasons, reasons


def main() -> int:
    ready, reasons = readiness_status()
    if ready:
        print("READY: market data provider can be considered for cron recovery")
        return 0
    print("NOT_READY: market data provider is not cleared for cron recovery")
    for reason in reasons:
        print(f"- {reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
